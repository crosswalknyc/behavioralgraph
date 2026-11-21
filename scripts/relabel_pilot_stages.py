#!/usr/bin/env python3
"""Make every stored pilot test's seat stage labels agree with the seats' bios, in place.

Jenna 2026-11-20 ("Yes, fix both"): in a stored test, a seat's stage label (discovering,
trying, in the habit, lapsing, returning) must be the stage its bio describes. For every
twin in the library, every pilot session is read seat by seat (digital_twin_pilot.bio_stage);
where the label differs from the bio, the label moves to the bio's stage and the group
lines that read the stage (the stage lines, avid and casual) are re-counted from the
stored seat dials, people-weighted (digital_twin_pilot.relabel_stages). The seat dials,
the room line, the battery and the scorecard are untouched; nothing is re-run. Each twin
document is copied to digital-twins/_backups/ before it is rewritten; a session carries a
stamp, so a second run finds nothing to do.

    python3 scripts/relabel_pilot_stages.py --dry-run
    python3 scripts/relabel_pilot_stages.py
    python3 scripts/relabel_pilot_stages.py --twin <s3 key>
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_synthesis as SYN  # noqa: E402
from migration import digital_twins as DT  # noqa: E402

BACKUP_PREFIX = 'digital-twins/_backups/'


def backup(s3, key: str, ts: str) -> str:
    name = key.rsplit('/', 1)[-1]
    if name.endswith('.json.gz'):
        name = name[:-8]
    dest = f"{BACKUP_PREFIX}{name}.pre_stage_relabel_{ts}.json.gz"
    s3.copy_object(Bucket=SYN.S3_BUCKET, CopySource={'Bucket': SYN.S3_BUCKET, 'Key': key}, Key=dest, MetadataDirective='COPY')
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--twin', default='')
    args = ap.parse_args()
    import boto3
    s3 = boto3.client('s3')
    ts = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
    runs = [{'key': args.twin}] if args.twin else DT.list_runs(s3)
    rows = []
    for r in runs:
        key = r.get('key')
        payload = SYN.load_twin(s3, key)
        if not payload:
            print(f"skip {key}: not found")
            continue
        name = str(((payload.get('meta') or {}).get('name')) or key)
        changed = []
        for sess in (payload.get('pilot_sessions') or []):
            if not isinstance(sess, dict):
                continue
            got = PI.relabel_stages(sess, payload)
            if got:
                changed.append((sess, got))
        if not changed:
            continue
        for sess, got in changed:
            for seat, who, was, now in got['seats']:
                rows.append((name, sess.get('id'), sess.get('title'), seat, who, was, now))
        if args.dry_run:
            print(f"would write {name} ({key}): {len(changed)} test(s), {sum(len(g['seats']) for _s, g in changed)} label(s)")
            continue
        dest = backup(s3, key, ts)
        SYN.save_twin(s3, key, payload)
        print(f"wrote {name} ({key}): {len(changed)} test(s); backup {dest}")
    print()
    print(f"{'twin':24} {'session':12} {'title':30} {'seat':>4} {'who':10} {'label was':14} {'label now':14}")
    for name, sid, title, seat, who, was, now in rows:
        print(f"{str(name)[:24]:24} {str(sid)[:12]:12} {str(title or '')[:30]:30} {seat:>4} {str(who)[:10]:10} {str(was)[:14]:14} {str(now)[:14]:14}")
    print(f"\n{len(rows)} label(s) moved across {len({(a, b) for a, b, *_ in rows})} test(s){' (dry run, nothing written)' if args.dry_run else ''}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
