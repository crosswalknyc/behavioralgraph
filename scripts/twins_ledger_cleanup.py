#!/usr/bin/env python3
"""Clean up the open rows on the Digital Twins IQ track record
(2026-11-20).

Jenna, 2026-11-20, in a choice form: "Only calls we deliberately log
before the result; practice rooms stay internal, and the 24 open room
answers come off clients' view now". Every open row that is not a call
leaves the record (kept in storage with its status and the reason, never
deleted); an answer asked again in the same room folds into the first
time it was asked; a row that names a real result and a date still
ahead stays a call with its due day and measure filled in. The ledger
is backed up first.

    python3 scripts/twins_ledger_cleanup.py --dry-run
    python3 scripts/twins_ledger_cleanup.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def backup(s3) -> str:
    from migration import digital_twin_ledger as LG
    ts = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
    prefix = f"{LG.LEDGER_PREFIX}_backups/{ts}/"
    n = 0
    token = None
    while True:
        kw = {'Bucket': LG.S3_BUCKET, 'Prefix': LG.RECORDS_PREFIX, 'MaxKeys': 1000}
        if token:
            kw['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kw)
        for o in resp.get('Contents') or []:
            s3.copy_object(Bucket=LG.S3_BUCKET, CopySource={'Bucket': LG.S3_BUCKET, 'Key': o['Key']}, Key=prefix + 'records/' + o['Key'].rsplit('/', 1)[-1])
            n += 1
        token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
        if not token:
            break
    s3.copy_object(Bucket=LG.S3_BUCKET, CopySource={'Bucket': LG.S3_BUCKET, 'Key': LG.INDEX_KEY}, Key=prefix + 'index.json')
    return f"{prefix} ({n} records and the index)"


def plan(s3) -> list:
    from migration import digital_twin_calls as CALLS
    from migration import digital_twin_ledger as LG
    rows = [r for r in LG.load_index(s3).get('records') or []
            if LG.is_call(r) and not r.get('has_outcome') and not r.get('post_hoc') and not r.get('backfilled') and not r.get('logged')]
    recs = [LG.load_record(s3, r['id']) for r in rows]
    recs = [r for r in recs if r]
    return CALLS.classify_open_rows(recs)


def apply(s3, steps: list) -> None:
    from migration import digital_twin_ledger as LG
    for rec, status, reason, into in steps:
        if status == 'not_a_call':
            LG.update_call(s3, rec['id'], status='not_a_call', status_reason=reason, public=False)
        elif status == 'folded':
            LG.update_call(s3, rec['id'], status='folded', status_reason=reason, folded_into=into, public=False)
        else:
            LG.update_call(s3, rec['id'], status='call', status_reason=reason, public=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Clean up the open rows on the track record.')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3')
    steps = plan(s3)
    if not args.dry_run and steps:
        print('[cleanup] backup', backup(s3))
        apply(s3, steps)
    for rec, status, reason, into in steps:
        print(json.dumps({'id': rec['id'], 'subject': rec.get('subject'), 'made': str(rec.get('created_at') or '')[:16], 'question': ' '.join(str(rec.get('question') or '').split())[:120],
                          'now': status, 'why': reason, 'folded_into': into}, ensure_ascii=False))
    counts = {}
    for _r, st, _w, _i in steps:
        counts[st] = counts.get(st, 0) + 1
    print('[cleanup]', 'dry run' if args.dry_run else 'applied', counts)
    return 0


if __name__ == '__main__':
    code = main()
    sys.stdout.flush()
    os._exit(code)
