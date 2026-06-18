"""Archive Journey IQ runs out of the live dashboard.

Removes named runs from `journey-iq/_index.json` (the live manifest the
dashboard lists), copies the underlying `.json.gz` payloads to the
`journey-iq/archive/` prefix, deletes the originals, and appends entries
to `journey-iq/_archive_index.json` with `archived_at` / `archived: true`
so admins can still find them via `?include_archive=1`.

Matching: case-insensitive contains on `project_name` OR `target`.

Usage:
    # dry run (default — prints what would change)
    python -m migration.archive_journey_iq_runs

    # actually do it
    python -m migration.archive_journey_iq_runs --apply

    # custom names (default = HUNGRY, OBSESSION)
    python -m migration.archive_journey_iq_runs --names HUNGRY OBSESSION COURTSIDE --apply

Requires AWS creds in env / ~/.aws/credentials and the same `dashboard-inputs`
bucket the build_*_payload.py scripts already write to.
"""

import argparse
import json
from datetime import datetime, timezone

import boto3


S3_BUCKET            = 'dashboard-inputs'
S3_PREFIX            = 'journey-iq/'
S3_ADMIN_PREFIX      = 'journey-iq/admin/'
S3_ARCHIVE_PREFIX    = 'journey-iq/archive/'
S3_INDEX_KEY         = 'journey-iq/_index.json'
S3_ARCHIVE_INDEX_KEY = 'journey-iq/_archive_index.json'

DEFAULT_NAMES = ['HUNGRY', 'OBSESSION']


def _load_index(s3, key):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8')) or {'runs': []}
    except s3.exceptions.NoSuchKey:
        return {'runs': []}
    except Exception as e:
        print(f"[warn] could not read s3://{S3_BUCKET}/{key}: {e}")
        return {'runs': []}


def _save_index(s3, key, idx):
    s3.put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=json.dumps(idx, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json',
    )


def _matches(entry, names_uc):
    proj   = (entry.get('project_name') or '').upper()
    target = (entry.get('target')       or '').upper()
    return any(n in proj or n in target for n in names_uc)


def _archive_key_for(live_key: str) -> str:
    """Map journey-iq/admin/foo.json.gz → journey-iq/archive/foo.json.gz.

    Tolerates entries that already live under archive/ (returns them
    unchanged) and entries that live under journey-iq/<other_user>/
    (rewrites to journey-iq/archive/<basename>).
    """
    if live_key.startswith(S3_ARCHIVE_PREFIX):
        return live_key
    if live_key.startswith(S3_ADMIN_PREFIX):
        return S3_ARCHIVE_PREFIX + live_key[len(S3_ADMIN_PREFIX):]
    if live_key.startswith(S3_PREFIX):
        # journey-iq/<user>/<file> → journey-iq/archive/<user>__<file>
        rest = live_key[len(S3_PREFIX):]
        return S3_ARCHIVE_PREFIX + rest.replace('/', '__')
    return S3_ARCHIVE_PREFIX + live_key.lstrip('/')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', nargs='+', default=DEFAULT_NAMES,
                    help='Project names to archive (case-insensitive contains match).')
    ap.add_argument('--apply', action='store_true',
                    help='Actually mutate S3. Without this flag the script is a dry run.')
    args = ap.parse_args()

    names_uc = [n.upper() for n in args.names]
    print(f"[archive] target names (case-insensitive contains): {names_uc}")
    print(f"[archive] bucket: {S3_BUCKET}")
    print(f"[archive] mode:   {'APPLY' if args.apply else 'DRY-RUN (use --apply to execute)'}")
    print()

    s3 = boto3.client('s3')

    live_idx    = _load_index(s3, S3_INDEX_KEY)
    archive_idx = _load_index(s3, S3_ARCHIVE_INDEX_KEY)

    live_runs    = list(live_idx.get('runs') or [])
    archive_runs = list(archive_idx.get('runs') or [])

    keep   = [r for r in live_runs if not _matches(r, names_uc)]
    remove = [r for r in live_runs if     _matches(r, names_uc)]

    print(f"[archive] live index has {len(live_runs)} runs; {len(remove)} match.")
    if not remove:
        print("[archive] nothing to archive. exiting.")
        return

    print()
    print("[archive] runs that will be archived:")
    for r in remove:
        print(f"   - {r.get('project_name','?'):14s}  target={r.get('target','?'):24s}  key={r.get('key','?')}")
    print()

    archived_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    moved_runs = []

    for r in remove:
        live_key = r.get('key') or ''
        if not live_key:
            print(f"   [skip] no key for {r.get('project_name')}")
            continue
        new_key = _archive_key_for(live_key)
        if args.apply and live_key != new_key:
            try:
                s3.copy_object(
                    Bucket=S3_BUCKET,
                    Key=new_key,
                    CopySource={'Bucket': S3_BUCKET, 'Key': live_key},
                    MetadataDirective='COPY',
                )
                s3.delete_object(Bucket=S3_BUCKET, Key=live_key)
                print(f"   [moved] {live_key}")
                print(f"        → {new_key}")
            except Exception as e:
                print(f"   [error] failed to move {live_key} → {new_key}: {e}")
                continue
        elif not args.apply:
            print(f"   [would move] {live_key}  →  {new_key}")

        new_entry = dict(r)
        new_entry['key']         = new_key
        new_entry['archived']    = True
        new_entry['archived_at'] = archived_at
        moved_runs.append(new_entry)

    # Dedupe archive index by new_key, then prepend new entries.
    new_archive_keys = {r['key'] for r in moved_runs}
    deduped = [r for r in archive_runs if r.get('key') not in new_archive_keys]
    deduped.extend(moved_runs)

    new_live_idx    = {'runs': keep}
    new_archive_idx = {'runs': deduped}

    print()
    print(f"[archive] live index: {len(live_runs)} → {len(keep)}")
    print(f"[archive] archive index: {len(archive_runs)} → {len(deduped)}")

    if args.apply:
        _save_index(s3, S3_INDEX_KEY, new_live_idx)
        _save_index(s3, S3_ARCHIVE_INDEX_KEY, new_archive_idx)
        print()
        print(f"[archive] ✓ wrote s3://{S3_BUCKET}/{S3_INDEX_KEY}")
        print(f"[archive] ✓ wrote s3://{S3_BUCKET}/{S3_ARCHIVE_INDEX_KEY}")
    else:
        print()
        print("[archive] DRY-RUN — no S3 writes. Re-run with --apply to execute.")


if __name__ == '__main__':
    main()
