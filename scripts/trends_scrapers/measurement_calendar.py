"""Which day does a dated folder actually measure?

The problem
-----------
A dated snapshot folder is not reliably named for the day it
measures. It was written by a run, and depending on when that run
happened the measurement inside is either the folder's own day or the
day before. Readers have been treating the folder name as the
measurement date, and that one conflation shows up in three places:

  * asking the board for 2026-09-22 returns the day measured on
    2026-09-21, because it reads the folder of that name
  * the accumulator's overnight guard compares a snapshot's
    `target_date` against the folder date to decide whether `latest`
    is stale, and on the current convention those two are a day apart
    BY DESIGN, so the guard fires every single night
  * an item can resolve to an older folder than the one the caller
    meant

The obvious fix, shifting every lookup by a day, is wrong. Measured
across all 266 dated folders on 2026-09-23:

    2026-01-01 .. 2026-05-31   folder = measured + 1   (151 days)
    2026-06-01 .. 2026-07-04   mixed, flips five times
    2026-07-05 .. 2026-09-04   folder = measured        (62 days)
    2026-09-05 .. 2026-09-23   folder = measured + 1    (19 days)

84 folders on one convention and 182 on the other. A uniform shift
would move a third of the archive a day the wrong way, which is worse
than the bug it fixes.

The answer
----------
Every snapshot already states the day it measures. The full file
carries `target_date`; the lean window index carries `target_date`
alongside `archive_date`, having drawn the distinction correctly all
along. So nothing needs renaming or moving: the read path stops
inferring the measurement date and reads it.

This module is that single authority. The `asof` lookup, the
accumulator and anything else resolving a day go through it, so the
three symptoms above have one place to be right.

Cheap: the measured date sits in the first few hundred bytes of the
window index, so a folder costs one ranged read rather than a 62 MB
download, and the whole archive resolves in a few seconds. The result
is cached in S3 and refreshed only for folders it has not seen.

Fail-safe: every failure degrades to the old behaviour, treating the
folder name as the measured date. A calendar that cannot be built
must never be the reason a board goes dark.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BUCKET = 'dashboard-inputs'
PREFIX = 'trends_iq_snapshots/'
CALENDAR_KEY = f'{PREFIX}system/measurement_calendar.json'

# The source whose measured date defines the day. Every other scraper
# in a folder belongs to the same run.
_ANCHOR = 'stream_estimates'

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TARGET_RE = re.compile(rb'"target_date"\s*:\s*"(\d{4}-\d{2}-\d{2})"')

_lock = threading.Lock()
_cache: Optional[dict] = None       # folder -> measured date
_cache_loaded_at: Optional[float] = None
_CACHE_TTL_S = 900


def _s3():
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def _read_measured_date(s3, folder: str) -> Optional[str]:
    """The day this folder measures, read from the folder itself."""
    for key, ranges in (
            (f'{PREFIX}{folder}/{_ANCHOR}_window.json',
             ('bytes=0-3000',)),
            (f'{PREFIX}{folder}/{_ANCHOR}.json',
             ('bytes=0-3000', 'bytes=-400000'))):
        for rng in ranges:
            try:
                body = s3.get_object(Bucket=BUCKET, Key=key,
                                     Range=rng)['Body'].read()
            except Exception:
                continue
            m = _TARGET_RE.search(body)
            if m:
                return m.group(1).decode()
    return None


def _list_folders(s3) -> list[str]:
    out, tok = [], None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=PREFIX, Delimiter='/')
        if tok:
            kw['ContinuationToken'] = tok
        r = s3.list_objects_v2(**kw)
        for p in r.get('CommonPrefixes') or []:
            name = p['Prefix'][len(PREFIX):].strip('/')
            if _DATE_RE.match(name):
                out.append(name)
        tok = r.get('NextContinuationToken')
        if not r.get('IsTruncated'):
            break
    return sorted(out)


def build(refresh_all: bool = False, s3=None) -> dict:
    """Folder to measured date, for every dated folder.

    Incremental: a folder already in the stored calendar is not read
    again, because a published day's measurement date never changes.
    """
    s3 = s3 or _s3()
    known: dict = {}
    if not refresh_all:
        try:
            known = json.loads(s3.get_object(
                Bucket=BUCKET, Key=CALENDAR_KEY)['Body'].read()
            ).get('folders') or {}
        except Exception:
            known = {}
    folders = _list_folders(s3)
    todo = [f for f in folders if f not in known]
    # The newest folder is re-read every time: a same-day re-run can
    # republish it, and an incremental cache that trusted its own
    # first answer would pin the board to a stale day.
    if folders and folders[-1] not in todo:
        todo.append(folders[-1])
    if todo:
        with ThreadPoolExecutor(max_workers=16) as ex:
            for folder, measured in zip(
                    todo, ex.map(lambda f: _read_measured_date(s3, f),
                                 todo)):
                if measured:
                    known[folder] = measured
    out = {f: known[f] for f in folders if f in known}
    try:
        s3.put_object(Bucket=BUCKET, Key=CALENDAR_KEY,
                      Body=json.dumps({'folders': out},
                                      sort_keys=True).encode(),
                      ContentType='application/json')
    except Exception:
        logger.info('measurement calendar: could not persist (non-fatal)')
    return out


def _calendar(force: bool = False) -> dict:
    global _cache, _cache_loaded_at
    import time
    with _lock:
        fresh = (_cache is not None and _cache_loaded_at is not None
                 and (time.time() - _cache_loaded_at) < _CACHE_TTL_S)
        if fresh and not force:
            return _cache
        try:
            _cache = build()
        except Exception:
            logger.info('measurement calendar: build failed, readers '
                        'fall back to the folder name (non-fatal)')
            _cache = _cache or {}
        _cache_loaded_at = time.time()
        return _cache


def measured_date(folder: str) -> Optional[str]:
    """The day `folder` measures, or None when it cannot be read."""
    return (_calendar() or {}).get(folder)


def folder_for(measured: str) -> Optional[str]:
    """The folder holding the measurement taken on `measured`.

    None when no folder measured that day, which is the honest answer
    for a day that has not been measured yet: the newest folder holds
    yesterday, so a request for today has no folder behind it and the
    caller must say so rather than serve yesterday under today's
    label.

    Where two folders claim the same measured day, which a same-day
    re-run can produce, the later folder wins: it is the corrected
    copy.
    """
    if not measured or not _DATE_RE.match(str(measured)):
        return None
    cal = _calendar() or {}
    hits = sorted(f for f, m in cal.items() if m == measured)
    if hits:
        return hits[-1]
    return None


def folder_for_or_self(measured: str) -> str:
    """`folder_for`, degrading to the folder of the same name.

    The fallback is the behaviour every reader had before this module
    existed, so a calendar that cannot be built changes nothing rather
    than breaking a read.
    """
    return folder_for(measured) or measured


def measured_days(limit: Optional[int] = None) -> list[str]:
    """Every day that has a measurement, newest first."""
    days = sorted({m for m in (_calendar() or {}).values() if m},
                  reverse=True)
    return days[:limit] if limit else days


def latest_measured_day() -> Optional[str]:
    days = measured_days(1)
    return days[0] if days else None


def window_folders(end_measured: str, n: int) -> list[tuple[str, str]]:
    """`(measured_day, folder)` for the n measured days ending at
    `end_measured`, newest first, skipping days nothing measured.

    Walking measured days rather than folder names is what stops a
    window silently shifting when the convention changed inside it.
    """
    try:
        end = date.fromisoformat(end_measured)
    except Exception:
        return []
    cal = _calendar() or {}
    by_measured: dict[str, str] = {}
    for folder in sorted(cal):
        m = cal[folder]
        if m:
            by_measured[m] = folder      # later folder wins
    out = []
    for i in range(n):
        d = (end - timedelta(days=i)).isoformat()
        f = by_measured.get(d)
        if f:
            out.append((d, f))
    return out


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    cal = build(refresh_all=True)
    lag = {}
    for folder, m in sorted(cal.items()):
        d = (date.fromisoformat(folder) - date.fromisoformat(m)).days
        lag[d] = lag.get(d, 0) + 1
    print(f'{len(cal)} folder(s) resolved')
    for d, n in sorted(lag.items()):
        print(f'  folder minus measured = {d}: {n} folder(s)')
    print('newest measured day:', latest_measured_day())
