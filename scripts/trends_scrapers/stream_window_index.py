"""Lean per-day read index for the streaming window sum.

Why this exists
---------------
`trends_iq._accumulate_stream_estimates_over_window` sums each item's
daily US audience across the days of the chosen window, and does the
same over the immediately preceding equal-length window so the chip can
compare like with like. A 7 day view therefore reads 14 dated days and a
30 day view reads 60.

The dated `stream_estimates.json` it reads is large and getting larger:
48.6 MB across 20,455 items on 2026-09-17, about 4.3s to download and
0.7s to parse on the build host, and slower on the app host. Fourteen of
those is already past the section compute budget, sixty is far past it,
and when a read misses the budget the window silently sums only the days
that landed. So the size was costing both latency and accuracy.

Over half of those bytes are fields the window sum never opens: the
per-item `method` and `day_specificity` reasoning prose, `sources`,
poster `image`, `url`, `chart_labels`. The sum reads exactly three
things per day:

    target_date
    items[key].us_estimate
    items[key].by_platform[slug].us_estimate

This module writes those three, and only those three, to a sibling key
under the same dated prefix:

    trends_iq_snapshots/{YYYY-MM-DD}/stream_estimates_window.json

which measures about 2.5 MB, roughly twenty times smaller.

Shape
-----
Entries are written in the SAME shape they hold in the full snapshot
(`{'us_estimate': N, 'by_platform': {slug: {'us_estimate': N}}}`) rather
than a packed form. The reader can then hand an index day straight to
the accumulation loop with no translation step, which is what makes a
lean day and a full day arithmetically indistinguishable.

`index_version` rides on every index. A future field the window sum
starts reading is added to `ITEM_FIELDS` / `PLATFORM_FIELDS` and the
version is bumped, at which point every older index reads as stale and
the reader falls back to the full snapshot for that day until the
rebuild catches up. A new field can therefore never go silently missing.

Staleness
---------
Many scripts rewrite a dated `stream_estimates.json` after the fact
(backfills, the distinctness sweeps, the derived-rails sweep, one-off
repairs). Any one of them could leave an index describing numbers that
no longer exist, and a stale index would move rendered values, which is
the one thing this must never do.

So the index records the full snapshot's ETag and byte count, and the
reader re-checks them with a HEAD before trusting the index (about
0.13s against a 4.3s download). An index that does not match the object
it describes is ignored and that day falls back to the full read, per
day rather than all or nothing. `reconcile()` then rebuilds whatever
drifted on the next nightly pass.

No clickstream is involved anywhere in this path: the index is a
projection of a snapshot the scraper already wrote.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, date, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_DATED_PREFIX = 'trends_iq_snapshots/{date}/'

# Basename of the index object, and of the snapshot it projects.
INDEX_BASENAME = 'stream_estimates_window'
SOURCE_BASENAME = 'stream_estimates'

# Bump whenever the field set below changes. Older indexes then read as
# stale and fall back to the full snapshot until rebuilt.
INDEX_VERSION = 1

# The complete set of fields the window sum reads. Anything not listed
# here is deliberately absent from the index.
ITEM_FIELDS = ('us_estimate',)
PLATFORM_FIELDS = ('us_estimate',)

# The accumulator's own cap on how many dated days one request may sum.
# Matches `trends_iq._accumulate_stream_estimates_over_window`.
DEFAULT_BACKFILL_DAYS = 62


def index_key(date_iso: str) -> str:
    """S3 key of the lean index for one archive day."""
    return f'{S3_DATED_PREFIX.format(date=date_iso)}{INDEX_BASENAME}.json'


def source_key(date_iso: str) -> str:
    """S3 key of the full snapshot the index projects."""
    return f'{S3_DATED_PREFIX.format(date=date_iso)}{SOURCE_BASENAME}.json'


def _s3_client():
    import boto3  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region)


def build_index(snapshot: dict,
                date_iso: str,
                *,
                source_etag: Optional[str] = None,
                source_bytes: Optional[int] = None) -> dict:
    """Project a full stream_estimates snapshot down to the window
    sum's field set.

    Items with no usable `us_estimate` are dropped: the accumulator
    skips them anyway (`isinstance(v, (int, float)) and v > 0`), so
    carrying them would only add bytes. A `by_platform` block is kept
    only for the slugs that carry a usable number, for the same reason.
    """
    items = (snapshot or {}).get('items') or {}
    lean: dict[str, dict] = {}
    for key, entry in items.items():
        if not isinstance(entry, dict):
            continue
        val = entry.get('us_estimate')
        if not isinstance(val, (int, float)) or val <= 0:
            continue
        out: dict[str, Any] = {'us_estimate': val}
        by_platform = entry.get('by_platform')
        if isinstance(by_platform, dict):
            plats: dict[str, dict] = {}
            for slug, per in by_platform.items():
                if not isinstance(per, dict):
                    continue
                pv = per.get('us_estimate')
                if isinstance(pv, (int, float)) and pv > 0:
                    plats[slug] = {'us_estimate': pv}
            if plats:
                out['by_platform'] = plats
        lean[key] = out

    return {
        'index_version': INDEX_VERSION,
        'index_of': SOURCE_BASENAME,
        'archive_date': date_iso,
        # Verbatim from the snapshot. The accumulator reports this as
        # the measured day, which runs a day behind the archive day.
        'target_date': (snapshot or {}).get('target_date'),
        'item_fields': list(ITEM_FIELDS),
        'platform_fields': list(PLATFORM_FIELDS),
        'source_key': source_key(date_iso),
        'source_etag': (source_etag or '').strip('"') or None,
        'source_bytes': source_bytes,
        'item_count': len(lean),
        'built_at': datetime.now(timezone.utc).isoformat(),
        'items': lean,
    }


def write_index(date_iso: str,
                snapshot: dict,
                *,
                source_etag: Optional[str] = None,
                source_bytes: Optional[int] = None,
                s3: Any = None) -> Optional[str]:
    """Write the lean index for `date_iso` beside its full snapshot.

    Never raises: the index is an optimisation, and a failed write only
    costs the slower full read on that day.
    """
    try:
        s3 = s3 or _s3_client()
        if source_etag is None or source_bytes is None:
            head = s3.head_object(Bucket=S3_BUCKET, Key=source_key(date_iso))
            source_etag = source_etag or head.get('ETag')
            source_bytes = source_bytes if source_bytes is not None \
                else head.get('ContentLength')
        payload = build_index(snapshot, date_iso,
                              source_etag=source_etag,
                              source_bytes=source_bytes)
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        key = index_key(date_iso)
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body,
                      ContentType='application/json')
        logger.info('stream window index: wrote s3://%s/%s '
                    '(%.2f MB, %d items)',
                    S3_BUCKET, key, len(body) / 1e6, payload['item_count'])
        return key
    except Exception:
        logger.exception('stream window index: write for %s failed '
                         '(non-fatal)', date_iso)
        return None


def index_is_current(index: Optional[dict], head: Optional[dict]) -> bool:
    """True when `index` describes exactly the object `head` reports.

    Version, ETag and byte count all have to line up. This is the whole
    safety story: a dated snapshot rewritten by any of the repair or
    backfill scripts changes its ETag, the index stops matching, and the
    reader goes back to the full snapshot for that day.
    """
    if not isinstance(index, dict) or not isinstance(head, dict):
        return False
    if index.get('index_version') != INDEX_VERSION:
        return False
    want = (index.get('source_etag') or '').strip('"')
    got = (head.get('ETag') or '').strip('"')
    if not want or want != got:
        return False
    size = index.get('source_bytes')
    if size is not None and head.get('ContentLength') is not None \
            and int(size) != int(head['ContentLength']):
        return False
    return isinstance(index.get('items'), dict)


def archive_days(days: int, end: Optional[str] = None) -> list[str]:
    """The trailing `days` archive dates ending at `end` (default
    today UTC), newest first."""
    try:
        ref = date.fromisoformat(end) if end \
            else datetime.now(timezone.utc).date()
    except Exception:
        ref = datetime.now(timezone.utc).date()
    return [(ref - timedelta(days=i)).isoformat() for i in range(max(1, days))]


def reconcile(days: int = DEFAULT_BACKFILL_DAYS,
              end: Optional[str] = None,
              *,
              dry_run: bool = False,
              max_rebuilds: Optional[int] = None,
              s3: Any = None) -> dict:
    """Make sure every archive day in the trailing window carries a
    current index, rebuilding the ones that are missing or stale.

    Derives each index from that day's existing dated snapshot. Nothing
    is re-priced and no snapshot is modified: this only ever reads the
    snapshot and writes the sibling index.

    Cheap in the healthy case. A day whose index already matches costs
    two HEADs and a small GET; only a day that actually drifted pays for
    the full download. Safe to run on every nightly pass.
    """
    s3 = s3 or _s3_client()
    result = {'checked': 0, 'current': 0, 'rebuilt': 0,
              'missing_source': 0, 'failed': 0, 'days_rebuilt': []}

    for day in archive_days(days, end):
        result['checked'] += 1
        try:
            head = s3.head_object(Bucket=S3_BUCKET, Key=source_key(day))
        except Exception:
            # No snapshot for that day, so nothing to index.
            result['missing_source'] += 1
            continue

        existing = None
        try:
            resp = s3.get_object(Bucket=S3_BUCKET, Key=index_key(day))
            existing = json.loads(resp['Body'].read().decode('utf-8'))
        except Exception:
            existing = None

        if index_is_current(existing, head):
            result['current'] += 1
            continue

        if max_rebuilds is not None and result['rebuilt'] >= max_rebuilds:
            logger.info('stream window index: rebuild cap %d reached; '
                        '%s and older left for the next pass',
                        max_rebuilds, day)
            break

        if dry_run:
            result['rebuilt'] += 1
            result['days_rebuilt'].append(day)
            logger.info('stream window index: %s would be rebuilt', day)
            continue

        try:
            obj = s3.get_object(Bucket=S3_BUCKET, Key=source_key(day))
            snap = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            logger.exception('stream window index: could not read the '
                             'snapshot for %s', day)
            result['failed'] += 1
            continue

        written = write_index(day, snap,
                              source_etag=head.get('ETag'),
                              source_bytes=head.get('ContentLength'),
                              s3=s3)
        if written:
            result['rebuilt'] += 1
            result['days_rebuilt'].append(day)
        else:
            result['failed'] += 1

    logger.info('stream window index reconcile: %d day(s) checked, '
                '%d already current, %d rebuilt, %d with no snapshot, '
                '%d failed',
                result['checked'], result['current'], result['rebuilt'],
                result['missing_source'], result['failed'])
    return result


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    ap = argparse.ArgumentParser(
        description=('Build or repair the lean per-day read index the '
                     'streaming window sum uses.'))
    ap.add_argument('--days', type=int, default=DEFAULT_BACKFILL_DAYS,
                    help=(f'How many trailing archive days to cover '
                          f'(default {DEFAULT_BACKFILL_DAYS}, the '
                          f'accumulator cap).'))
    ap.add_argument('--end', default=None,
                    help='Newest archive day to cover (YYYY-MM-DD).')
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would be rebuilt and write nothing.')
    ap.add_argument('--max-rebuilds', type=int, default=None,
                    help='Stop after this many rebuilds in one pass.')
    args = ap.parse_args()

    out = reconcile(days=args.days, end=args.end, dry_run=args.dry_run,
                    max_rebuilds=args.max_rebuilds)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
