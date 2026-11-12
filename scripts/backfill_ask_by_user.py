#!/usr/bin/env python3
"""Backfill the reader-keyed ask rows (2026-11-12).

`render_usage_log` now writes every ask a second time, in short form,
under the asker's own prefix (`system/usage/ask_log/_by_user/<user>/
<day>/...`), so "where is the howdy deck" reads one reader's rows
instead of the whole log. This writes those rows for the asks already in
the log and then the marker that tells the lookup the rows are complete
(`system/ops/pm_ask_by_user_ready.json`). Run it once after the writer is live; running
it again is harmless (a row that already matches is left alone).

    python3 scripts/backfill_ask_by_user.py            # dry run: counts only
    python3 scripts/backfill_ask_by_user.py --apply

Read-only against the log itself and against everything a reader owns.
The rows it writes are new system records; nothing is replaced except a
reader-keyed row that no longer matches its log record.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import render_usage_log as rul  # noqa: E402

BUCKET = 'dashboard-inputs'
_ROW_RX = re.compile(r"^" + re.escape(rul.ASK_PREFIX) + r"\d{4}-\d{2}-\d{2}/[^/]+\.json$")


def log_rows(s3):
    keys, token = [], None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=rul.ASK_PREFIX)
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        keys += [o['Key'] for o in (r.get('Contents') or []) if _ROW_RX.match(o['Key'])]
        if not r.get('IsTruncated'):
            return sorted(keys)
        token = r.get('NextContinuationToken')


def _json(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return None


def plan_one(s3, key):
    rec = _json(s3, key)
    if not isinstance(rec, dict) or not rul._indexable_user(rec.get('user')):
        return key, None, None, 'skipped'
    target = rul.ask_user_key(rec.get('user'), key)
    row = rul.ask_user_row(rec, key)
    have = _json(s3, target)
    return key, target, row, ('same' if have == row else ('new' if have is None else 'changed'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--workers', type=int, default=24)
    args = ap.parse_args()
    import boto3
    s3 = boto3.client('s3', region_name='us-east-2')
    keys = log_rows(s3)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        plans = list(ex.map(lambda k: plan_one(s3, k), keys))
    states = Counter(p[3] for p in plans)
    users = Counter(p[2]['user'] for p in plans if p[2])
    print(f"log rows: {len(keys)} | {dict(states)} | readers: {len(users)}")
    print('most rows:', users.most_common(8))
    todo = [p for p in plans if p[3] in ('new', 'changed')]
    if not args.apply:
        print(f"dry run: {len(todo)} reader-keyed row(s) would be written; nothing written")
        return 0

    def put(p):
        s3.put_object(Bucket=BUCKET, Key=p[1], Body=json.dumps(p[2]).encode('utf-8'), ContentType='application/json')
        return 1
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        wrote = sum(ex.map(put, todo))
    marker = {'ready': True, 'backfilled_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
              'log_rows': len(keys), 'reader_rows': sum(users.values()), 'readers': len(users),
              'first_day': keys[0][len(rul.ASK_PREFIX):][:10] if keys else '', 'last_day': keys[-1][len(rul.ASK_PREFIX):][:10] if keys else ''}
    s3.put_object(Bucket=BUCKET, Key=rul.ASK_BY_USER_READY_KEY, Body=json.dumps(marker).encode('utf-8'), ContentType='application/json')
    print(f"wrote {wrote} reader-keyed row(s); marker -> {rul.ASK_BY_USER_READY_KEY}: {marker}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
