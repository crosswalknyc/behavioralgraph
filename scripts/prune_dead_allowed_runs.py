#!/usr/bin/env python3
"""Prune allowed_runs entries whose profile no longer exists.

Seats that are not on the ``["*"]`` wildcard accumulate one allowed_runs entry
per profile ever created, and nothing removes them when a profile is deleted.
As of 2026-09-29 that was 23,590 dead entries across 49 seats. They cost real
time on every /api/jobs request, because the post-fix access check is
O(profiles the seat cannot open x length of its run list).

A key is pruned only when ALL THREE of these hold:

  1. it is not in the live catalog (system/s3_cache.json), and
  2. no object with that exact key exists in the bucket, and
  3. no object anywhere in the bucket shares its filename.

Rule 3 is the one that matters. Without it this would strip grants for files
that were swept or reverted and still sit under ``_backups/...``, which are
recoverable. On the 2026-09-29 catalog it held back 540 keys.

Dry run (default, writes nothing):
    python3 scripts/prune_dead_allowed_runs.py

Apply, after backing users.json up to S3 and to disk:
    python3 scripts/prune_dead_allowed_runs.py --apply

Run log
    2026-09-29  removed 1,431 keys / 23,590 entries, 12.69 MB -> 11.58 MB.
                Verified against the backup: 0 seats lost or gained access to
                a live profile, no field other than allowed_runs changed.
                Backup: system/backups/users.json.20260929_173731.pre_prune

This is a cleanup, not a fix. auto_add_runs_to_all_users still appends every
new profile to every non-wildcard seat and nothing removes deleted ones, so
the lists start growing again immediately and this needs re-running.
"""
import argparse
import collections
import datetime
import json
import os
import sys

import boto3
import botocore.exceptions

BUCKET = "dashboard-inputs"
USERS_KEY = "system/users.json"
CATALOG_KEY = "system/s3_cache.json"


def _load(s3, key):
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    body = obj["Body"].read()
    return json.loads(body), (obj.get("ETag") or "").strip('"'), body


def _index_bucket(s3):
    """Every object key in the bucket, plus a filename -> keys index."""
    keys = set()
    by_base = collections.defaultdict(list)
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for o in page.get("Contents", []):
            k = o["Key"]
            keys.add(k)
            by_base[os.path.basename(k)].append(k)
    return keys, by_base


def _classify(granted, live, all_keys, by_base):
    """Split granted keys into prune / held, with a reason for each hold."""
    prune, held = [], []
    for key in sorted(granted):
        if key in live:
            continue  # in the catalog; not a candidate at all
        if key in all_keys:
            held.append((key, granted[key], "object still exists in S3"))
            continue
        twins = [t for t in by_base.get(os.path.basename(key), []) if t != key]
        if twins:
            held.append((key, granted[key],
                         "same filename exists at %s" % twins[0]))
            continue
        prune.append(key)
    return prune, held


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the pruned users.json back to S3")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    users_doc, users_etag, users_raw = _load(s3, USERS_KEY)
    catalog, _, _ = _load(s3, CATALOG_KEY)
    users = users_doc.get("users", {})
    live = {j.get("s3_key") for j in catalog.get("jobs", [])
            if isinstance(j, dict) and j.get("s3_key")}

    print("users.json  : %.2f MB, %d seats, ETag %s"
          % (len(users_raw) / 1e6, len(users), users_etag))
    print("live catalog: %d profiles" % len(live))
    print("indexing bucket ...")
    all_keys, by_base = _index_bucket(s3)
    print("bucket      : %s objects" % format(len(all_keys), ","))

    # Only non-wildcard seats carry explicit lists.
    listed = {n: u for n, u in users.items()
              if isinstance(u, dict)
              and isinstance(u.get("allowed_runs"), list)
              and "*" not in u["allowed_runs"]}
    granted = collections.Counter()
    for u in listed.values():
        for k in u["allowed_runs"]:
            granted[k] += 1

    prune, held = _classify(granted, live, all_keys, by_base)
    prune_set = set(prune)

    assert not (prune_set & live), "refusing to prune a live profile"

    removed_total = sum(granted[k] for k in prune)
    print()
    print("distinct granted keys        : %s" % format(len(granted), ","))
    print("  in live catalog (untouched): %s"
          % format(sum(1 for k in granted if k in live), ","))
    print("  SAFE TO PRUNE              : %s  (%s seat entries)"
          % (format(len(prune), ","), format(removed_total, ",")))
    print("  held back by a safety rule : %s" % format(len(held), ","))
    print("  live profiles affected     : 0")

    if held:
        print()
        print("held back (showing 8):")
        for k, c, why in sorted(held, key=lambda r: -r[1])[:8]:
            print("   %-50s x%-4d %s" % (k[:50], c, why[:46]))

    print()
    print("per-seat effect (showing 12 largest):")
    effects = []
    for n, u in listed.items():
        before = len(u["allowed_runs"])
        after = sum(1 for k in u["allowed_runs"] if k not in prune_set)
        effects.append((before - after, n, before, after))
    for drop, n, before, after in sorted(effects, reverse=True)[:12]:
        print("   %-16s %5d -> %5d  (-%d)" % (n, before, after, drop))

    if not args.apply:
        print()
        print("DRY RUN - nothing written. Re-run with --apply to write.")
        return 0

    # Before mutating, prove our serializer reproduces the file exactly.
    # If it does not, the diff we write would carry unrelated churn (key
    # order, spacing, coerced values) and we should not be the one writing.
    if json.dumps(users_doc, indent=2, default=str).encode("utf-8") != users_raw:
        print()
        print("ABORTED: re-serializing users.json does not reproduce the "
              "original bytes, so a write would change more than the prune.")
        return 1

    # Back up before touching anything.
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_key = "system/backups/users.json.%s.pre_prune" % stamp
    local = "/tmp/users.json.%s.pre_prune" % stamp
    with open(local, "wb") as fh:
        fh.write(users_raw)
    s3.put_object(Bucket=BUCKET, Key=backup_key, Body=users_raw,
                  ContentType="application/json")
    print()
    print("backup -> s3://%s/%s" % (BUCKET, backup_key))
    print("backup -> %s" % local)

    for u in listed.values():
        u["allowed_runs"] = [k for k in u["allowed_runs"]
                             if k not in prune_set]

    # Match app.py's _s3_json_cas_update byte for byte: same serialization,
    # and the same IfMatch conditional put. A worker rewrites users.json on
    # nearly every request, so an unconditional write would clobber whatever
    # landed since our read. On a conflict we bail rather than retry, because
    # re-running is cheap and a blind retry would re-prune against a snapshot
    # the operator never saw.
    body = json.dumps(users_doc, indent=2, default=str).encode("utf-8")
    try:
        s3.put_object(Bucket=BUCKET, Key=USERS_KEY, Body=body,
                      ContentType="application/json", IfMatch=users_etag)
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == "PreconditionFailed" or status == 412:
            print()
            print("ABORTED: users.json changed under us since ETag %s. "
                  "Nothing written; re-run." % users_etag)
            return 1
        raise

    print("wrote users.json: %.2f MB -> %.2f MB (removed %s entries)"
          % (len(users_raw) / 1e6, len(body) / 1e6,
             format(removed_total, ",")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
