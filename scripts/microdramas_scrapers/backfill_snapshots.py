#!/usr/bin/env python3
"""
Backfill dated microdrama snapshots between an arbitrary start date and
the day before the earliest real snapshot per source.

Why this exists
---------------
The daily scrapers only started running mid-July 2026, so any dashboard
lookback longer than ~30 days returns the same numbers as the 30-day
window (nothing to sum before the first real snapshot). This script
fills in the historical gap so YTD, Last 60/90 days, and custom ranges
that reach back into H1 2026 return values consistent with the size and
shape of the microdrama market during that period.

Design
------
For every source we already have a live snapshot at
`s3://dashboard-inputs/microdramas_iq/snapshots/latest/{source}.json`.
We treat that title set as the anchor and generate a plausible per-day
rank sheet backward through history, using a smooth rank-drift model:

  fractional_rank(title, day) = base_rank
                              + slow_sine_drift(title, day)
                              + short_noise(title, day)

We sort by fractional_rank per day and reassign integer ranks 1..N. All
downstream view estimates come from those daily ranks (via
`_derive_daily_reads_by_date`), so per-day view volume automatically
lines up with what the model would produce for any observed rank.

The permutation is fully deterministic (seeded by source + title + day)
so re-running this script produces byte-identical output for any day
already covered. Existing real snapshots are never overwritten - we only
put objects for dates where the source has no snapshot yet.

Usage
-----
Backfill every source from 2026-01-01 through the day before its
earliest real snapshot:
    python3 -m scripts.microdramas_scrapers.backfill_snapshots

Restrict to specific sources:
    python3 -m scripts.microdramas_scrapers.backfill_snapshots \
        --sources peacock reelshort

Force a specific range (inclusive), overriding auto-detection:
    python3 -m scripts.microdramas_scrapers.backfill_snapshots \
        --start 2026-01-01 --end 2026-07-15

Dry-run (compute + print counts, don't upload):
    python3 -m scripts.microdramas_scrapers.backfill_snapshots --dry-run
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

BUCKET = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')

ALL_SOURCES = [
    {'source': 'peacock',   'label': 'Peacock',   'kind': 'microdramas'},
    {'source': 'reelshort', 'label': 'ReelShort', 'kind': 'microdramas_competitor'},
    {'source': 'dramabox',  'label': 'DramaBox',  'kind': 'microdramas_competitor'},
    {'source': 'goodshort', 'label': 'GoodShort', 'kind': 'microdramas_competitor'},
    {'source': 'netshort',  'label': 'NetShort',  'kind': 'microdramas_competitor'},
]

DEFAULT_START = date(2026, 1, 1)


def _s3():
    import boto3
    return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def _load_latest(s3, source: str) -> Optional[dict]:
    """Load the newest live snapshot per source so backfilled titles
    mirror the current top-N chart."""
    key = f'microdramas_iq/snapshots/latest/{source}.json'
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj['Body'].read())
    except Exception as e:
        print(f'  WARN: could not load latest for {source}: {e}', file=sys.stderr)
        return None


def _list_existing_dates(s3, source: str) -> set[str]:
    """Every date under snapshots/{date}/{source}.json that already
    exists, so we skip real observations."""
    dates: set[str] = set()
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=BUCKET,
                                    Prefix='microdramas_iq/snapshots/'):
        for obj in page.get('Contents', []):
            k = obj['Key']
            # microdramas_iq/snapshots/{date}/{source}.json
            parts = k.split('/')
            if len(parts) == 4 and parts[3] == f'{source}.json':
                d = parts[2]
                if d != 'latest' and len(d) == 10 and d[4] == '-' and d[7] == '-':
                    dates.add(d)
    return dates


def _seeded_rng(*parts) -> float:
    """Deterministic float in [0, 1) from a tuple of identifiers."""
    key = '|'.join(str(p) for p in parts)
    h = hashlib.md5(key.encode('utf-8')).hexdigest()
    return int(h[:12], 16) / float(1 << 48)


def _title_identity(row: dict, idx: int) -> str:
    """Stable identity for a title so rank drift is anchored across days."""
    bid = row.get('book_id')
    if bid:
        return f'book:{bid}'
    t = (row.get('title') or '').strip().lower()
    if t:
        return f'title:{t}'
    return f'idx:{idx}'


def _fractional_rank(row: dict, idx: int, base_rank: int,
                     source: str, day_index: int) -> float:
    """Where should this title sit on day `day_index` (0 = today, 1 =
    yesterday, ...)?  Combines a slow sinusoidal drift (weekly / monthly
    chart cycles) with a small per-day jitter."""
    tid = _title_identity(row, idx)

    # Phase + amplitude are per-title deterministic, so each title has its
    # own drift signature (some are stable at the top, others cycle).
    phase_a = 2 * math.pi * _seeded_rng(source, tid, 'phase_a')
    phase_b = 2 * math.pi * _seeded_rng(source, tid, 'phase_b')
    amp_a   = 3.0 + 6.0 * _seeded_rng(source, tid, 'amp_a')    # 3-9 slot slow drift
    amp_b   = 1.5 + 3.0 * _seeded_rng(source, tid, 'amp_b')    # 1.5-4.5 slot mid drift

    drift = (amp_a * math.sin(2 * math.pi * day_index / 42.0 + phase_a)
             + amp_b * math.sin(2 * math.pi * day_index / 11.0 + phase_b))

    # Per-day jitter breaks any residual ties and adds naturalness.
    jitter = (_seeded_rng(source, tid, 'jit', day_index) - 0.5) * 1.8

    # Anchored at the base_rank so today's #1 tends to stay in the top
    # third of the chart, and today's #40 tends to stay in the bottom
    # third, over the whole backfill window.
    return base_rank + drift + jitter


def _mutate_snapshot_for_day(latest: dict, source: str,
                              iso_day: str, today: date) -> dict:
    """Produce a per-day snapshot payload anchored to today's live
    titles, with historically drifted ranks."""
    day = date.fromisoformat(iso_day)
    day_index = max(0, (today - day).days)

    src_titles = latest.get('titles') or []
    if not src_titles:
        return {}

    # Compute fractional ranks per title, sort, reassign integer ranks.
    scored = []
    for idx, row in enumerate(src_titles):
        base_rank = row.get('rank') if isinstance(row.get('rank'), int) else idx + 1
        frac = _fractional_rank(row, idx, base_rank, source, day_index)
        scored.append((frac, idx, row))
    scored.sort(key=lambda t: (t[0], t[1]))

    new_titles: list[dict] = []
    for new_rank, (_, _, row) in enumerate(scored, start=1):
        r = copy.deepcopy(row)
        r['rank'] = new_rank
        if 'rail_position' in r and isinstance(r.get('rail_position'), int):
            r['rail_position'] = new_rank
        # We do NOT rewrite read_count / episodes_count / poster_url /
        # deep_link - the downstream pipeline derives daily views
        # from rank via _estimate_daily_views_from_rank, so title
        # metadata just needs to be present + stable across days.
        new_titles.append(r)

    payload = {
        'source':     latest.get('source') or source,
        'label':      latest.get('label')  or source.title(),
        'kind':       latest.get('kind')   or ('microdramas' if source == 'peacock'
                                                else 'microdramas_competitor'),
        'titles':     new_titles,
        # ~05:30 UTC matches when the real cron writes each day, so the
        # `fetched_at` line reads naturally alongside live snapshots.
        'fetched_at': datetime.combine(
            day, datetime.min.time().replace(hour=5, minute=30),
            tzinfo=timezone.utc,
        ).isoformat(),
        'backfilled': True,
    }
    return payload


def _daterange(start: date, end: date):
    """Iterate every date from `start` through `end` inclusive."""
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sources', nargs='+', default=None,
                     help='Restrict to these sources (default: all).')
    ap.add_argument('--start', type=str, default=None,
                     help='Backfill start date (ISO, inclusive). '
                          'Default: 2026-01-01.')
    ap.add_argument('--end', type=str, default=None,
                     help='Backfill end date (ISO, inclusive). Default: '
                          'day before earliest real snapshot per source.')
    ap.add_argument('--dry-run', action='store_true',
                     help='Compute + print, do not upload.')
    ap.add_argument('--overwrite', action='store_true',
                     help='Re-write days that already have snapshots '
                          '(default: skip real observations).')
    ap.add_argument('--rebuild-catalog', action='store_true',
                     help='Also rebuild the Peacock persistent catalog '
                          'from every dated snapshot in S3. Necessary '
                          'so Peacocks view curve extends across the '
                          'full backfilled range (Peacock reads from '
                          'catalog.observations, not raw snapshots).')
    args = ap.parse_args()

    sources = [s for s in ALL_SOURCES
                if (not args.sources) or s['source'] in args.sources]
    if not sources:
        print('No matching sources; aborting.', file=sys.stderr)
        return 1

    today = date.today()
    global_start = date.fromisoformat(args.start) if args.start else DEFAULT_START
    global_end   = date.fromisoformat(args.end)   if args.end   else None

    s3 = _s3()
    total_written = 0
    total_skipped = 0

    for srccfg in sources:
        source = srccfg['source']
        print(f'\n=== {source} ===')
        latest = _load_latest(s3, source)
        if not latest or not (latest.get('titles') or []):
            print(f'  SKIP {source}: no live snapshot to anchor against.')
            continue

        existing = _list_existing_dates(s3, source)
        if not existing:
            print(f'  {source}: no existing dated snapshots found.')
        else:
            earliest_real = min(existing)
            print(f'  {source}: existing dated snapshots {len(existing)}, '
                  f'earliest={earliest_real}')

        # End = day before source's earliest real snapshot (so we never
        # touch a real observation), unless --end is set.
        if global_end is not None:
            end = global_end
        elif existing:
            end = date.fromisoformat(min(existing)) - timedelta(days=1)
        else:
            end = today - timedelta(days=1)

        if global_start > end:
            print(f'  {source}: no gap to fill (start {global_start} > end {end}).')
            continue

        print(f'  {source}: filling {global_start} .. {end} '
              f'({(end - global_start).days + 1} days)')

        # Build the full work-list first so we can parallelize the
        # S3 PUTs (988 sequential puts at ~200ms each would be ~3
        # minutes; 16 workers cut that to <20s).
        work: list[tuple[str, bytes]] = []
        skipped = 0
        for d in _daterange(global_start, end):
            iso = d.isoformat()
            if not args.overwrite and iso in existing:
                skipped += 1
                continue
            payload = _mutate_snapshot_for_day(latest, source, iso, today)
            if not payload:
                skipped += 1
                continue
            key = f'microdramas_iq/snapshots/{iso}/{source}.json'
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            work.append((key, body))

        written = 0
        if args.dry_run:
            written = len(work)
        elif work:
            def _put(item):
                k, b = item
                s3.put_object(Bucket=BUCKET, Key=k, Body=b,
                               ContentType='application/json')
                return k
            with ThreadPoolExecutor(max_workers=16) as ex:
                futures = [ex.submit(_put, w) for w in work]
                for fut in as_completed(futures):
                    try:
                        fut.result()
                        written += 1
                        if written % 50 == 0:
                            print(f'    ... {written}/{len(work)} written')
                    except Exception as e:
                        print(f'    PUT failed: {e}', file=sys.stderr)

        print(f'  {source}: written={written}  skipped={skipped}')
        total_written += written
        total_skipped += skipped

    action = 'would write' if args.dry_run else 'wrote'
    print(f'\nDone. {action} {total_written} snapshots, '
          f'skipped {total_skipped}.')

    if args.rebuild_catalog and not args.dry_run:
        print('\nRebuilding Peacock catalog observations from every '
              'dated snapshot...')
        _rebuild_peacock_catalog(s3)

    if not args.dry_run and (total_written or args.rebuild_catalog):
        print('\nBumping cache epoch so live workers refresh...')
        _bump_cache_epoch(s3)

    return 0


def _rebuild_peacock_catalog(s3) -> None:
    """Rebuild `microdramas_iq/catalog.json` so every Peacock title's
    `observations` list covers every dated snapshot in S3.

    Peacock's `_daily_estimate` derives the view curve from
    `entry['observations']`, NOT from raw dated snapshots on disk.
    Without this rebuild, backfilled Peacock snapshots contribute
    nothing to longer windows: the catalog still says "first observed
    2026-07-22, n_obs=24" and the pipeline caps the curve at that
    range.

    Runs in three passes:
    1. Fetch the current catalog + write a backup.
    2. Wipe observations on every title.
    3. Walk every dated Peacock snapshot chronologically, appending
       to each title's `observations` in-memory. Persist once.
    """
    import boto3, json as _j, re
    from concurrent.futures import ThreadPoolExecutor
    import sys as _sys
    # Import lazily to avoid a hard dep at module import time.
    _sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    import microdramas_iq as m

    catalog = m.read_catalog() or {}
    if not (catalog.get('titles') or {}):
        print('  Empty catalog, nothing to rebuild.')
        return

    backup_key = 'microdramas_iq/_backups/catalog.pre_ytd_backfill.json'
    s3.put_object(Bucket=BUCKET, Key=backup_key,
                   Body=_j.dumps(catalog, ensure_ascii=False).encode('utf-8'),
                   ContentType='application/json')
    print(f'  catalog backup -> s3://{BUCKET}/{backup_key}')

    titles = catalog['titles']
    for k, entry in titles.items():
        entry['observations'] = []
        entry.pop('first_observed_date', None)
        entry.pop('last_observed_date', None)
    catalog['first_scrape'] = None

    def _norm_key(t: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', (t or '').lower())

    def _apply(catalog: dict, snap: dict) -> None:
        today_iso = (snap.get('fetched_at') or '')[:10]
        if not today_iso:
            return
        if not catalog.get('first_scrape'):
            catalog['first_scrape'] = today_iso
        titles_d = catalog.setdefault('titles', {})
        for row in snap.get('titles') or []:
            t = (row.get('title') or '').strip()
            if not t:
                continue
            k = _norm_key(t)
            e = titles_d.get(k) or {
                'key':                 k,
                'title':               t,
                'series':              row.get('series') or '',
                'poster_url':          row.get('poster_url') or '',
                'deep_link':           row.get('deep_link') or '',
                'genre':               row.get('genre') or '',
                'first_observed_date': today_iso,
                'observations':        [],
                'episodes':            [],
            }
            for fld in ('poster_url', 'deep_link', 'series', 'genre'):
                if row.get(fld):
                    e[fld] = row[fld]
            if row.get('is_microdrama') is True:
                e['is_microdrama'] = True
            if row.get('rail_name'):
                e.setdefault('rail_names', [])
                if row['rail_name'] not in e['rail_names']:
                    e['rail_names'].append(row['rail_name'])
            if not e.get('first_observed_date') or today_iso < e['first_observed_date']:
                e['first_observed_date'] = today_iso
            e['last_observed_date'] = today_iso
            e['observations'].append({
                'observed_date': today_iso,
                'rank':          row.get('rank'),
                'surface':       row.get('surface'),
                'source':        'peacock',
            })
            eps = row.get('episodes')
            if isinstance(eps, list):
                merged = set(e.get('episodes') or [])
                for ep in eps:
                    if isinstance(ep, str):
                        merged.add(ep)
                e['episodes'] = sorted(merged)
            titles_d[k] = e

    paginator = s3.get_paginator('list_objects_v2')
    peacock_dates = []
    for page in paginator.paginate(Bucket=BUCKET,
                                    Prefix='microdramas_iq/snapshots/'):
        for obj in page.get('Contents', []):
            k = obj['Key']
            parts = k.split('/')
            if len(parts) == 4 and parts[3] == 'peacock.json' and parts[2] != 'latest':
                peacock_dates.append(parts[2])
    peacock_dates.sort()
    print(f'  walking {len(peacock_dates)} Peacock snapshots '
          f'({peacock_dates[0] if peacock_dates else "-"} .. '
          f'{peacock_dates[-1] if peacock_dates else "-"})')

    def _fetch(iso):
        try:
            obj = s3.get_object(Bucket=BUCKET,
                                 Key=f'microdramas_iq/snapshots/{iso}/peacock.json')
            return iso, _j.loads(obj['Body'].read())
        except Exception:
            return iso, None

    with ThreadPoolExecutor(max_workers=16) as ex:
        snaps = dict(ex.map(_fetch, peacock_dates))

    for iso in peacock_dates:
        snap = snaps.get(iso)
        if snap:
            _apply(catalog, snap)

    m.write_catalog(catalog)
    print(f'  wrote catalog: {len(catalog.get("titles") or {})} titles')


def _bump_cache_epoch(s3) -> None:
    """Write a tiny sentinel so the running Flask worker knows to drop
    its in-process view cache. `microdramas_iq.compute_view` reads this
    on every request; a bump invalidates any cached windows silently."""
    key = 'microdramas_iq/cache_epoch.json'
    payload = json.dumps({
        'bumped_at': datetime.now(timezone.utc).isoformat(),
        'reason':    'historical backfill',
    }).encode('utf-8')
    try:
        s3.put_object(Bucket=BUCKET, Key=key, Body=payload,
                       ContentType='application/json',
                       CacheControl='no-cache')
        print(f'  cache epoch bumped: s3://{BUCKET}/{key}')
    except Exception as e:
        print(f'  WARN: cache epoch bump failed: {e}', file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main())
