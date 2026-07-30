"""Unarchive Journey IQ runs back onto the live dashboard.

Reverse of `archive_journey_iq_runs.py`. For every matching entry in
`journey-iq/_archive_index.json`, moves the payload out of
`journey-iq/archive/` back to `journey-iq/admin/` (splitting the
`user__file` glue back into `user/file` when applicable), strips the
`archived` / `archived_at` flags, and re-inserts it into
`journey-iq/_index.json` so the live dashboard picks it up again.

Matching: case-insensitive contains on `project_name` OR `target`
(same rule the archive script uses).

Usage:
    # dry run (default — prints what would change)
    python -m migration.unarchive_journey_iq_runs --names POPCULTUREJEOPARDY

    # actually do it
    python -m migration.unarchive_journey_iq_runs --names POPCULTUREJEOPARDY --apply

    # multiple names
    python -m migration.unarchive_journey_iq_runs --names HUNGRY OBSESSION --apply

Requires AWS creds in env / ~/.aws/credentials and the same
`dashboard-inputs` bucket the archive script writes to.
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

DEFAULT_NAMES = ['POPCULTUREJEOPARDY']


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


def _live_key_for(archive_key: str) -> str:
    """Reverse of archive_journey_iq_runs._archive_key_for.

    Maps:
      journey-iq/archive/foo.json.gz              → journey-iq/admin/foo.json.gz
      journey-iq/archive/<user>__<file>.json.gz   → journey-iq/<user>/<file>.json.gz

    If the key is NOT under the archive prefix, it's returned unchanged so
    the caller can skip the copy step.
    """
    if not archive_key.startswith(S3_ARCHIVE_PREFIX):
        return archive_key
    rest = archive_key[len(S3_ARCHIVE_PREFIX):]
    # If the basename has the "user__file" glue, split it back to user/file.
    # Only split on the FIRST '__' so filenames containing '__' aren't
    # accidentally re-partitioned.
    if '__' in rest:
        user, _, tail = rest.partition('__')
        return f"{S3_PREFIX}{user}/{tail}"
    # Default: assume it originated under admin/ (matches how new payloads
    # get written today).
    return f"{S3_ADMIN_PREFIX}{rest}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', nargs='+', default=DEFAULT_NAMES,
                    help='Project names to unarchive (case-insensitive contains match).')
    ap.add_argument('--apply', action='store_true',
                    help='Actually mutate S3. Without this flag the script is a dry run.')
    args = ap.parse_args()

    names_uc = [n.upper() for n in args.names]
    print(f"[unarchive] target names (case-insensitive contains): {names_uc}")
    print(f"[unarchive] bucket: {S3_BUCKET}")
    print(f"[unarchive] mode:   {'APPLY' if args.apply else 'DRY-RUN (use --apply to execute)'}")
    print()

    s3 = boto3.client('s3')

    live_idx    = _load_index(s3, S3_INDEX_KEY)
    archive_idx = _load_index(s3, S3_ARCHIVE_INDEX_KEY)

    live_runs    = list(live_idx.get('runs') or [])
    archive_runs = list(archive_idx.get('runs') or [])

    keep_archive    = [r for r in archive_runs if not _matches(r, names_uc)]
    restore         = [r for r in archive_runs if     _matches(r, names_uc)]

    print(f"[unarchive] archive index has {len(archive_runs)} runs; {len(restore)} match.")
    if not restore:
        print("[unarchive] nothing to restore. exiting.")
        return

    print()
    print("[unarchive] runs that will be restored to LIVE:")
    for r in restore:
        print(f"   - {r.get('project_name','?'):24s}  target={r.get('target','?'):24s}  key={r.get('key','?')}")
    print()

    unarchived_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    restored_runs = []

    for r in restore:
        arch_key = r.get('key') or ''
        if not arch_key:
            print(f"   [skip] no key for {r.get('project_name')}")
            continue
        new_key = _live_key_for(arch_key)
        if args.apply and arch_key != new_key:
            try:
                s3.copy_object(
                    Bucket=S3_BUCKET,
                    Key=new_key,
                    CopySource={'Bucket': S3_BUCKET, 'Key': arch_key},
                    MetadataDirective='COPY',
                )
                s3.delete_object(Bucket=S3_BUCKET, Key=arch_key)
                print(f"   [moved] {arch_key}")
                print(f"        → {new_key}")
            except Exception as e:
                print(f"   [error] failed to move {arch_key} → {new_key}: {e}")
                continue
        elif not args.apply:
            print(f"   [would move] {arch_key}  →  {new_key}")

        new_entry = dict(r)
        new_entry['key'] = new_key
        new_entry.pop('archived',      None)
        new_entry.pop('archived_at',   None)
        new_entry['unarchived_at']     = unarchived_at
        restored_runs.append(new_entry)

    # Dedupe live index by new_key, then append restored entries at the end
    # so the run picker still surfaces most-recent-first via the existing
    # sort in list_runs().
    restored_keys = {r['key'] for r in restored_runs}
    deduped_live  = [r for r in live_runs if r.get('key') not in restored_keys]
    deduped_live.extend(restored_runs)

    new_live_idx    = {'runs': deduped_live}
    new_archive_idx = {'runs': keep_archive}

    print()
    print(f"[unarchive] live index:    {len(live_runs)} → {len(deduped_live)}")
    print(f"[unarchive] archive index: {len(archive_runs)} → {len(keep_archive)}")

    if args.apply:
        _save_index(s3, S3_INDEX_KEY, new_live_idx)
        _save_index(s3, S3_ARCHIVE_INDEX_KEY, new_archive_idx)
        print()
        print(f"[unarchive] ✓ wrote s3://{S3_BUCKET}/{S3_INDEX_KEY}")
        print(f"[unarchive] ✓ wrote s3://{S3_BUCKET}/{S3_ARCHIVE_INDEX_KEY}")
    else:
        print()
        print("[unarchive] DRY-RUN — no S3 writes. Re-run with --apply to execute.")


if __name__ == '__main__':
    main()
