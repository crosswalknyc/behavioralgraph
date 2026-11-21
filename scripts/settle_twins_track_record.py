#!/usr/bin/env python3
"""The daily track record check on the build box (Digital Twins IQ,
2026-11-20).

Settles every call that came due by its own check (our own count, a
published or certified result, the client's own results), asks the
client for results on the due day, and alerts on a check that keeps
failing. With ``--weekly-summary`` it sends the week's newly scored
calls, hits and misses instead. Runs from
``twins-track-record-settle.timer`` (daily) and
``twins-track-record-weekly.timer`` (Mondays).

    python3 scripts/settle_twins_track_record.py --dry-run
    python3 scripts/settle_twins_track_record.py
    python3 scripts/settle_twins_track_record.py --weekly-summary [--dry-run] [--force]

Leaves with os._exit once its work is on S3: the usage log's pricing
lookup imports the dashboard app, whose threads would hold the process
open.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DTIQ_ROOM_HOST', '0')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Settle the Digital Twins IQ track record.')
    ap.add_argument('--dry-run', action='store_true', help='check and print, write and send nothing')
    ap.add_argument('--weekly-summary', action='store_true', help='send the weekly summary instead of the daily check')
    ap.add_argument('--force', action='store_true', help='send the weekly summary even when nothing is new')
    args = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3')
    from migration import digital_twin_settle as ST
    if args.weekly_summary:
        res = ST.weekly_summary(s3, dry_run=args.dry_run, force=args.force)
        print(json.dumps({k: v for k, v in res.items() if k != 'body'}, default=str))
        if args.dry_run and res.get('body'):
            print(res['body'])
        return 0
    res = ST.run(s3, dry_run=args.dry_run)
    print(json.dumps({'ran': res.get('ran'), 'reason': res.get('reason'), 'checked': res.get('checked'), 'counts': res.get('counts'),
                      'error': res.get('error'), 'alert': res.get('alert')}, default=str))
    for r in res.get('results') or []:
        print('  ', json.dumps(r, default=str)[:300])
    return 1 if res.get('error') else 0


if __name__ == '__main__':
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
