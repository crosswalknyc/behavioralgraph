#!/usr/bin/env python3
"""Seed the Pilot Testing norms ledger with the published anchors.

    python3 scripts/seed_pilot_norms.py --dry-run     # what would be written
    python3 scripts/seed_pilot_norms.py               # write to the bucket (idempotent)
    python3 scripts/seed_pilot_norms.py --report      # cells, seeded, accumulated, sources

Idempotent: an existing seeded cell with the same seed is left alone, a
cell our own tests have blended into is never overwritten (its seed
fields refresh), a changed seed updates the cell. The sources on each
cell are internal and never reach a page a user reads.
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

from migration import digital_twin_norms as N  # noqa: E402


def _s3():
    try:
        import boto3
        return boto3.client('s3')
    except Exception as e:
        print(f"no s3 client: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--json', action='store_true', help='print the counts as JSON')
    ap.add_argument('--drop-tests', default='', help='comma-separated test ids whose reads leave the ledger first (trial runs); a cell left without tests goes back to its seed')
    ap.add_argument('--prune-unseeded', action='store_true', help='remove cells that have no published anchor and no tests')
    ap.add_argument('--retire', default='', help='comma-separated cell keys (format|genre|metric) to remove when they carry no tests of our own (anchors withdrawn from the seed)')
    args = ap.parse_args()
    if args.report:
        s3 = _s3()
        rep = N.sources_report(s3=s3)
        print(json.dumps(rep, indent=1) if args.json else f"cells {rep['cells']}, seeded {rep['seeded']}, accumulated {rep['accumulated']}, sources {rep['sources']}" + (' (in-memory seed, no ledger in the bucket yet)' if rep.get('in_memory') else ''))
        return 0
    s3 = None if args.dry_run else _s3()
    if not args.dry_run and s3 is None:
        print('cannot write without an s3 client')
        return 2
    drop = [t.strip() for t in str(args.drop_tests or '').split(',') if t.strip()]
    retire = [k.strip() for k in str(args.retire or '').split(',') if k.strip()]
    if not args.dry_run and (drop or retire or args.prune_unseeded):
        # a copy of the ledger before anything leaves it
        import time as _time
        try:
            cur = s3.get_object(Bucket=N.S3_BUCKET, Key=N.NORMS_KEY)['Body'].read()
            bk = f"{N.BACKUP_PREFIX}norms.pre_reseed_{_time.strftime('%Y%m%d_%H%M%S', _time.gmtime())}.json"
            s3.put_object(Bucket=N.S3_BUCKET, Key=bk, Body=cur, ContentType='application/json')
            print('backup', bk)
        except Exception as e:
            print(f'no ledger to back up ({e})')
    if retire and not args.dry_run:
        def _retire(doc):
            gone = 0
            for k in retire:
                c = doc['cells'].get(k)
                if c is not None and not int(c.get('our_n') or 0):
                    del doc['cells'][k]
                    gone += 1
            print(f'retired {gone} anchor cells')
            return doc if gone else None
        N.update(s3, _retire)
    stats = N.seed(s3, dry_run=args.dry_run, drop_tests=drop, prune_unseeded=args.prune_unseeded)
    if args.json:
        print(json.dumps(stats, indent=1))
    else:
        print(("would write" if args.dry_run else "wrote") + f" {stats['written']} cells, updated {stats['updated']}, skipped {stats['skipped']}, kept {stats['kept_accumulated']} accumulated; "
              f"{stats['cells']} seed cells from {stats['sources']} sources; dropped {stats.get('dropped_tests', 0)} trial reads, removed {stats.get('removed', 0)} cells")
    if not args.dry_run:
        rep = N.sources_report(s3=s3)
        print(f"ledger now: cells {rep['cells']}, seeded {rep['seeded']}, accumulated {rep['accumulated']}, sources {rep['sources']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
