"""Backfill the COMPETITIVE PLATFORMS section in every SVOD CSV in S3.

Why this exists:
  Audit on 2026-06-08 showed 6 clusters of CSVs sharing identical 7-platform
  cross-platform overlap (e.g. Grimsburg / Krapopolis / Alien: Earth all
  inherited the same Hulu-tier default of NFLX 58.10%, AMZN 48.20%, …). The
  root cause: the main Claude research prompt asks for `competitive_overlap`
  in the same JSON as reach + demographics + signup drivers, and Claude
  returns null for that field ~80% of the time because panel-level overlap
  isn't web-searchable. The pipeline then drops to the platform-tier default
  table, which is identical for every show on the same home platform.

  SVOD_Churn_Attribution._research_competitive_overlap_focused() now produces
  show-differentiated overlap via a single tight Claude call (no web search,
  genre-aware anchors, +/-2pp show-name hash jitter). This script re-runs
  that function for every CSV already in S3 and swaps the COMPETITIVE
  PLATFORMS rows in-place.

Usage (dry run first):
  cd bg-webapp && python3 scripts/backfill_competitive_overlap.py --dry-run

Apply:
  cd bg-webapp && python3 scripts/backfill_competitive_overlap.py
"""
from __future__ import annotations

import argparse
import csv as _csv
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3

# Load .env so ANTHROPIC_API_KEY/USE_CLAUDE_REASONING are present
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if ENV_PATH.exists():
    for _line in ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        k, v = _line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
# Pipeline reads this flag to decide whether to make any Claude calls.
os.environ.setdefault("USE_CLAUDE_REASONING", "1")

# Pipeline module lives one level up from this script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from SVOD_Churn_Attribution import _research_competitive_overlap_focused  # noqa: E402

BUCKET = os.environ.get("SVOD_BUCKET", "svod-acquisition")


def parse_header(text: str) -> dict:
    """Pull show_name / platform / genre / context out of the CSV header rows."""
    info = {"show": None, "platform": None, "genre": None}
    rdr = _csv.reader(io.StringIO(text))
    for i, row in enumerate(rdr):
        if i > 25:
            break
        if not row:
            continue
        label = (row[0] or "").strip().lower()
        # Row format is: <label>,,,<value>,,,,,,
        val = ""
        for cell in row[1:]:
            c = (cell or "").strip()
            if c:
                val = c
                break
        if not val:
            continue
        if label.startswith("show/content tracked"):
            info["show"] = val
        elif label.startswith("platform tracked"):
            info["platform"] = val
        elif label.startswith("genre"):
            info["genre"] = val
    return info


_COMP_HEADER_RE = re.compile(r"COMPETITIVE PLATFORMS", re.IGNORECASE)


def replace_competitive_section(csv_text: str, overlap: dict) -> tuple[str, int]:
    """Rewrite the COMPETITIVE PLATFORMS rows with the new overlap dict.

    Preserves everything else byte-for-byte where possible (we only rewrite
    the platform-percent lines inside the section, not the section header or
    surrounding blank rows).

    Returns (new_text, rows_replaced). If the section is not found, returns
    (original_text, 0) — the caller can decide whether to append or skip.
    """
    lines = csv_text.splitlines(keepends=True)
    start_idx = None
    for i, ln in enumerate(lines):
        if _COMP_HEADER_RE.search(ln):
            start_idx = i
            break
    if start_idx is None:
        return csv_text, 0

    # The header line is something like:
    #   ,,COMPETITIVE PLATFORMS (% of Show Watchers),,,,,,,
    # Followed immediately by the platform rows, then a blank/comma-only row.
    # We delete every row from start_idx+1 up to (but not including) the
    # next blank/comma-only row, then insert our fresh rows.
    j = start_idx + 1
    while j < len(lines):
        stripped = lines[j].strip().strip(",").strip()
        if not stripped:
            break
        j += 1
    # `start_idx+1 .. j-1` are the old platform rows.
    removed = j - (start_idx + 1)

    # Build new rows. CSV column layout (from inspection of existing files):
    # 10 columns: Category,Episode Date,Count,Count Label,Secondary Count,
    # Secondary Label,Tertiary Count,Tertiary Label,Percentage,Gen Pop Projection
    # Competitive rows put platform name in col 0 and pct in col 8.
    line_terminator = "\n"
    sample = lines[start_idx]
    if sample.endswith("\r\n"):
        line_terminator = "\r\n"
    elif sample.endswith("\r"):
        line_terminator = "\r"

    # Sort by descending overlap pct for readable output
    items = sorted(overlap.items(), key=lambda kv: -float(kv[1]))
    new_rows = []
    for platform, pct in items:
        # 10 columns, percent in col 9 (index 8). Format "%.2f%%".
        name_upper = platform.upper()
        pct_str = f"{float(pct):.2f}%"
        row = f"{name_upper},,,,,,,,{pct_str},{line_terminator}"
        new_rows.append(row)

    new_lines = lines[: start_idx + 1] + new_rows + lines[j:]
    return "".join(new_lines), removed


