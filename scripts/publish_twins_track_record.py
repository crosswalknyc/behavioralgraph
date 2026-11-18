#!/usr/bin/env python3
"""Publish the Digital Twins IQ track record to the public site (2026-11-17).

Reads the outcome ledger (``migration/digital_twin_ledger``), takes the
public rows (scored, never post hoc) and the public summary, and writes
``site/assets/data/twins-track-record.json`` for twins-track-record.html.
With ``--backfill`` the first rows (the midterm calls and the NCIS: New
York pilot) are put on the ledger first. With ``--s3`` the same JSON is
also written to the site bucket key given.

    python3 scripts/publish_twins_track_record.py
    python3 scripts/publish_twins_track_record.py --backfill
    python3 scripts/publish_twins_track_record.py --s3 s3://crosswalk-site/assets/data/twins-track-record.json

The JSON carries no method words and no source names beyond the class
of each outcome (certified result, published, panel).
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

OUT = os.path.join(ROOT, 'site', 'assets', 'data', 'twins-track-record.json')
BANNED = ('synthetic', 'synth', 'model', 'estimat', 'research', 'panel', 'nielsen', 'comscore', 'samba', 'luminate', 'parrot', 'calibrat', 'anchor')


def build(s3) -> dict:
    from migration import digital_twin_ledger as LG
    rows = [{k: v for k, v in r.items() if k not in ('company', 'twin_key', 'session_id', 'id', 'public', 'house')} for r in LG.public_rows(s3, limit=500, site=True)]
    sm = LG.summary(s3, site=True)
    doc = {'as_of': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'rows': rows,
           'summary': {'calls_scored': sm.get('n_scored'), 'hit_rate': sm.get('hit_rate'), 'mean_relative_miss': sm.get('mean_rel_error'), 'coverage': sm.get('coverage'),
                       'by_kind': {k: {'n': v.get('n'), 'hit_rate': v.get('hit_rate'), 'mean_relative_miss': v.get('mean_rel_error'), 'mean_tvd': v.get('mean_tvd')} for k, v in (sm.get('by_kind') or {}).items()}}}
    # the public copy never names a source or a method; the outcome class is the one word that travels
    text = json.dumps(doc).lower()
    for w in BANNED:
        if w in text and w != 'panel':
            raise SystemExit(f"a banned word reached the public record: {w}")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--backfill', action='store_true', help='put the first rows on the ledger first')
    ap.add_argument('--s3', default='', help='also write the JSON to this s3://bucket/key')
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    s3 = None
    try:
        import boto3
        s3 = boto3.client('s3')
    except Exception as e:
        print(f"[track-record] no S3 client ({e}); the in-memory ledger is used")
    from migration import digital_twin_ledger as LG
    if args.backfill:
        rows = LG.backfill_known(s3)
        print(f"[track-record] backfilled {len(rows)} rows")
    doc = build(s3)
    print(f"[track-record] {len(doc['rows'])} public rows; summary {doc['summary']}")
    if args.dry_run:
        print(json.dumps(doc, indent=1)[:2000])
        return 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"[track-record] wrote {args.out}")
    if args.s3 and s3 is not None and args.s3.startswith('s3://'):
        bucket, key = args.s3[5:].split('/', 1)
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc, ensure_ascii=False).encode('utf-8'), ContentType='application/json', CacheControl='max-age=300')
        print(f"[track-record] wrote {args.s3}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
