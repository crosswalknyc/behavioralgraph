#!/usr/bin/env python3
"""Re-count every finished Pilot Testing dial on the people behind each seat, in place.

Jenna 2026-11-20, asked whether the dial should count the way every other count does
(by the people behind each seat) instead of as a plain average of the seats: "Yes,
weight the dial by the people behind each seat". New tests count that way as they are
prepared (digital_twin_pilot.prepare_watch, digital_twin_live.frame). This sweep brings
the tests already on file to the same line: for every twin in the library, every pilot
session (and every extra-asset read on it) whose dial is not yet stamped and whose seat
dials sit on the dial's clock is re-counted from those seat dials and the roster's people
(digital_twin_pilot.reweight_dial): the room line, every group line, the probes, the
dial average, the dead spot, the favorite moment's screen and the women / men note, and
the cached minute reads' lean. The battery, the people, the norms and the forecast are
left as they were. Each twin document is copied to digital-twins/_backups/ before it is
rewritten; a second run finds nothing to do.

    python3 scripts/reweight_pilot_dials.py --dry-run        # list what would change
    python3 scripts/reweight_pilot_dials.py                  # write
    python3 scripts/reweight_pilot_dials.py --twin <s3 key>  # one twin
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


def _fmt(v):
    return '-' if v is None else f"{float(v):.1f}"


def reweight_asset_reads(sess: dict, twin: dict) -> list:
    """The extra assets' reads (a second trailer, a further episode) kept
    their own seat dials; each is re-counted the same way."""
    out = []
    reads = sess.get('asset_reads') if isinstance(sess.get('asset_reads'), dict) else {}
    for aid, rec in reads.items():
        if not isinstance(rec, dict) or not isinstance(rec.get('seat_dials'), dict) or not rec.get('t'):
            continue
        if str(rec.get('dial_weighting') or '') == PI.DIAL_WEIGHTING:
            continue
        sub = {'id': f"{sess.get('id')}-{aid}", 'title': rec.get('label') or sess.get('title'), 'group': sess.get('group'),
               'media': {'hash': f"{(sess.get('media') or {}).get('hash') or sess.get('id')}|{aid}"},
               'dial': {'t': list(rec.get('t') or []), 'value': list(rec.get('dial') or []), 'splits': dict(rec.get('splits') or {})},
               'seat_dials': rec['seat_dials'], 'beats': list(rec.get('beats') or []), 'events': list(rec.get('events') or []),
               'scorecard': rec.get('scorecard') if isinstance(rec.get('scorecard'), dict) else None, 'probes': rec.get('probes')}
        r = PI.reweight_dial(sub, twin)
        if not r:
            continue
        rec['dial'] = sub['dial']['value']
        rec['splits'] = sub['dial']['splits']
        rec['probes'] = sub.get('probes')
        if sub.get('scorecard') is not None:
            rec['scorecard'] = sub['scorecard']
        rec['dial_weighting'] = PI.DIAL_WEIGHTING
        out.append((str(aid), r))
    return out


def _two_audiences(sess: dict, s3, payload: dict, key: str) -> bool:
    """A test with two twins in the room keeps a line per twin; re-read
    it when every twin can be loaded."""
    if not sess.get('two_audiences'):
        return False
    keys = []
    for r in ((sess.get('group') or {}).get('roster') or []):
        k = str((r or {}).get('twin') or '')
        if k and k not in keys:
            keys.append(k)
    twins = {}
    for k in keys:
        t = payload if k == key else SYN.load_twin(s3, k)
        if not t:
            return False
        twins[k] = t
    try:
        from migration import digital_twin_compare as CMP
        got = CMP.two_audiences(sess, twins)
        if got:
            sess['two_audiences'] = got
            return True
    except Exception as e:
        print(f"  two audiences left as they were: {e}")
    return False


def backup(s3, key: str, ts: str) -> str:
    name = key.rsplit('/', 1)[-1]
    if name.endswith('.json.gz'):
        name = name[:-8]
    dest = f"{BACKUP_PREFIX}{name}.pre_dial_people_{ts}.json.gz"
    s3.copy_object(Bucket=SYN.S3_BUCKET, CopySource={'Bucket': SYN.S3_BUCKET, 'Key': key}, Key=dest, MetadataDirective='COPY')
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--twin', default='', help='one twin document key')
    args = ap.parse_args()
    import boto3
    s3 = boto3.client('s3')
    ts = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
    runs = [{'key': args.twin}] if args.twin else DT.list_runs(s3)
    rows, skipped = [], 0
    for r in runs:
        key = r.get('key')
        payload = SYN.load_twin(s3, key)
        if not payload:
            print(f"skip {key}: not found")
            continue
        name = str(((payload.get('meta') or {}).get('name')) or key)
        changes = []
        for sess in (payload.get('pilot_sessions') or []):
            if not isinstance(sess, dict):
                continue
            before = PI.dial_is_weighted(sess)
            got = PI.reweight_dial(sess, payload)
            extra = reweight_asset_reads(sess, payload)
            if got:
                _two_audiences(sess, s3, payload, key)
                changes.append((sess, got, extra))
            elif extra:
                changes.append((sess, None, extra))
            elif not before and not PI.dial_is_weighted(sess):
                d = sess.get('dial') or {}
                if (d.get('t') or []) and sess.get('seat_dials'):
                    skipped += 1
                    print(f"  left alone {name} / {sess.get('id')} ({sess.get('title')}): seat dials are not on the dial's clock")
        if not changes:
            continue
        for sess, got, extra in changes:
            if got:
                rows.append((name, sess.get('id'), sess.get('title'), got['old_avg'], got['new_avg'], got['old_mean'], got['new_mean'], got['seats'], got['splits']))
            for aid, r2 in extra:
                rows.append((name, f"{sess.get('id')} asset {aid}", sess.get('title'), r2['old_avg'], r2['new_avg'], r2['old_mean'], r2['new_mean'], r2['seats'], r2['splits']))
        if args.dry_run:
            print(f"would write {name} ({key}): {len(changes)} test(s)")
            continue
        dest = backup(s3, key, ts)
        SYN.save_twin(s3, key, payload)
        print(f"wrote {name} ({key}): {len(changes)} test(s); backup {dest}")
    print()
    print(f"{'twin':28} {'session':24} {'title':34} {'avg was':>8} {'avg now':>8} {'line was':>9} {'line now':>9} seats splits")
    for name, sid, title, oa, na, om, nm, seats, splits in rows:
        print(f"{str(name)[:28]:28} {str(sid)[:24]:24} {str(title or '')[:34]:34} {_fmt(oa):>8} {_fmt(na):>8} {_fmt(om):>9} {_fmt(nm):>9} {seats:>5} {splits:>6}")
    print(f"\n{len(rows)} dial(s) re-counted{' (dry run, nothing written)' if args.dry_run else ''}; {skipped} left alone")
    return 0


if __name__ == '__main__':
    sys.exit(main())
