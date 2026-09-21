"""Weeks-on-chart history, read once instead of scanned per request.

Why this exists
---------------
Every streaming row carries a `weeks_in_top10` tally, and the read side
built it by scanning the dated archive: for each platform, the trailing
twelve weeks of dated snapshots, one `get_object` per day. Twelve
platforms times eighty-four days is 1,008 small reads, and on a cold
process that was 23.6s of the streaming section's 42.0s, the single
largest item in it.

None of that work depends on the request. The answer only changes when
the nightly run lands a new dated snapshot, so it belongs in the
nightly run. This module folds those 1,008 reads into one object:

    trends_iq_snapshots/latest/streaming_weeks.json

which the read side loads once per process instead.

Shape
-----
Per platform, the days each title appeared on, as offsets in days back
from `anchor_date`:

    {"anchor_date": "2026-09-21", "cover_days": 91,
     "platforms": {"hulu": {"titles": {"<title_norm>": [0, 1, 4, ...]}}}}

Offsets rather than dates because a title on the chart all quarter
would otherwise carry ninety date strings. `title_norm` is
`trends_iq._title_norm`, the same key the scan built.

The reader does the same arithmetic it always did: take the days it
wants, keep the titles that appeared on them, union those days' ISO
weeks. Reading a day out of the index and reading it off S3 therefore
give the same set, so the tally cannot move.

Coverage
--------
`cover_days` is 91 against a reader that asks for 84, and the extra
week is what makes a missed nightly run harmless. A read on the day
the index was built wants offsets 1 to 84; a read six days later wants
7 to 90, still inside the index. The seventh day without a rebuild
falls off the end, and the reader detects that and goes back to
scanning S3 for the days it is missing rather than quietly tallying
fewer weeks. Coverage is checked per platform and per day, so a
platform absent from the index falls back on its own.

Netflix is deliberately not indexed. Its tally ships with the
published TSV and goes back further than our archive, so the read side
keeps that value and never scans for it.

No clickstream is involved: this is a projection of dated snapshots
the platform scrapers already wrote.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_LATEST_PREFIX = 'trends_iq_snapshots/latest/'
S3_DATED_PREFIX = 'trends_iq_snapshots/{date}/'

INDEX_BASENAME = 'streaming_weeks'

# Bump when the shape below changes. An older index then reads as stale
# and every platform falls back to the scan until the rebuild catches
# up, so a shape change can never be served as if it were the new one.
INDEX_VERSION = 1

# Days back from the anchor that the index covers, against a reader
# that asks for 84. See "Coverage" above.
COVER_DAYS = 91

# What the reader asks for, mirroring
# `trends_iq._STREAMING_HISTORY_WEEKS`. Recorded on the index so a
# reader wanting a longer history can tell that this one is too short.
HISTORY_WEEKS = 12

# Its tally is published with the chart, so it is never scanned.
SKIP_SLUGS = frozenset({'netflix'})


def index_key() -> str:
    return f'{S3_LATEST_PREFIX}{INDEX_BASENAME}.json'


def _s3_client():
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region,
                        config=Config(max_pool_connections=64))


def _title_norm(t: str) -> str:
    """Byte-identical to `trends_iq._title_norm`. Duplicated rather
    than imported so building the index never pulls in the Flask app."""
    t = (t or '').strip().lower()
    return re.sub(r'[^a-z0-9]+', '', t)


def iso_week_key(d: date) -> tuple:
    y, w, _ = d.isocalendar()
    return (y, w)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(slugs, anchor: Optional[str] = None, *,
          cover_days: int = COVER_DAYS,
          workers: int = 32,
          s3: Any = None) -> dict:
    """Read every dated snapshot in the window and record which days
    each title appeared on.

    A day whose snapshot is missing or unreadable contributes nothing,
    which is what the scan did with the same day.
    """
    s3 = s3 or _s3_client()
    try:
        ref = date.fromisoformat(anchor) if anchor \
            else datetime.now(timezone.utc).date()
    except Exception:
        ref = datetime.now(timezone.utc).date()

    wanted = [s for s in slugs if s not in SKIP_SLUGS]
    jobs = [(slug, off) for slug in wanted for off in range(cover_days)]

    def _one(job):
        slug, off = job
        day = (ref - timedelta(days=off)).isoformat()
        key = f'{S3_DATED_PREFIX.format(date=day)}{slug}.json'
        try:
            resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
            data = json.loads(resp['Body'].read().decode('utf-8'))
            return slug, off, (data.get('national') or [])
        except Exception:
            return slug, off, []

    platforms: dict[str, dict[str, list]] = {
        slug: {} for slug in wanted}
    days_present: dict[str, int] = {slug: 0 for slug in wanted}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for slug, off, items in ex.map(_one, jobs):
            if items:
                days_present[slug] += 1
            titles = platforms[slug]
            for it in items:
                tn = _title_norm((it or {}).get('title') or '')
                if not tn:
                    continue
                titles.setdefault(tn, []).append(off)

    out_platforms: dict[str, dict] = {}
    for slug in wanted:
        titles = {tn: sorted(set(offs))
                  for tn, offs in platforms[slug].items()}
        out_platforms[slug] = {
            'days_with_rows': days_present[slug],
            'title_count': len(titles),
            'titles': titles,
        }

    return {
        'index_version': INDEX_VERSION,
        'index_of': 'streaming platform dated snapshots',
        'anchor_date': ref.isoformat(),
        'cover_days': int(cover_days),
        'history_weeks': HISTORY_WEEKS,
        'skipped': sorted(SKIP_SLUGS),
        'built_at': datetime.now(timezone.utc).isoformat(),
        'platforms': out_platforms,
    }


def write(payload: dict, *, s3: Any = None) -> Optional[str]:
    """Publish the index. Never raises: a failed write only costs the
    slower scan until the next pass."""
    try:
        s3 = s3 or _s3_client()
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        key = index_key()
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body,
                      ContentType='application/json',
                      CacheControl='public, max-age=60')
        logger.info('streaming weeks index: wrote s3://%s/%s '
                    '(%.2f MB, %d platform(s))',
                    S3_BUCKET, key, len(body) / 1e6,
                    len(payload.get('platforms') or {}))
        return key
    except Exception:
        logger.exception('streaming weeks index: write skipped '
                         '(non-fatal)')
        return None


def rebuild(slugs, *, anchor: Optional[str] = None,
            workers: int = 32, s3: Any = None) -> dict:
    """Build and publish in one step. Returns the summary the nightly
    run logs."""
    s3 = s3 or _s3_client()
    payload = build(slugs, anchor=anchor, workers=workers, s3=s3)
    key = write(payload, s3=s3)
    plats = payload.get('platforms') or {}
    return {
        'key': key,
        'anchor_date': payload.get('anchor_date'),
        'cover_days': payload.get('cover_days'),
        'platforms': len(plats),
        'titles': sum(p.get('title_count', 0) for p in plats.values()),
        'days_with_rows': {s: p.get('days_with_rows', 0)
                           for s, p in sorted(plats.items())},
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read(s3: Any = None) -> Optional[dict]:
    """Load the published index. Returns None when it is absent,
    unreadable, or written by a different version of this module."""
    try:
        s3 = s3 or _s3_client()
        resp = s3.get_object(Bucket=S3_BUCKET, Key=index_key())
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get('index_version') != INDEX_VERSION:
        logger.info('streaming weeks index: version %r is not %r; '
                    'falling back to the scan',
                    data.get('index_version'), INDEX_VERSION)
        return None
    if not isinstance(data.get('platforms'), dict):
        return None
    return data


def uncovered_days(index: Optional[dict], slug: str, dates) -> Optional[list]:
    """Which of `dates` the index cannot answer for `slug`.

    An empty list means the index covers all of them. `None` means the
    index cannot serve this platform at all and the caller should scan
    every day itself.
    """
    if not isinstance(index, dict):
        return None
    plat = (index.get('platforms') or {}).get(slug)
    if not isinstance(plat, dict) or not isinstance(plat.get('titles'), dict):
        return None
    try:
        anchor = date.fromisoformat(index['anchor_date'])
        cover = int(index['cover_days'])
    except Exception:
        return None
    missing = []
    for d in dates:
        off = (anchor - d).days
        if off < 0 or off >= cover:
            missing.append(d)
    return missing


def weeks_for(index: Optional[dict], slug: str, dates) -> Optional[dict]:
    """`{title_norm: {(iso_year, iso_week), ...}}` over `dates`.

    Returns None when the index cannot cover the request, so the caller
    can fall back rather than tally a short answer. Only days the index
    actually covers are consulted, so the result is the same set the
    per-day scan produced.
    """
    missing = uncovered_days(index, slug, dates)
    if missing is None or missing:
        return None
    anchor = date.fromisoformat(index['anchor_date'])
    by_offset = {(anchor - d).days: iso_week_key(d) for d in dates}
    titles = (index['platforms'][slug].get('titles') or {})
    out: dict[str, set] = {}
    for tn, offsets in titles.items():
        weeks = {by_offset[o] for o in offsets if o in by_offset}
        if weeks:
            out[tn] = weeks
    return out


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    ap = argparse.ArgumentParser(
        description=('Build the weeks-on-chart history index the '
                     'streaming section reads.'))
    ap.add_argument('--anchor', default=None,
                    help='Newest day to cover (YYYY-MM-DD, default today).')
    ap.add_argument('--workers', type=int, default=32)
    ap.add_argument('--dry-run', action='store_true',
                    help='Build and report, write nothing.')
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    import trends_iq as T  # noqa: E402
    slugs = [s for s, _, _ in T.STREAMING_PLATFORMS]

    s3 = _s3_client()
    if args.dry_run:
        payload = build(slugs, anchor=args.anchor,
                        workers=args.workers, s3=s3)
        plats = payload.get('platforms') or {}
        print(json.dumps({
            'anchor_date': payload['anchor_date'],
            'cover_days': payload['cover_days'],
            'platforms': len(plats),
            'titles': sum(p['title_count'] for p in plats.values()),
            'days_with_rows': {s: p['days_with_rows']
                               for s, p in sorted(plats.items())},
        }, indent=2))
        return 0

    print(json.dumps(rebuild(slugs, anchor=args.anchor,
                             workers=args.workers, s3=s3), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
