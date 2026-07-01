"""Apply the pre-2021 data-availability disclaimer to SVOD CSVs.

Rule (per analyst direction, 2026-06-08):
  Subscriber-IQ panel data only goes back to 2021-01-01. For any show whose
  episodes aired before that date, the CSV header's "Analysis Date Range"
  must read "2021-01-01 to 2025-12-31" (the panel window we actually have
  data for) and the Episode Dates tab must surface a disclaimer:

      "Episodes tracked were watched after the original air date due to
       availability of data."

What this script does, per CSV in s3://svod-acquisition/:
  1. Scan the PER-EPISODE / PER-DATE attribution rows to find the earliest
     air date.
  2. If that earliest date is < 2021-01-01:
       a. Replace the "Analysis Date Range,,,<old>,,,,,," row with the
          fixed panel window 2021-01-01 to 2025-12-31.
       b. Insert (or update) a new header row right after Analysis Date
          Range: "Episode Date Availability Note,,,<disclaimer>,,,,,,".
          The dashboard parser keys off this exact label and surfaces it
          in the Episode Dates tab via _episode_dates_availability_note.
  3. Re-upload.

Idempotent: re-running won't append duplicate disclaimer rows or
double-rewrite the date range.

Usage:
  cd bg-webapp && python3 scripts/apply_pre_2021_disclaimer.py --dry-run
  cd bg-webapp && python3 scripts/apply_pre_2021_disclaimer.py
"""
from __future__ import annotations

import argparse
import csv as _csv
import io
import os
import re
from datetime import datetime
from pathlib import Path

import boto3

BUCKET = os.environ.get("SVOD_BUCKET", "svod-acquisition")
PANEL_START = "2021-01-01"
PANEL_END   = "2025-12-31"
DISCLAIMER  = (
    "Episodes tracked were watched after the original air date due to "
    "availability of data."
)
NOTE_LABEL  = "Episode Date Availability Note"


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y")


def _parse_any_date(s: str) -> datetime | None:
    s = (s or "").strip().strip('"')
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def earliest_episode_date(text: str) -> datetime | None:
    """Find the earliest air date across PER-EPISODE / PER-DATE rows."""
    in_section = False
    earliest: datetime | None = None
    for ln in text.splitlines():
        if "PER-EPISODE ATTRIBUTION" in ln or "PER-DATE ATTRIBUTION" in ln:
            in_section = True
            continue
        if not in_section:
            continue
        if "ATTRIBUTION SUMMARY" in ln or "SIGNUP TIMING (Days After" in ln:
            break
        try:
            parts = next(_csv.reader([ln]))
        except Exception:
            continue
        if not parts:
            continue
        # Episode N row: date in col 1. PER-DATE row: date in col 0.
        for candidate in (parts[1] if len(parts) > 1 else "",
                          parts[0] if parts else ""):
            d = _parse_any_date(candidate)
            if d is None:
                continue
            if earliest is None or d < earliest:
                earliest = d
            break
    return earliest


def _csv_row(label: str, value: str) -> str:
    """Header rows follow the 10-column convention: <label>,,,<value>,,,,,,\n"""
    # Quote value if it contains commas or quotes
    if "," in value or '"' in value:
        value = '"' + value.replace('"', '""') + '"'
    return f"{label},,,{value},,,,,,\n"


def patch_csv(text: str) -> tuple[str, bool, str]:
    """Apply the disclaimer + canonical panel range.

    Returns (new_text, modified, reason).
    """
    lines = text.splitlines(keepends=True)
    adr_idx = None
    existing_note_idx = None
    for i, ln in enumerate(lines[:30]):
        low = ln.lower()
        if low.startswith("analysis date range"):
            adr_idx = i
        elif low.startswith(NOTE_LABEL.lower()):
            existing_note_idx = i
    if adr_idx is None:
        return text, False, "no_analysis_date_range_row"

    # 1. Replace the Analysis Date Range row in place (preserves line ending).
    target_value = f"{PANEL_START} to {PANEL_END}"
    line_ending = "\r\n" if lines[adr_idx].endswith("\r\n") else "\n"
    new_adr = f"Analysis Date Range,,,{target_value},,,,,,{line_ending}"
    already_correct_range = (lines[adr_idx] == new_adr)
    lines[adr_idx] = new_adr

    # 2. Insert or update the disclaimer row right after Analysis Date Range.
    new_note = _csv_row(NOTE_LABEL, DISCLAIMER)
    if line_ending == "\r\n":
        new_note = new_note.replace("\n", "\r\n")
    if existing_note_idx is not None:
        already_correct_note = (lines[existing_note_idx] == new_note)
        lines[existing_note_idx] = new_note
    else:
        already_correct_note = False
        lines.insert(adr_idx + 1, new_note)

    new_text = "".join(lines)
    modified = (new_text != text)
    reason = "updated" if modified else (
        "noop_already_applied" if already_correct_range and already_correct_note else "no_diff"
    )
    return new_text, modified, reason


def process_one(s3, key: str, dry_run: bool) -> dict:
    res = {"key": key, "status": "?", "earliest": None, "error": None}
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8-sig", errors="replace")

        earliest = earliest_episode_date(text)
        if earliest is None:
            res["status"] = "skip_no_episodes"
            return res
        res["earliest"] = earliest.strftime("%Y-%m-%d")
        if earliest >= datetime(2021, 1, 1):
            res["status"] = "skip_post_2021"
            return res

        new_text, modified, reason = patch_csv(text)
        if not modified:
            res["status"] = reason
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
    ap.add_argument("--filter", default="")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    pager = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in pager.paginate(Bucket=BUCKET):
        for o in (page.get("Contents") or []):
            k = o["Key"]
            if k.endswith(".csv") and ("/" not in k or k.startswith("purgatory/")):
                if not args.filter or args.filter.lower() in k.lower():
                    keys.append(k)
    print(f"📦 Scanning {len(keys)} CSVs — mode: "
          f"{'DRY-RUN' if args.dry_run else 'APPLY'}\n")

    results = []
    for i, k in enumerate(keys, 1):
        r = process_one(s3, k, args.dry_run)
        results.append(r)
        if r["status"] in ("updated", "dry_run_ok", "noop_already_applied"):
            icon = "✅"
        elif r["status"].startswith("skip"):
            icon = "⚪"
        elif r["status"] == "error":
            icon = "💥"
        else:
            icon = "?"
        if r["status"].startswith("skip"):
            # Don't spam the console for the >90 unaffected files
            continue
        print(f"  [{i:3d}/{len(keys)}] {icon} {r['status']:<22} "
              f"earliest={r['earliest'] or '?'}  {k}")
        if r.get("error"):
            print(f"                    └─ {r['error']}")

    print()
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("📊 SUMMARY")
    for st, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"   {st:<22} {n}")


if __name__ == "__main__":
    main()
