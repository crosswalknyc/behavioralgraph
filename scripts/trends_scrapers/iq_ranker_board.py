"""Build the compact per-day IQ Rankers board.

The two estimate snapshots a day board is assembled from are large
(`stream_estimates.json` reached 42MB on 2026-09-15 and grows). Reading
them once per night is fine; reading them again for every backfill day
or every re-score is not. This writes the distilled board back into the
same dated snapshot folder as

    s3://dashboard-inputs/trends_iq_snapshots/<date>/iq_ranker_board.json

about 400KB for a typical day: one row per board entry with its key,
kind, surface, title, second line, chart rank, and the anchored audience
already converted to daily US people.

Nothing here reads the clickstream. See
`.cursor/rules/trends-rankers-never-clickstream.mdc`.

Usage on Hetzner:

    python3 -m scripts.trends_scrapers.iq_ranker_board              # yesterday
    python3 -m scripts.trends_scrapers.iq_ranker_board --date 2026-09-14
    python3 -m scripts.trends_scrapers.iq_ranker_board --days 30    # backfill
    python3 -m scripts.trends_scrapers.iq_ranker_board --days 30 --workers 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

import iq_ranker_signals as sig  # noqa: E402

BUCKET = os.environ.get('IQR_SIGNAL_BUCKET', sig.S3_DEFAULT_BUCKET)


def build_one(day: str, *, force: bool = False) -> dict:
    s3 = boto3.client('s3')
    key = f'{sig.SNAPSHOT_PREFIX}/{day}/{sig.BOARD_FILENAME}'
    if not force:
        try:
            s3.head_object(Bucket=BUCKET, Key=key)
            return {'day': day, 'status': 'exists'}
        except Exception:
            pass
    t0 = time.time()
    board = sig.build_day_board(s3_client=s3, day=day, bucket=BUCKET)
    if not board:
        return {'day': day, 'status': 'no_snapshots', 'count': 0}
    payload = {
        'day': day,
        'count': len(board),
        'surface_totals': {k: round(v, 2)
                           for k, v in sig.surface_totals(board).items()},
        'items': [b.as_dict() for b in board],
    }
    body = json.dumps(payload).encode()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                  ContentType='application/json')
    return {'day': day, 'status': 'built', 'count': len(board),
            'bytes': len(body), 'elapsed_s': round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='single ISO date (default: yesterday)')
    ap.add_argument('--days', type=int, default=0,
                    help='build this many days back from --date')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--force', action='store_true',
                    help='rebuild even when the board already exists')
    args = ap.parse_args()

    end = args.date or (date.today() - timedelta(days=1)).isoformat()
    days = ([end] if args.days <= 0
            else sig.recent_days(end, args.days))

    results = []
    if len(days) == 1:
        results.append(build_one(days[0], force=args.force))
    else:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {ex.submit(build_one, d, force=args.force): d for d in days}
            for f in as_completed(futs):
                try:
                    results.append(f.result())
                except Exception as e:
                    results.append({'day': futs[f], 'status': 'error',
                                    'error': str(e)[:200]})

    for r in sorted(results, key=lambda d: d['day']):
        print(json.dumps(r))
    built = sum(1 for r in results if r.get('status') == 'built')
    print(f'[iq_ranker_board] built={built} '
          f'exists={sum(1 for r in results if r.get("status") == "exists")} '
          f'missing={sum(1 for r in results if r.get("status") == "no_snapshots")} '
          f'errors={sum(1 for r in results if r.get("status") == "error")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
