#!/usr/bin/env python3
"""Repair two pipeline defects in SVOD CSVs uploaded to s3://svod-acquisition/.

Defect 1 — COMPETITIVE PLATFORMS section not sorted descending
------------------------------------------------------------------
The pipeline's `_build_synthetic_competitive` in SVOD_Churn_Attribution.py
returns a DataFrame in whatever order Claude's dict iterator produced,
and `write_output` iterates that DataFrame without sorting. Result: the
rendered "COMPETITIVE PLATFORMS (% of Show Watchers)" section on the
dashboard shows Apple TV+ sitting between Peacock and Paramount+ instead
of between Disney+ and Peacock, etc. Affects 147/267 CSVs.

Fix: read the block bounded by
  ",,COMPETITIVE PLATFORMS (% of Show Watchers),,,,,,,"
  ...next non-platform-row blank/section header...
and re-emit its platform rows sorted by the Percentage column (col 8)
descending.

Defect 2 — "New Platform Signups" and "1st Touchpoint" Gen Pop projection
rounded away from the true attributed+reactivated sum
------------------------------------------------------------------------
The pipeline computes New Platform Signups panel count as
  new_signups_panel = int(clean_sample_panel * conversion_pct / 100)
and derives its Gen Pop from an alternate proportional path
(clean_sample_GP × conversion_pct), which typically produces a value
several (1-20) below the SUM of Attributed Signups GP + Dormant to
Reactive GP. Then `1st Touchpoint` is aliased to NPS GP (write_output
line ~5434), so it inherits the same rounded-down value. Meanwhile
`TOTAL SIGNUPS` GP is set from the sum. Result: on the Bear S5 CSV,
NPS row shows 75,600, 1st Touchpoint shows 75,600, but TOTAL SIGNUPS
shows 75,613. Dashboard displays 75,600 (the rounded value) as
"Total Reactivated and Acquired Accounts" -- the user's complaint.
Affects 115/267 CSVs (typical delta ±1-20).

Fix: after both rows are written, compute the canonical value as
  Attributed Signups GP + Dormant to Reactive GP
and force NPS GP + 1st Touchpoint GP + TOTAL SIGNUPS GP to that value.
This preserves the analyst's row-by-row conversion and reactivation
splits (they still sum to the correct total) but eliminates the
double-rounding gap.

Usage
-----
    python3 scripts/repair_svod_csvs.py --key <s3_key>          # one file
    python3 scripts/repair_svod_csvs.py --all                   # every CSV
    python3 scripts/repair_svod_csvs.py --all --dry-run         # audit only

Idempotent: re-running on an already-repaired file is a no-op.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from typing import List, Tuple

import boto3

BUCKET = "svod-acquisition"

COMPETITIVE_HEADER = "COMPETITIVE PLATFORMS (% of Show Watchers)"


def _parse_gp(cell: str) -> int | None:
    s = cell.strip().replace(",", "").strip('"')
    if not s or not s.replace("-", "").isdigit():
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _fmt_gp(n: int) -> str:
    return f"{int(round(n)):,}"


def _row_is_platform(row: List[str]) -> bool:
    """A COMPETITIVE PLATFORMS row: platform name in col 0, percent in col 8."""
    if len(row) < 10:
        return False
    if not row[0].strip():
        return False
    section_marker = row[2].strip() if len(row) > 2 else ""
    if section_marker:  # header/section rows have text in col 2
        return False
    pct = row[8].strip()
    return pct.endswith("%")


def repair_rows(rows: List[List[str]]) -> Tuple[List[List[str]], dict]:
    """Repair both defects in-place on a row list. Returns (new_rows, stats)."""
    stats = {
        "platforms_sorted": False,
        "signups_reconciled": False,
        "nps_before": None,
        "nps_after": None,
        "touch1_before": None,
        "touch1_after": None,
        "total_before": None,
        "total_after": None,
    }

    # ── Step 1: Locate COMPETITIVE PLATFORMS block and sort it ──
    plat_start_idx = None
    for i, r in enumerate(rows):
        if len(r) > 2 and COMPETITIVE_HEADER in r[2]:
            plat_start_idx = i
            break

    if plat_start_idx is not None:
        # collect consecutive platform rows starting the row after the header
        plat_rows_idx: list[int] = []
        j = plat_start_idx + 1
        while j < len(rows) and _row_is_platform(rows[j]):
            plat_rows_idx.append(j)
            j += 1
        if len(plat_rows_idx) >= 2:
            block = [rows[k] for k in plat_rows_idx]
            def _pct_val(row: list[str]) -> float:
                try:
                    return float(row[8].strip().rstrip("%"))
                except (ValueError, IndexError):
                    return 0.0
            sorted_block = sorted(block, key=lambda r: -_pct_val(r))
            if block != sorted_block:
                for k, new_row in zip(plat_rows_idx, sorted_block):
                    rows[k] = new_row
                stats["platforms_sorted"] = True

    # ── Step 2: Reconcile New Platform Signups + 1st Touchpoint + TOTAL SIGNUPS ──
    attr_gp = dorm_gp = None
    nps_idx = touch1_idx = total_idx = None
    attr_idx = dorm_idx = None

    for i, r in enumerate(rows):
        if len(r) < 10:
            continue
        cat = r[0].strip()
        if cat == "New Platform Signups":
            nps_idx = i
        elif cat == "Attributed Signups":
            attr_idx = i
            attr_gp = _parse_gp(r[9])
        elif cat == "Dormant to Reactive":
            dorm_idx = i
            dorm_gp = _parse_gp(r[9])
        elif cat == "TOTAL SIGNUPS":
            total_idx = i
        elif cat == "1st Touchpoint":
            touch1_idx = i

    if attr_gp is not None and dorm_gp is not None:
        canonical_gp = attr_gp + dorm_gp
        canonical_str = _fmt_gp(canonical_gp)

        def _set(idx: int, key: str) -> None:
            if idx is None:
                return
            before = _parse_gp(rows[idx][9])
            if before != canonical_gp:
                stats[f"{key}_before"] = before
                stats[f"{key}_after"] = canonical_gp
                stats["signups_reconciled"] = True
                # Ensure the row has 10 columns and set col 9 (Gen Pop Projection)
                while len(rows[idx]) < 10:
                    rows[idx].append("")
                rows[idx][9] = canonical_str

        _set(nps_idx, "nps")
        _set(touch1_idx, "touch1")
        _set(total_idx, "total")

    return rows, stats


def repair_key(s3, key: str, dry_run: bool = False) -> dict:
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    body = obj["Body"].read().decode("utf-8")

    reader = csv.reader(io.StringIO(body))
    rows = [list(r) for r in reader]

    original_rows = [list(r) for r in rows]
    new_rows, stats = repair_rows(rows)

    if not stats["platforms_sorted"] and not stats["signups_reconciled"]:
        stats["action"] = "no-op"
        return stats

    if dry_run:
        stats["action"] = "would-repair"
        return stats

    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_MINIMAL)
    for r in new_rows:
        writer.writerow(r)

    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=out.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )
    stats["action"] = "repaired"
    return stats


def list_all_csvs(s3) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kw = {"Bucket": BUCKET}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            k = o["Key"]
            if k.endswith(".csv") and "/" not in k:
                keys.append(k)
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return sorted(keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--key", help="Single S3 key under svod-acquisition/")
    grp.add_argument("--all", action="store_true", help="Repair every CSV in bucket root")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s3 = boto3.client("s3")

    if args.key:
        keys = [args.key]
    else:
        keys = list_all_csvs(s3)

    print(f"Repairing {len(keys)} CSV(s){' (DRY-RUN)' if args.dry_run else ''}")
    print()

    action_counts = {"no-op": 0, "repaired": 0, "would-repair": 0, "error": 0}
    sample_reports: list[str] = []
    for i, k in enumerate(keys, 1):
        try:
            st = repair_key(s3, k, dry_run=args.dry_run)
        except Exception as e:
            action_counts["error"] += 1
            print(f"  [{i}/{len(keys)}] ERR {k}: {e}")
            continue
        act = st.get("action", "no-op")
        action_counts[act] = action_counts.get(act, 0) + 1
        if act in ("repaired", "would-repair") and len(sample_reports) < 12:
            parts = []
            if st["platforms_sorted"]:
                parts.append("platforms-sorted")
            if st["signups_reconciled"]:
                nps_b, nps_a = st.get("nps_before"), st.get("nps_after")
                if nps_b is not None and nps_a is not None:
                    parts.append(f"NPS {nps_b:,}→{nps_a:,}")
            sample_reports.append(f"  [{i}/{len(keys)}] {act:12s} {k[:56]:<56}  {' | '.join(parts)}")

    for r in sample_reports:
        print(r)

    print()
    print(f"{'Action':<15} Count")
    for act, cnt in sorted(action_counts.items(), key=lambda x: -x[1]):
        if cnt:
            print(f"{act:<15} {cnt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
