"""Normalize the "Content Cadence" header row across all SVOD CSVs.

Historically the pipeline emitted both "All at Once" and "Binge" for shows
where every episode dropped on the same day. Per analyst direction
(2026-06-08) the canonical label is now "Binge". This script rewrites the
header row in-place on s3://svod-acquisition/ for every CSV that still
shows "All at Once".

Idempotent: CSVs already saying "Binge" (or any other distinct cadence,
e.g. "Weekly", "Single Event Telecast", "2-episode premiere then weekly")
are left untouched.

Pipeline forward-compat: run_synthetic_attribution and the dashboard form
validators now silently normalize incoming "All at Once" → "Binge", so
new runs will only ever land as "Binge". This script exists to clean up
the 12 existing CSVs that pre-date that change.

Usage:
  cd bg-webapp && python3 scripts/normalize_cadence_labels.py --dry-run
  cd bg-webapp && python3 scripts/normalize_cadence_labels.py
"""
from __future__ import annotations

import argparse
import csv as _csv
import os
from typing import Tuple

import boto3

BUCKET = os.environ.get("SVOD_BUCKET", "svod-acquisition")
OLD_LABEL = "All at Once"
NEW_LABEL = "Binge"


def rewrite_cadence(text: str) -> Tuple[str, bool]:
    """Replace the cadence value in the "Content Cadence" header row only.

    We deliberately parse line-by-line and only touch rows whose first
    column is "Content Cadence" (case-insensitive). A naive global
    string-replace would also rewrite any "All at Once" text that
    happened to appear elsewhere (e.g. in a Claude-research narrative
    embedded in the CSV).
    """
    out_lines = []
    changed = False
    for ln in text.splitlines(keepends=True):
        # Cheap pre-filter to avoid CSV-parsing every row.
        if not ln.lower().startswith("content cadence"):
            out_lines.append(ln)
            continue
        try:
            row = next(_csv.reader([ln.rstrip("\r\n")]))
        except Exception:
            out_lines.append(ln)
            continue
        # The expected schema is: ("Content Cadence", "", "", <value>, "", "", "", "", "", "").
        # Walk columns 1..end looking for the cadence value.
        target_idx = None
        for i in range(1, len(row)):
            if row[i].strip() == OLD_LABEL:
                target_idx = i
                break
        if target_idx is None:
            out_lines.append(ln)
            continue
        row[target_idx] = NEW_LABEL
        # Re-emit using csv.writer so quoting matches the rest of the file.
        import io
        buf = io.StringIO()
        _csv.writer(buf, lineterminator="").writerow(row)
        new_ln = buf.getvalue()
        # Preserve original line ending.
        new_ln += "\r\n" if ln.endswith("\r\n") else "\n"
        out_lines.append(new_ln)
        changed = True
    return ("".join(out_lines), changed)


def process_one(s3, key: str, dry_run: bool) -> dict:
    res = {"key": key, "status": "?", "error": None}
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8-sig", errors="replace")
        new_text, changed = rewrite_cadence(text)
        if not changed:
            res["status"] = "skip_no_match"
            return res
        if dry_run:
            res["status"] = "dry_run_ok"
            return res
        s3.put_object(Bucket=BUCKET, Key=key,
                      Body=new_text.encode("utf-8"),
                      ContentType="text/csv")
        res["status"] = "updated"
        return res
    except Exception as e:
        res["status"] = "error"
        res["error"] = f"{type(e).__name__}: {e}"
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    pager = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in pager.paginate(Bucket=BUCKET):
        for o in (page.get("Contents") or []):
            k = o["Key"]
            if k.endswith(".csv") and ("/" not in k or k.startswith("purgatory/")):
                keys.append(k)
    print(f"📦 Scanning {len(keys)} CSVs for cadence='{OLD_LABEL}' "
          f"→ '{NEW_LABEL}' — mode: "
          f"{'DRY-RUN' if args.dry_run else 'APPLY'}\n")

    results = []
    for k in keys:
        r = process_one(s3, k, args.dry_run)
        results.append(r)
        if r["status"].startswith("skip"):
            continue
        icon = "✅" if r["status"] in ("updated", "dry_run_ok") else "💥"
        print(f"  {icon} {r['status']:<14} {k}")
        if r.get("error"):
            print(f"     └─ {r['error']}")

    print()
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("📊 SUMMARY")
    for st, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"   {st:<14} {n}")


if __name__ == "__main__":
    main()