def process_one(s3, key: str, dry_run: bool) -> dict:
    """Re-run focused overlap for a single S3 CSV and (optionally) re-upload.

    Returns a status dict for the run summary.
    """
    result = {"key": key, "status": "?", "show": None, "platform": None,
              "rows_replaced": 0, "new_overlap": None, "error": None}
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        # CSVs are UTF-8 with windows-style line endings sometimes
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8-sig", errors="replace")

        hdr = parse_header(text)
        result["show"] = hdr.get("show")
        result["platform"] = hdr.get("platform")
        if not hdr.get("show") or not hdr.get("platform"):
            result["status"] = "skipped_no_header"
            return result

        # Build a small context note from the genre row so Claude has a hint
        # even when the show name alone is ambiguous.
        ctx = f"genre={hdr.get('genre')}" if hdr.get("genre") else None

        focused = _research_competitive_overlap_focused(
            show_name=hdr["show"],
            platform_name=hdr["platform"],
            context_note=ctx,
            research=None,
        )
        if not focused or not focused.get("overlap"):
            result["status"] = "skipped_no_overlap"
            return result

        # Drop the home platform from the overlap (defensive — the prompt
        # already says to exclude it but Claude sometimes still emits it).
        home = (hdr["platform"] or "").strip().lower()
        overlap = {k: v for k, v in focused["overlap"].items()
                   if k.lower() != home}
        result["new_overlap"] = overlap

        new_text, removed = replace_competitive_section(text, overlap)
        result["rows_replaced"] = removed
        if not removed:
            result["status"] = "no_section_found"
            return result

        if dry_run:
            result["status"] = "dry_run_ok"
            return result

        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=new_text.encode("utf-8"),
            ContentType="text/csv",
        )
        result["status"] = "updated"
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="compute new overlap + diff but skip the S3 upload")
    ap.add_argument("--max-workers", type=int, default=6,
                    help="parallel Claude calls (Anthropic rate-limits at ~50 RPM)")
    ap.add_argument("--filter", default="",
                    help="only process keys containing this substring")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    # Paginate so we don't silently miss files past MaxKeys=2000.
    paginator = s3.get_paginator("list_objects_v2")
    all_objs: list[dict] = []
    for page in paginator.paginate(Bucket=BUCKET):
        all_objs.extend(page.get("Contents") or [])
    # Include root-level CSVs AND purgatory/*.csv (admin-review queue) — both
    # are surfaced in the dashboard once promoted, so both need correct
    # overlap. Skip historic/ (legacy snapshots, not actively served).
    keys = [
        o["Key"] for o in all_objs
        if o["Key"].endswith(".csv") and (
            "/" not in o["Key"]
            or o["Key"].startswith("purgatory/")
        )
    ]
    if args.filter:
        keys = [k for k in keys if args.filter.lower() in k.lower()]

    print(f"📦 Bucket: {BUCKET}  |  CSVs to process: {len(keys)}  "
          f"|  mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    if args.filter:
        print(f"   filter: {args.filter!r}")
    print()

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(process_one, s3, k, args.dry_run): k for k in keys}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            stat = r["status"]
            short = r["key"][:60]
            if stat in ("updated", "dry_run_ok"):
                sample = r.get("new_overlap") or {}
                top3 = sorted(sample.items(), key=lambda kv: -kv[1])[:3]
                top3_s = ", ".join(f"{p}={v:.1f}" for p, v in top3)
                print(f"   [{i:3d}/{len(keys)}] ✅ {stat:<12} {short:<60} → {top3_s}")
            else:
                print(f"   [{i:3d}/{len(keys)}] ⚠️  {stat:<14} {short:<60} {r.get('error') or ''}")

    print()
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("📊 SUMMARY")
    for st, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"   {st:<20} {n}")
    print()

    # Re-cluster to verify dedup. After backfill there should be ~92 unique
    # signatures (one per show) instead of the previous 78.
    sigs = set()
    for r in results:
        ov = r.get("new_overlap")
        if isinstance(ov, dict):
            sigs.add(tuple(sorted((k, round(float(v), 2)) for k, v in ov.items())))
    print(f"🧬 Unique overlap signatures across {len(results)} files: {len(sigs)}")


if __name__ == "__main__":
    main()
