#!/usr/bin/env python3
"""In-place sweep of s3://svod-acquisition/ tracker CSVs for the three
2026-08-24 defect classes:

  1. round monthly platform totals (trailing zero / tier constants),
  2. touchpoint components not summing exactly to their printed total,
  3. any displayed integer count or projection ending in 0
     (no-round-numbers-in-deliverables workspace rule),

plus reconciliation invariants (pre-existing + clean = watchers,
attributed + dormant = signups = TOTAL SIGNUPS, demographics anchored to
signups) and COMPETITIVE PLATFORMS descending rank order.

Shares its fix implementation with the engine final pass:
bg-webapp/svod_output_hygiene.py (SVOD_Churn_Attribution.write_output
calls the same passes on df_out right before to_csv).

Safety properties:
  * deterministic and idempotent (re-running on a patched corpus no-ops)
  * every patched file is backed up first to
    historic/<name>.csv.pre_round_reconcile_<YYYYMMDD>
  * a file is only patched when its parsed rows re-serialize
    byte-identically to the shipped object (so only defective cells can
    differ in the uploaded bytes); files that fail that round-trip are
    flagged for manual attention and left untouched
  * every patched file is re-audited (second hygiene pass must report
    zero changes) before upload
  * non-tracker CSVs and .research.json sidecars are never touched

Usage:
  python3 scripts/sweep_svod_round_reconcile.py --dry-run   # audit only
  python3 scripts/sweep_svod_round_reconcile.py             # audit + patch
  python3 scripts/sweep_svod_round_reconcile.py --only KEY  # single file
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from svod_output_hygiene import process_rows  # noqa: E402

BUCKET = "svod-acquisition"
SKIP_PREFIXES = ("historic/", "_backups/", "purgatory/")
BACKUP_TAG = "pre_round_reconcile"


def list_root_csvs(s3):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.startswith(SKIP_PREFIXES):
                continue
            if not k.endswith(".csv"):
                continue
            keys.append(k)
    return sorted(keys)


def serialize(rows, terminator):
    buf = io.StringIO()
    csv.writer(buf, lineterminator=terminator).writerows(rows)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="audit and report only, never write to S3")
    ap.add_argument("--only", help="process a single S3 key")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    keys = [args.only] if args.only else list_root_csvs(s3)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    audited = 0
    patched = []
    skipped_roundtrip = []
    non_tracker = []
    flagged = {}
    klass_files = Counter()
    klass_cells = Counter()

    for key in keys:
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        text = raw.decode("utf-8")
        terminator = "\r\n" if "\r\n" in text else "\n"
        all_rows = list(csv.reader(io.StringIO(text)))
        if not all_rows:
            continue
        audited += 1
        header, data = all_rows[0], all_rows[1:]

        new_data, report = process_rows(data)
        if not report["is_tracker"]:
            non_tracker.append(key)
            continue
        if report["flags"]:
            flagged[key] = report["flags"]
        if not report["changes"]:
            continue

        # Round-trip guard: parsed rows must reproduce the shipped bytes
        # exactly, so the upload can only differ in the defective cells.
        if serialize([header] + data, terminator) != text:
            skipped_roundtrip.append(key)
            continue

        # Re-audit the patched rows: a second pass must be a no-op.
        _, report2 = process_rows(new_data)
        if report2["changes"]:
            flagged.setdefault(key, []).append(
                f"NOT IDEMPOTENT after patch ({len(report2['changes'])} residual "
                f"changes) - left untouched")
            continue

        per_file_klass = Counter(ch["klass"] for ch in report["changes"])
        for kl, cnt in per_file_klass.items():
            klass_files[kl] += 1
            klass_cells[kl] += cnt

        print(f"\n{key}: {len(report['changes'])} cell change(s)")
        for ch in report["changes"]:
            print(f"  [{ch['klass']}] {ch['label']}: {ch['before']} -> {ch['after']}")
        for fl in report.get("flags", []):
            print(f"  note: {fl}")

        if args.dry_run:
            patched.append(key)
            continue

        backup_key = f"historic/{key}.{BACKUP_TAG}_{stamp}"
        s3.put_object(Bucket=BUCKET, Key=backup_key, Body=raw,
                      ContentType="text/csv")
        body = serialize([header] + new_data, terminator).encode("utf-8")
        s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                      ContentType="text/csv")
        print(f"  backed up -> s3://{BUCKET}/{backup_key}")
        print(f"  uploaded patched file ({len(body)} bytes)")
        patched.append(key)

    print("\n" + "=" * 70)
    mode = "DRY RUN - would patch" if args.dry_run else "patched"
    print(f"audited {audited} CSVs; {mode} {len(patched)}")
    print("\nper-class totals (files / cells):")
    for kl in sorted(klass_cells):
        print(f"  {kl}: {klass_files[kl]} files, {klass_cells[kl]} cells")
    if non_tracker:
        print(f"\nnon-tracker CSVs skipped ({len(non_tracker)}):")
        for k in non_tracker:
            print(f"  {k}")
    if skipped_roundtrip:
        print(f"\nfiles with violations SKIPPED (round-trip not byte-identical, "
              f"needs manual attention) ({len(skipped_roundtrip)}):")
        for k in skipped_roundtrip:
            print(f"  {k}")
    if flagged:
        print(f"\nstructural notes (cells deliberately left as shipped) "
              f"({len(flagged)} files):")
        for k, fls in sorted(flagged.items()):
            for fl in fls:
                print(f"  {k}: {fl}")
    if patched:
        print(f"\npatched files ({len(patched)}):")
        for k in patched:
            print(f"  {k}")


if __name__ == "__main__":
    main()
