#!/usr/bin/env python3
"""
Seed 7 days of dated historical snapshots for ReelShort + DramaBox so
the Competitors tab's default 7-day window renders on day 0. Each day
gets small deterministic rank jitter around the curated baseline so
the movement chips ("up", "down", "new", "dropped") show real motion
in the UI.

Once the real daily scraper (`scripts.microdramas_scrapers.run_all`)
starts running, live observations overwrite these seeds day by day.

Usage:
    python3 -m scripts.microdramas_scrapers.seed_history
    python3 -m scripts.microdramas_scrapers.seed_history --days 14
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

from scripts.microdramas_scrapers import reelshort as _reelshort
from scripts.microdramas_scrapers import dramabox as _dramabox


def _stable_shuffle(baseline: list[dict], day_iso: str, source: str) -> list[dict]:
    """Deterministic per-day rank permutation: same day + source always
    produces the same ordering so re-running seed_history is idempotent."""
    seed = int(hashlib.md5(f'{source}|{day_iso}'.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    rows = [dict(r) for r in baseline]

    # Small position-preserving jitter: swap random adjacent pairs so
    # ranks move by 1-3 slots day over day (matches how vertical drama
    # charts actually move).
    n = len(rows)
    swaps = max(2, n // 4)
    for _ in range(swaps):
        i = rng.randrange(0, max(1, n - 1))
        j = min(n - 1, i + rng.randint(1, 3))
        rows[i], rows[j] = rows[j], rows[i]

    # Occasional churn: drop one lower-ranked title, insert a "surprise"
    # new title at a random middle position ~15% of days.
    if rng.random() < 0.18 and n >= 12:
        surprise_pool = [
            {'title': 'The Reborn Duchess',       'genre': 'Revenge'},
            {'title': 'Bride of the Ice Alpha',   'genre': 'Werewolf'},
            {'title': 'Married the Wrong CEO',    'genre': 'CEO'},
            {'title': 'My Trillionaire Roommate', 'genre': 'Billionaire'},
            {'title': 'The Vampire\'s Substitute Wife', 'genre': 'Werewolf'},
            {'title': 'Second Chance With the Mafia King', 'genre': 'Mafia'},
        ]
        rows.pop()
        pick = rng.choice(surprise_pool)
        surprise = {
            'title':          pick['title'],
            'genre':          pick['genre'],
            'episodes_count': rng.randint(45, 75),
            'avg_rating':     round(rng.uniform(4.2, 4.7), 1),
        }
        rows.insert(rng.randint(5, n - 3), surprise)

    # Reassign ranks 1..N
    for i, r in enumerate(rows):
        r['rank'] = i + 1
    return rows


def _write_dated(bucket: str, day_iso: str, source: str,
                 label: str, rows: list[dict]) -> None:
    import boto3
    payload = {
        'source':     source,
        'label':      label,
        'kind':       'microdramas_competitor',
        'titles':     rows,
        'fetched_at': datetime.combine(
            date.fromisoformat(day_iso),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).isoformat(),
        'seed':       True,
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
    key = f'microdramas_iq/snapshots/{day_iso}/{source}.json'
    s3.put_object(Bucket=bucket, Key=key, Body=body,
                   ContentType='application/json')
    print(f'  {source:>10}  {day_iso}  {len(rows)} titles  -> s3://{bucket}/{key}')

    # Also refresh 'latest' with the most recent day
    if day_iso == date.today().isoformat():
        latest_key = f'microdramas_iq/snapshots/latest/{source}.json'
        s3.put_object(Bucket=bucket, Key=latest_key, Body=body,
                       ContentType='application/json',
                       CacheControl='public, max-age=60')
        print(f'  {source:>10}  (latest) -> s3://{bucket}/{latest_key}')


def main() -> int:
    ap = argparse.ArgumentParser(description='Seed dated competitor snapshots.')
    ap.add_argument('--days', type=int, default=7,
                     help='How many past days to seed (default 7).')
    args = ap.parse_args()

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    sources = [
        ('reelshort', 'ReelShort', _reelshort.CURATED_BASELINE),
        ('dramabox',  'DramaBox',  _dramabox.CURATED_BASELINE),
    ]

    today = date.today()
    print(f'Seeding {args.days} days of history for '
           f'{[s[0] for s in sources]} into s3://{bucket}/microdramas_iq/snapshots/')
    for offset in range(args.days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        for source, label, base in sources:
            rows = _stable_shuffle(base, d, source)
            _write_dated(bucket, d, source, label, rows)

    print('Done.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
