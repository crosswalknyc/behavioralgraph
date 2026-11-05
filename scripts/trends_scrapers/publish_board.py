"""Publish the Trends IQ board in step with the data drop (Jenna 2026-11-04).

The scrapers write `trends_iq_snapshots/latest/` as they run. The dashboard
used to read that prefix, so the board turned over whenever the nightly run
happened to finish (3 to 5 AM Pacific), hours after the clickstream day had
landed for clients at 12:01 AM Pacific. Jenna: build it all into one run, so
the Trends boards and the ranker tabs are updated when the data drops.

Now the dashboard reads `trends_iq_snapshots/published/`, a copy of `latest/`
that this module makes:

  * the FLIP, at 12:01 AM Pacific: every object under `latest/` is copied to
    `published/` (server-side, bodies and ETags unchanged), and the hold is
    cleared. The nightly run starts at 5:15 PM Pacific (after the UTC day has
    closed, so every board measures the day that just ended) and finishes
    well before midnight; its new boards sit under `latest/` until the flip.
  * the HOURLY copy, at :40: the same copy, taken only while there is no hold,
    so the re-levels the hourly chart watch applies to the current board
    (chart_moved_watch at :25) still reach the dashboard within the hour.
    The hold is written by run_all when a nightly run starts
    (`system/publish_hold.json`) and lifted by the flip, so a day's new
    boards never leak before 12:01 AM.

The copy is idempotent (an object whose ETag already matches is skipped) and
never raises: a publish that cannot run leaves `published/` as it was, which
is the previous board, never a dark one. `trends_iq._read_snapshot` falls back
to `latest/` for any file `published/` does not hold (sources refreshed ad
hoc, a first run), so nothing goes missing in the switch.

    python3 -m scripts.trends_scrapers.publish_board flip            # 12:01 AM Pacific
    python3 -m scripts.trends_scrapers.publish_board hourly          # :40 every hour; no-op under a hold
    python3 -m scripts.trends_scrapers.publish_board hold --measured 2026-11-03   # run_all calls this at start
    python3 -m scripts.trends_scrapers.publish_board status
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)

BUCKET = os.environ.get('TRENDS_S3_BUCKET', 'dashboard-inputs')
LATEST = 'trends_iq_snapshots/latest/'
PUBLISHED = 'trends_iq_snapshots/published/'
HOLD_KEY = 'trends_iq_snapshots/system/publish_hold.json'
STAMP_KEY = 'trends_iq_snapshots/published/_published_at.json'
HOLD_MAX_AGE_S = 30 * 3600          # a hold older than this is a run that never flipped; the hourly copy resumes


def _s3():
    return boto3.client('s3')


def _list(s3, prefix: str) -> dict:
    out = {}
    token = None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=prefix)
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get('Contents', []) or []:
            out[o['Key'][len(prefix):]] = (o.get('ETag') or '').strip('"')
        if not r.get('IsTruncated'):
            break
        token = r.get('NextContinuationToken')
    return out


def read_hold(s3=None) -> dict | None:
    s3 = s3 or _s3()
    try:
        body = s3.get_object(Bucket=BUCKET, Key=HOLD_KEY)['Body'].read()
        hold = json.loads(body)
    except Exception:
        return None
    try:
        started = datetime.fromisoformat(hold.get('started', '').replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - started).total_seconds() > HOLD_MAX_AGE_S:
            return None
    except Exception:
        return None
    return hold


def hold(measured: str | None = None, s3=None) -> None:
    """Written by run_all when a nightly run starts: the hourly copy pauses until the flip."""
    s3 = s3 or _s3()
    doc = {'started': datetime.now(timezone.utc).isoformat(), 'measured': measured or '', 'host': os.uname().nodename}
    s3.put_object(Bucket=BUCKET, Key=HOLD_KEY, Body=json.dumps(doc).encode(), ContentType='application/json')
    logger.info('publish hold set (measured %s)', measured or '?')


def copy_latest(reason: str, s3=None) -> dict:
    """Copy every object under latest/ onto published/ (only those whose ETag differs)."""
    s3 = s3 or _s3()
    t0 = time.time()
    src = _list(s3, LATEST)
    dst = _list(s3, PUBLISHED)
    copied = skipped = failed = 0
    for name, etag in sorted(src.items()):
        if not name or name.startswith('_published_at'):
            continue
        if dst.get(name) == etag:
            skipped += 1
            continue
        try:
            s3.copy_object(Bucket=BUCKET, Key=PUBLISHED + name, CopySource={'Bucket': BUCKET, 'Key': LATEST + name},
                           MetadataDirective='COPY', TaggingDirective='COPY')
            copied += 1
        except Exception as e:
            failed += 1
            logger.warning('publish: %s not copied (%s)', name, str(e)[:120])
    stamp = {'published_at': datetime.now(timezone.utc).isoformat(), 'reason': reason, 'copied': copied, 'skipped': skipped,
             'failed': failed, 'objects': len(src), 'secs': round(time.time() - t0, 1)}
    try:
        s3.put_object(Bucket=BUCKET, Key=STAMP_KEY, Body=json.dumps(stamp).encode(), ContentType='application/json')
    except Exception as e:
        logger.warning('publish: stamp not written (%s)', str(e)[:120])
    logger.info('publish (%s): %d copied, %d unchanged, %d failed of %d in %.1fs', reason, copied, skipped, failed, len(src), time.time() - t0)
    return stamp


def _refresh_dashboard(copied: int) -> None:
    """Re-render the dashboard's live cache entries from the copy just
    published, so the board turns over when published/ does and not at
    the next writer's invalidation (the 12:01 AM flip used to show on
    the dashboard only when the :25 board gate re-rendered, 2026-11-05).
    The invalidation publishes first itself; after a flip that is an
    ETag no-op."""
    if not copied:
        return
    try:
        import trends_iq  # noqa: E402
        trends_iq.invalidate_live_compute_view_caches()
    except Exception as e:
        logger.warning('publish: dashboard caches not refreshed (%s)', str(e)[:160])


def flip() -> int:
    """12:01 AM Pacific: the new day's boards go live with the data, and the hold is lifted."""
    s3 = _s3()
    stamp = copy_latest('flip', s3)
    try:
        s3.delete_object(Bucket=BUCKET, Key=HOLD_KEY)
    except Exception:
        pass
    _refresh_dashboard(int(stamp.get('copied') or 0))
    return 0 if stamp.get('failed', 0) == 0 else 2


