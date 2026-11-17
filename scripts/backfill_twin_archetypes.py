#!/usr/bin/env python3
"""Put the kinds of people on every twin already in the library (2026-11-17).

Twins built before today carry no ``archetypes``; a room opened on one
derives the kinds on the spot (deterministic, then the desk when a
model answers), which makes the first open on an older twin slow. This
runs the desk ahead of time on the build server, twin by twin, and
writes the kinds back in place (same key, nothing else on the payload
touched). Idempotent: a twin whose kinds match its fingerprint and the
current version is skipped unless ``--force``.

    python3 scripts/backfill_twin_archetypes.py            # every twin in the index
    python3 scripts/backfill_twin_archetypes.py --limit 20 # the twenty most recent
    python3 scripts/backfill_twin_archetypes.py --key digital-twins/jenna/gilmore_girls_...json.gz
    python3 scripts/backfill_twin_archetypes.py --dry-run  # say what would change

Needs the model (ANTHROPIC_API_KEY in the environment); without one, or
when the desk does not answer inside DTIQ_ARCHETYPE_BUDGET_S (default
300 s; the box can afford 900), the deterministic kinds are written and
the desk runs again at the next open or the next pass of this script
(a deterministic twin is never skipped).
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
# a script talks to the models directly through the strong chain; binding the Prometheus host would boot the dashboard's
# background threads inside this process (2026-11-17, seen on the box)
os.environ.setdefault('DTIQ_ROOM_HOST', '0')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--key', default='', help='one twin key')
    ap.add_argument('--limit', type=int, default=0, help='the N most recent twins')
    ap.add_argument('--force', action='store_true', help='re-derive even when the kinds are current')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-model', action='store_true', help='deterministic kinds only')
    args = ap.parse_args(argv)
    import boto3
    from migration import digital_twin_archetypes as A
    from migration import digital_twins as DT
    s3 = boto3.client('s3')
    keys = [args.key] if args.key else [r.get('key') for r in DT.list_runs(s3, limit=5000) if r.get('key')]
    if args.limit:
        keys = keys[:args.limit]
    done = skipped = failed = 0
    for key in keys:
        payload = DT.load_twin(s3, key)
        if not payload:
            print(f"[kinds] {key}: not found")
            failed += 1
            continue
        have = payload.get('archetypes') or {}
        if not args.force and have.get('groups') and have.get('fingerprint') == A.fingerprint(payload) and int(have.get('version') or 0) >= A.ARCHETYPE_VERSION and have.get('method') == 'reasoned':
            skipped += 1
            continue
        t0 = time.time()
        try:
            if args.no_model:
                arch = A.derive_archetypes(payload, call=None, force=True)
            else:
                arch = A.ensure_archetypes(payload, force=True)
        except Exception as e:
            print(f"[kinds] {key}: failed: {str(e)[:160]}")
            failed += 1
            continue
        line = f"[kinds] {key}: {arch.get('k')} kinds ({arch.get('method')}) in {time.time() - t0:.1f}s; " + '; '.join(A.archetype_lines(payload)[:3])
        print(line)
        if not args.dry_run:
            DT.save_twin(s3, key, payload)
        done += 1
    print(f"[kinds] done={done} skipped={skipped} failed={failed}")
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
