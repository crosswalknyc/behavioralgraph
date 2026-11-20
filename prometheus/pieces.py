"""Content pieces by date (Jenna 2026-11-20, the Leatherman x Matchbox
case).

Two valuations on the same pairing counted different press. The first
(September 29 to November 13) counted five pieces; the second (October 9
to November 19) counted two, and left out two pieces dated October 9 and
October 11 that sit inside both windows. The research pass simply did
not find them the second time. A piece dated inside the window is a
fact of the campaign, not of the run that happened to find it.

So every dated press piece a run researches on a pairing is remembered,
and a run's piece set is the union of what it found and every remembered
piece dated inside its window:

- `remember(pairing, pieces, store=None)`: keep the dated pieces of a
  footprint under the pairing (either order of the two names).
- `pieces_for_window(pairing, window, store=None)`: the remembered pieces
  dated inside the window.
- `merge_footprint(fp, pairing, window, store=None)`: the footprint with
  the remembered in-window pieces added, deduplicated on the url, then on
  outlet plus title. The piece count follows.

The registry lives at `system/pm_pieces/<pair_key>.json` and is internal
(it names outlets and urls). Nothing here is read by a client. Fail-safe
in every direction: no store, no match or any error leaves the footprint
as the run found it. Kill switch `PM_PIECES_REGISTRY=0`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from typing import Optional

PREFIX = 'system/pm_pieces/'
ENV = 'PM_PIECES_REGISTRY'
MAX_PIECES = 400


def enabled() -> bool:
    return os.environ.get(ENV, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def _fold(s) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


# words that describe a title rather than name it ('Matchbox the Film on
# Apple TV' is the title 'Matchbox'); shared with prometheus.consistency
_DESCRIPTOR_WORDS = frozenset((
    'the', 'a', 'an', 'of', 'on', 'in', 'at', 'and', 'film', 'movie', 'series', 'show', 'tv', 'apple',
    'appletv', 'netflix', 'hulu', 'peacock', 'prime', 'video', 'amazon', 'max', 'hbo', 'disney', 'paramount',
    'season', 'original', 'documentary', 'doc', 'limited', 'streaming', 'theatrical', 'feature',
))


def property_fold(name) -> str:
    """The title inside a descriptive name: 'Matchbox the Film on Apple
    TV' and 'Matchbox (The AppleTV movie)' both fold to 'matchbox'. A
    name made only of descriptor words keeps its plain fold."""
    words = re.findall(r'[a-z0-9]+', str(name or '').lower())
    kept = [w for w in words if w not in _DESCRIPTOR_WORDS]
    return ''.join(kept) if kept else ''.join(words)


def pair_key(a, b) -> str:
    """One key for a pairing in either order: the two folded names,
    sorted, joined by '__'."""
    fa, fb = property_fold(a), property_fold(b)
    if not fa or not fb:
        return ''
    return '__'.join(sorted((fa, fb)))[:120]


def doc_key(pk: str) -> str:
    return f"{PREFIX}{pk}.json"


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def load(self, key):
        d = self.rows.get(key)
        return json.loads(json.dumps(d)) if isinstance(d, dict) else None

    def save(self, key, doc):
        self.rows[key] = json.loads(json.dumps(doc, default=str))
        return True


class S3Store:
    def __init__(self, s3, bucket):
        self.s3, self.bucket = s3, bucket

    def load(self, key):
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
            doc = json.loads(obj['Body'].read().decode('utf-8'))
            return doc if isinstance(doc, dict) else None
        except Exception:
            return None

    def save(self, key, doc):
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key,
                               Body=json.dumps(doc, indent=1, default=str).encode('utf-8'),
                               ContentType='application/json')
            return True
        except Exception as e:
            print(f"[pm-pieces] save failed {key}: {e}")
            return False


STORE_FOR_TESTS = None


def default_store():
    """The S3 store off the bound host; None in a hermetic run or when
    no host is bound."""
    if STORE_FOR_TESTS is not None:
        return STORE_FOR_TESTS
    if os.environ.get('REGRESSION_TEST_MODE'):
        return None
    try:
        from .host import host as _host
        if _host.bound and _host.has('s3_client') and _host.has('bucket'):
            return S3Store(_host.s3_client, _host.bucket)
    except Exception:
        pass
    return None


def _date(s) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(s or '')[:10])
    except ValueError:
        return None


def piece_id(p: dict) -> str:
    """The identity of a piece: its url without scheme, query or trailing
    slash; else outlet plus title folded."""
    url = str((p or {}).get('url') or '').strip().lower()
    if url:
        url = re.sub(r'^https?://(www\.)?', '', url)
        url = url.split('?')[0].split('#')[0].rstrip('/')
        if url:
            return 'u:' + url[:200]
    return 'o:' + _fold(p.get('outlet')) + ':' + _fold(p.get('title'))[:80]


def _clean(p: dict) -> Optional[dict]:
    if not isinstance(p, dict):
        return None
    d = _date(p.get('date'))
    if not d:
        return None
    out = {k: p.get(k) for k in ('outlet', 'title', 'url', 'date', 'country', 'outlet_us_monthly_uniques',
                                 'article_reads_us', 'leads_with_talent') if p.get(k) not in (None, '')}
    out['date'] = d.isoformat()
    if not (out.get('outlet') or out.get('url')):
        return None
    return out


def remember(pairing, pieces, store=None, *, run_key: str = '') -> int:
    """Keep the dated pieces under the pairing. Returns how many were
    new. Never raises."""
    if not enabled():
        return 0
    try:
        store = store if store is not None else default_store()
        if store is None or not isinstance(pairing, (tuple, list)) or len(pairing) != 2:
            return 0
        pk = pair_key(*pairing)
        if not pk:
            return 0
        doc = store.load(doc_key(pk)) or {}
        rows = {piece_id(r): r for r in (doc.get('pieces') or []) if isinstance(r, dict)}
        new = 0
        for p in (pieces or []):
            c = _clean(p)
            if not c:
                continue
            pid = piece_id(c)
            if pid in rows:
                # a figure read later fills a blank; nothing already read is overwritten
                for k, v in c.items():
                    if rows[pid].get(k) in (None, '') and v not in (None, ''):
                        rows[pid][k] = v
                continue
            c['first_seen'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            if run_key:
                c['run_key'] = str(run_key)
            rows[pid] = c
            new += 1
        if not new and doc:
            return 0
        kept = sorted(rows.values(), key=lambda r: (str(r.get('date') or ''), str(r.get('outlet') or '')))[-MAX_PIECES:]
        store.save(doc_key(pk), {'pair_key': pk, 'names': [str(pairing[0]), str(pairing[1])],
                                 'pieces': kept, 'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        if new:
            print(f"[pm-pieces] {new} new dated piece(s) remembered for {pk}")
        return new
    except Exception as e:
        print(f"[pm-pieces] remember skipped: {type(e).__name__}: {e}")
        return 0


def pieces_for_window(pairing, window, store=None) -> list:
    """The remembered pieces dated inside the window, oldest first."""
    if not enabled():
        return []
    try:
        store = store if store is not None else default_store()
        if store is None or not isinstance(pairing, (tuple, list)) or len(pairing) != 2:
            return []
        pk = pair_key(*pairing)
        if not pk:
            return []
        w0, w1 = _date((window or {}).get('start')), _date((window or {}).get('end'))
        if not (w0 and w1):
            return []
        doc = store.load(doc_key(pk)) or {}
        out = []
        for r in (doc.get('pieces') or []):
            d = _date((r or {}).get('date'))
            if d and w0 <= d <= w1:
                out.append(dict(r))
        out.sort(key=lambda r: (str(r.get('date') or ''), str(r.get('outlet') or '')))
        return out
    except Exception as e:
        print(f"[pm-pieces] window lookup skipped: {type(e).__name__}: {e}")
        return []


def merge_footprint(fp: dict, pairing, window, store=None, *, run_key: str = '') -> tuple:
    """(footprint, note). The footprint with every remembered in-window
    piece added and this run's dated pieces remembered. `note` is
    {'found', 'added', 'pieces'} for the run log; {} when nothing
    changed. The footprint is returned as given on any trouble."""
    if not enabled() or not isinstance(fp, dict):
        return fp, {}
    try:
        found = [p for p in (fp.get('press') or []) if isinstance(p, dict)]
        remember(pairing, found, store, run_key=run_key)
        remembered = pieces_for_window(pairing, window, store)
        if not remembered:
            return fp, {}
        have = {piece_id(p) for p in found}
        w0, w1 = _date((window or {}).get('start')), _date((window or {}).get('end'))
        added = []
        for r in remembered:
            pid = piece_id(r)
            if pid in have:
                continue
            d = _date(r.get('date'))
            if not (d and w0 and w1 and w0 <= d <= w1):
                continue
            have.add(pid)
            added.append({k: v for k, v in r.items() if k not in ('first_seen', 'run_key')})
        if not added:
            return fp, {}
        out = dict(fp)
        out['press'] = (found + added)[:80]
        # the total counts syndication the research saw beyond the listed
        # pieces; the listed set grew, so the total grows by the same count
        total = int(fp.get('press_pieces_total') or 0)
        out['press_pieces_total'] = max(len(out['press']), total + len(added))
        note = {'found': len(found), 'added': len(added), 'pieces': len(out['press']),
                'added_dates': [a.get('date') for a in added]}
        print(f"[pm-pieces] {len(added)} remembered piece(s) dated inside the window added "
              f"({note['added_dates']}); {note['pieces']} pieces counted")
        return out, note
    except Exception as e:
        print(f"[pm-pieces] merge skipped: {type(e).__name__}: {e}")
        return fp, {}


__all__ = ['enabled', 'property_fold', 'pair_key', 'piece_id', 'remember', 'pieces_for_window',
           'merge_footprint', 'MemoryStore', 'S3Store', 'default_store', 'PREFIX', 'ENV']