def hourly() -> int:
    """Between flips: keep the current board's re-levels flowing, unless a new run is being held for the flip."""
    s3 = _s3()
    h = read_hold(s3)
    if h:
        logger.info('publish hourly: held (nightly run started %s for %s); nothing copied', h.get('started'), h.get('measured') or '?')
        return 0
    stamp = copy_latest('hourly', s3)
    _refresh_dashboard(int(stamp.get('copied') or 0))
    return 0 if stamp.get('failed', 0) == 0 else 2


def status() -> int:
    s3 = _s3()
    h = read_hold(s3)
    try:
        st = json.loads(s3.get_object(Bucket=BUCKET, Key=STAMP_KEY)['Body'].read())
    except Exception:
        st = {}
    src = _list(s3, LATEST); dst = _list(s3, PUBLISHED)
    behind = sorted(k for k, e in src.items() if k and not k.startswith('_published_at') and dst.get(k) != e)
    print(f"hold: {h or 'none'}")
    print(f"last publish: {st or 'never'}")
    print(f"latest/: {len(src)} objects; published/: {len(dst)} objects; differing: {len(behind)}" + (f" ({', '.join(behind[:8])}{'...' if len(behind) > 8 else ''})" if behind else ''))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['flip', 'hourly', 'hold', 'status'])
    p.add_argument('--measured', default='')
    a = p.parse_args(argv)
    if a.cmd == 'flip':
        return flip()
    if a.cmd == 'hourly':
        return hourly()
    if a.cmd == 'hold':
        hold(a.measured or None)
        return 0
    return status()


if __name__ == '__main__':
    sys.exit(main())
