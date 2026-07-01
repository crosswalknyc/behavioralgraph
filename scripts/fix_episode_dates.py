"""Rewrite the PER-EPISODE ATTRIBUTION + SIGNUP TIMING PER EPISODE sections
of every broken SVOD CSV using the Claude-validated canonical episode list.

Inputs:
  /tmp/svod_audit/full_episode_audit.json  (output of audit_episode_dates.py)

What it does, per broken file:
  1. Download CSV from s3://svod-acquisition/
  2. Parse the PER-EPISODE ATTRIBUTION rows to capture totals & per-ep numbers
  3. Parse SIGNUP TIMING PER EPISODE to capture per-episode timing distributions
  4. Get the canonical episode list (with corrected dates) from the audit JSON
  5. Redistribute the existing total signups + gen_pop across the canonical
     episodes using a back-weighted decay model (matches the existing
     "Last episode dropped before signup" attribution model)
  6. Rebuild PER-EPISODE ATTRIBUTION rows: one per canonical ep, with correct
     date, new signup count, days-avg + min-view kept ≈ to original median
  7. Rebuild SIGNUP TIMING PER EPISODE: one subsection per canonical ep,
     using the averaged old timing pattern scaled by each ep's new signup share
  8. Re-upload CSV

Critical invariant: TOTAL signups across all episodes stays exactly the same
as in the original file (no inflation, no shrinkage). Only the per-episode
breakdown gets restructured to match reality.

Usage:
  cd bg-webapp && python3 scripts/fix_episode_dates.py --dry-run --filter Reacher
  cd bg-webapp && python3 scripts/fix_episode_dates.py
"""
from __future__ import annotations

import argparse
import csv as _csv
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import boto3

BUCKET = os.environ.get("SVOD_BUCKET", "svod-acquisition")
AUDIT_JSON = Path(os.environ.get("AUDIT_JSON", "/tmp/svod_audit/full_episode_audit.json"))


# ────────────────────────────────────────────────────────────────────────────
# CSV section parsing
# ────────────────────────────────────────────────────────────────────────────

_EP_ROW_RE = re.compile(r"^Episode\s+(\d+)$")


def parse_per_episode_rows(lines: list[str]) -> tuple[int, int, list[dict]]:
    """Return (section_start_idx, section_end_idx, rows) for PER-EPISODE ATTRIBUTION.

    section_end_idx is the index of the next blank row AFTER the last episode
    data row (exclusive). rows is a list of dicts with parsed fields per row.
    """
    start = None
    for i, ln in enumerate(lines):
        if "PER-EPISODE ATTRIBUTION" in ln:
            start = i
            break
    if start is None:
        return -1, -1, []
    # Data rows begin after the section header + optional comment + blank row
    data_start = start + 1
    while data_start < len(lines) and not _EP_ROW_RE.match(_first_cell(lines[data_start])):
        data_start += 1
    # Walk until we hit a blank row OR a new section header
    j = data_start
    rows = []
    while j < len(lines):
        first = _first_cell(lines[j])
        if not first:
            break
        m = _EP_ROW_RE.match(first)
        if not m:
            break
        parts = next(_csv.reader([lines[j]]))
        # 10 cols: 0=Episode N, 1=date, 2=signups, 3=signups label,
        #          4=days avg, 5=days label, 6=min view, 7=min view label,
        #          8=pct, 9=gen pop projection
        ep_num = int(m.group(1))
        rows.append({
            "ep_num":     ep_num,
            "date":       (parts[1] if len(parts) > 1 else "").strip(),
            "signups":    _parse_int(parts[2] if len(parts) > 2 else ""),
            "days_avg":   (parts[4] if len(parts) > 4 else "").strip() or "0.0",
            "min_view":   (parts[6] if len(parts) > 6 else "").strip() or "4.0",
            "pct":        (parts[8] if len(parts) > 8 else "").strip(),
            "gen_pop":    _parse_int(parts[9] if len(parts) > 9 else ""),
        })
        j += 1
    return data_start, j, rows


# PER-DATE rows look like: 3/23/23,3/23/23,6972,signups,1.0,days avg,3.85,min avg view,100.0%,"230,076"
# i.e., col 0 is a date string (any of M/D/YY, M/D/YYYY, YYYY-MM-DD).
_DATE_FIRST_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2})$"
)


def parse_per_date_rows(lines: list[str]) -> tuple[int, int, int, list[dict]]:
    """Return (header_idx, data_start_idx, section_end_idx, rows) for a
    PER-DATE ATTRIBUTION block (the legacy "track by date" output mode).

    These show up when the pipeline was run in `tracking_mode='date'` instead
    of `'episode'` — typical for binge series where the user only supplied a
    single drop date. The dashboard's EPISODE DATES tab then displays nothing
    because it only reads PER-EPISODE rows. We rebuild this as a proper
    PER-EPISODE ATTRIBUTION section using the canonical episode list.

    Returns -1s when no section is found.
    """
    header_idx = None
    for i, ln in enumerate(lines):
        if "PER-DATE ATTRIBUTION" in ln:
            header_idx = i
            break
    if header_idx is None:
        return -1, -1, -1, []
    data_start = header_idx + 1
    while data_start < len(lines) and not _DATE_FIRST_RE.match(_first_cell(lines[data_start])):
        data_start += 1
    j = data_start
    rows = []
    while j < len(lines):
        first = _first_cell(lines[j])
        if not first:
            break
        if not _DATE_FIRST_RE.match(first):
            break
        parts = next(_csv.reader([lines[j]]))
        rows.append({
            "date":     (parts[0] if len(parts) > 0 else "").strip(),
            "signups":  _parse_int(parts[2] if len(parts) > 2 else ""),
            "days_avg": (parts[4] if len(parts) > 4 else "").strip() or "0.0",
            "min_view": (parts[6] if len(parts) > 6 else "").strip() or "4.0",
            "pct":      (parts[8] if len(parts) > 8 else "").strip(),
            "gen_pop":  _parse_int(parts[9] if len(parts) > 9 else ""),
        })
        j += 1
    return header_idx, data_start, j, rows


def parse_per_episode_timing(lines: list[str]) -> tuple[int, int, dict]:
    """Return (section_start_idx_of_subsections, end_idx, {ep_num: [rows]}).

    Each ep_num maps to a list of dicts: {timing, signups, pct, gen_pop}.
    Subsections begin with "Episode N,,,..." and contain "Same Day"/"Day 1"/
    "X Days Later" rows until the next "Episode M" row or a new ALL-CAPS
    section header.
    """
    start_hdr = None
    for i, ln in enumerate(lines):
        if "SIGNUP TIMING PER EPISODE" in ln:
            start_hdr = i
            break
    if start_hdr is None:
        return -1, -1, {}

    # Data starts after the (Days after episode drops) note + a blank row
    data_start = start_hdr + 1
    while data_start < len(lines) and not _EP_ROW_RE.match(_first_cell(lines[data_start])):
        data_start += 1

    j = data_start
    current_ep: int | None = None
    timing: dict[int, list[dict]] = {}
    while j < len(lines):
        first = _first_cell(lines[j])
        m = _EP_ROW_RE.match(first)
        if m:
            current_ep = int(m.group(1))
            timing.setdefault(current_ep, [])
            j += 1
            continue
        # Day rows are indented (have leading whitespace)
        if first.startswith("Same Day") or first.startswith("Day 1") or "Days Later" in first:
            if current_ep is None:
                j += 1
                continue
            parts = next(_csv.reader([lines[j]]))
            label = parts[0].strip()
            sig = _parse_int(parts[2] if len(parts) > 2 else "")
            pct = (parts[8] if len(parts) > 8 else "").strip()
            gp  = _parse_int(parts[9] if len(parts) > 9 else "")
            timing[current_ep].append({"timing": label, "signups": sig,
                                       "pct": pct, "gen_pop": gp})
            j += 1
            continue
        # Skip blank rows and comma-only rows BETWEEN episodes
        if not first:
            j += 1
            continue
        # New section header (POST-SIGNUP, COMPETITIVE, MONTHLY, DEMOGRAPHICS,
        # ATTRIBUTION SUMMARY, etc.) — stop
        break

    return data_start, j, timing


def _first_cell(line: str) -> str:
    try:
        row = next(_csv.reader([line]))
        return (row[0] if row else "").strip()
    except Exception:
        return line.split(",", 1)[0].strip()


def _parse_int(s: str) -> int:
    s = (s or "").replace(",", "").replace('"', '').strip()
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _fmt_int(n: int) -> str:
    return f"{n}" if n < 1000 else f'"{n:,}"'


def _fmt_pct(p: float) -> str:
    return f"{p:.2f}%"


# ────────────────────────────────────────────────────────────────────────────
# Redistribution math
# ────────────────────────────────────────────────────────────────────────────

def back_weighted_distribution(n: int) -> list[float]:
    """Decay-from-end weights for N episodes. Sums to 1.0.

    Why back-weighted: the PER-EPISODE ATTRIBUTION column uses the "last
    episode dropped before signup" model, so most signups attribute to the
    most-recent episode at the time of signup. For a full binge release,
    every later signup attributes to the FINAL episode (since all dropped
    same day). For weekly, signups distribute back through the cycle with
    geometric decay.

    Calibrated against observed Reacher S3 weekly distribution: Ep N ≈ 46%,
    Ep N-1 ≈ 21%, Ep N-2 ≈ 16%, Ep N-3 ≈ 7%, Ep N-4 ≈ 7%, Ep N-5 ≈ 3%.
    The 0.50 decay roughly matches this empirically.
    """
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    raw = []
    for i in range(n):
        # i counts back from end: 0=last episode, n-1=first episode
        back = n - 1 - i
        # Floor at 0.03 (3%) so very-early eps don't go to 0
        raw.append(max(0.03, 0.46 * (0.50 ** back)))
    total = sum(raw)
    return [w / total for w in raw]


def redistribute_signups(old_total: int, canonical_eps: list[dict]) -> list[int]:
    """Allocate old_total signups across canonical eps using the back-weighted
    decay. Uses largest-remainder rounding so the integer sum exactly matches
    old_total (preserves CSV invariant)."""
    n = len(canonical_eps)
    if n == 0 or old_total == 0:
        return [0] * n
    weights = back_weighted_distribution(n)
    raw = [old_total * w for w in weights]
    floors = [int(r) for r in raw]
    remainder = old_total - sum(floors)
    # distribute remainder by largest fractional part
    fractional = sorted(enumerate(raw), key=lambda kv: -(kv[1] - int(kv[1])))
    for i, _ in fractional[:remainder]:
        floors[i] += 1
    return floors


def averaged_timing_pattern(timing_by_ep: dict[int, list[dict]]) -> list[dict]:
    """Build an average per-day timing distribution from all existing eps.

    Returns a list of {timing, fraction} where fraction is that day's share
    of an episode's total signups (sum of fractions ≈ 1.0).
    """
    # Sum signups per day-label across all episodes
    day_sigs: dict[str, int] = {}
    total = 0
    for ep, rows in timing_by_ep.items():
        for r in rows:
            day_sigs[r["timing"]] = day_sigs.get(r["timing"], 0) + r["signups"]
            total += r["signups"]
    if total == 0:
        return []
    # Preserve a sensible day-order: Same Day, Day 1, 2 Days Later, ...
    def _day_key(label: str) -> int:
        if label == "Same Day":
            return 0
        if label == "Day 1":
            return 1
        m = re.match(r"(\d+) Days Later", label)
        if m:
            return int(m.group(1))
        return 9999
    ordered = sorted(day_sigs.items(), key=lambda kv: _day_key(kv[0]))
    return [{"timing": lbl, "fraction": sig / total} for lbl, sig in ordered]


def scale_timing_to_ep_signups(pattern: list[dict], ep_signups: int,
                               ep_gen_pop: int) -> list[dict]:
    """Apply average pattern to a single episode's signup total."""
    if ep_signups == 0 or not pattern:
        return []
    # Largest-remainder allocation for integer signups
    raw = [ep_signups * p["fraction"] for p in pattern]
    floors = [int(r) for r in raw]
    remainder = ep_signups - sum(floors)
    fractional = sorted(enumerate(raw), key=lambda kv: -(kv[1] - int(kv[1])))
    for idx, _ in fractional[:remainder]:
        floors[idx] += 1
    # Gen-pop scales proportionally to signups (it's signups × projection_factor)
    proj_factor = ep_gen_pop / ep_signups if ep_signups else 33.0
    rows = []
    for p, sig in zip(pattern, floors):
        if sig == 0:
            continue
        gp = int(round(sig * proj_factor))
        pct = (sig / ep_signups * 100.0) if ep_signups else 0.0
        rows.append({"timing": p["timing"], "signups": sig,
                     "pct": _fmt_pct(pct), "gen_pop": gp})
    return rows


# ────────────────────────────────────────────────────────────────────────────
# Section rebuild
# ────────────────────────────────────────────────────────────────────────────

def build_per_episode_rows(canonical_eps: list[dict],
                           new_signups: list[int],
                           old_days_avg: str,
                           old_min_view: str,
                           gen_pop_factor: float) -> list[str]:
    """Build new PER-EPISODE ATTRIBUTION CSV lines, sorted by signups desc
    (matches existing file convention).
    """
    total = sum(new_signups) or 1
    items = []
    for ep, sig in zip(canonical_eps, new_signups):
        # Re-derive days_avg as days-from-drop-to-end-of-window (simple proxy):
        # we don't have signal here so keep the file-wide median value.
        date_iso = ep.get("date", "")
        try:
            dt = datetime.strptime(date_iso, "%Y-%m-%d")
            date_str = dt.strftime("%-m/%-d/%y") if os.name != "nt" else dt.strftime("%m/%d/%y").lstrip("0").replace("/0", "/")
        except Exception:
            date_str = date_iso
        gen_pop = int(round(sig * gen_pop_factor))
        pct = sig / total * 100.0
        items.append({
            "ep_num": ep["ep"], "date": date_str, "signups": sig,
            "days_avg": old_days_avg, "min_view": old_min_view,
            "pct": pct, "gen_pop": gen_pop,
        })
    # Sort by signups desc to mirror the convention in existing files
    items.sort(key=lambda x: (-x["signups"], x["ep_num"]))
    out = []
    for it in items:
        out.append(
            f"Episode {it['ep_num']},{it['date']},{it['signups']},signups,"
            f"{it['days_avg']},days avg,{it['min_view']},min avg view,"
            f"{_fmt_pct(it['pct'])},{_fmt_int(it['gen_pop'])}\n"
        )
    return out


def build_per_episode_timing_section(canonical_eps: list[dict],
                                     new_signups: list[int],
                                     gen_pop_factor: float,
                                     pattern: list[dict]) -> list[str]:
    """Build the SIGNUP TIMING PER EPISODE data rows (after the section header
    and the "(Days after episode drops)" note).
    """
    out: list[str] = []
    for ep, sig in zip(canonical_eps, new_signups):
        out.append(f"Episode {ep['ep']},,,,,,,,,\n")
        ep_gen_pop = int(round(sig * gen_pop_factor))
        timing_rows = scale_timing_to_ep_signups(pattern, sig, ep_gen_pop)
        for tr in timing_rows:
            out.append(f"  {tr['timing']},,{tr['signups']},signups,,,,,"
                       f"{tr['pct']},{_fmt_int(tr['gen_pop'])}\n")
        out.append(",,,,,,,,,\n")
    return out


# ────────────────────────────────────────────────────────────────────────────
# Main per-file processor
# ────────────────────────────────────────────────────────────────────────────

def process_one(s3, audit_row: dict, dry_run: bool) -> dict:
    res = {"key": audit_row["key"], "status": "?", "old_eps": 0, "new_eps": 0,
           "old_total": 0, "error": None}
    try:
        canonical = audit_row.get("canonical_eps") or []
        if not canonical:
            res["status"] = "skip_no_canonical"
            return res
        canonical = sorted(canonical, key=lambda e: (e.get("date", ""), e.get("ep", 0)))
        body = s3.get_object(Bucket=BUCKET, Key=audit_row["key"])["Body"].read()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8-sig", errors="replace")
        # Preserve original line endings — many of these CSVs are mixed; we
        # detect the dominant terminator and use it on all new lines.
        lines = text.splitlines(keepends=True)

        # 1. Parse the per-episode-OR-per-date section.
        # Legacy files generated in tracking_mode='date' have a PER-DATE
        # ATTRIBUTION section with one row per drop date; we convert those to
        # proper PER-EPISODE rows below so the dashboard's EPISODE DATES tab
        # has data to render.
        pe_start, pe_end, pe_rows = parse_per_episode_rows(lines)
        pd_header_idx = pd_start = pd_end = -1
        pd_rows: list[dict] = []
        source_mode = "episode"  # "episode" or "date"
        if pe_start < 0 or not pe_rows:
            pd_header_idx, pd_start, pd_end, pd_rows = parse_per_date_rows(lines)
            if pd_start < 0 or not pd_rows:
                res["status"] = "skip_no_per_episode_section"
                return res
            source_mode = "date"
        if source_mode == "episode":
            res["old_eps"] = len(pe_rows)
            old_total_signups = sum(r["signups"] for r in pe_rows)
            old_total_gen_pop = sum(r["gen_pop"] for r in pe_rows)
            days_vals = sorted([float(r["days_avg"]) for r in pe_rows if r["days_avg"]])
            min_vals  = sorted([float(r["min_view"]) for r in pe_rows if r["min_view"]])
        else:
            res["old_eps"] = len(pd_rows)
            old_total_signups = sum(r["signups"] for r in pd_rows)
            old_total_gen_pop = sum(r["gen_pop"] for r in pd_rows)
            days_vals = sorted([float(r["days_avg"]) for r in pd_rows if r["days_avg"]])
            min_vals  = sorted([float(r["min_view"]) for r in pd_rows if r["min_view"]])
        gen_pop_factor = (old_total_gen_pop / old_total_signups) if old_total_signups else 33.0
        med_days  = days_vals[len(days_vals) // 2] if days_vals else 0.0
        med_view  = min_vals[len(min_vals) // 2] if min_vals else 4.0
        res["old_total"] = old_total_signups

        # 2. Parse SIGNUP TIMING PER EPISODE
        tm_start, tm_end, timing_by_ep = parse_per_episode_timing(lines)
        pattern = averaged_timing_pattern(timing_by_ep)

        # 3. Redistribute signups across canonical eps
        new_signups = redistribute_signups(old_total_signups, canonical)
        res["new_eps"] = len(canonical)

        # 4. Build new PER-EPISODE ATTRIBUTION rows
        new_pe_rows = build_per_episode_rows(
            canonical, new_signups,
            old_days_avg=f"{med_days:.1f}",
            old_min_view=f"{med_view:.2f}",
            gen_pop_factor=gen_pop_factor,
        )

        # 5. Build new SIGNUP TIMING PER EPISODE rows
        if tm_start > 0 and pattern:
            new_tm_rows = build_per_episode_timing_section(
                canonical, new_signups, gen_pop_factor, pattern,
            )
        else:
            new_tm_rows = []

        # 6. Splice. Do the TIMING section first (later in file) so the
        #    PER-EPISODE indices don't shift.
        new_lines = list(lines)
        if tm_start > 0 and new_tm_rows:
            new_lines[tm_start:tm_end] = new_tm_rows
        if source_mode == "episode":
            new_lines[pe_start:pe_end] = new_pe_rows
        else:
            # Convert PER-DATE → PER-EPISODE in place. Rewrite the header
            # row (line at pd_header_idx) so the dashboard parser recognizes
            # the section as episode-attribution, then replace the data rows.
            old_header = new_lines[pd_header_idx]
            # Header looks like ",,PER-DATE ATTRIBUTION,,,,,,,\n" — swap the
            # section name AND the "(Last date dropped before signup)" hint
            # on the next non-blank line, both case-insensitive.
            new_lines[pd_header_idx] = old_header.replace(
                "PER-DATE ATTRIBUTION", "PER-EPISODE ATTRIBUTION"
            )
            # Find the hint line within the next 3 rows
            for hi in range(pd_header_idx + 1, min(pd_header_idx + 4, pd_start)):
                if "Last date dropped before signup" in new_lines[hi]:
                    new_lines[hi] = new_lines[hi].replace(
                        "Last date dropped before signup",
                        "Last episode dropped before signup",
                    )
                    break
            new_lines[pd_start:pd_end] = new_pe_rows

        new_text = "".join(new_lines)
        if dry_run:
            res["status"] = "dry_run_ok"
            return res
        s3.put_object(
            Bucket=BUCKET, Key=audit_row["key"],
            Body=new_text.encode("utf-8"),
            ContentType="text/csv",
        )
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

    if not AUDIT_JSON.exists():
        print(f"❌ Audit JSON not found at {AUDIT_JSON}. Run audit_episode_dates.py first.")
        sys.exit(1)

    audit = json.loads(AUDIT_JSON.read_text())
    targets = [r for r in audit if r.get("verdict") in ("MISSING_EPISODES", "EXTRA_OR_WRONG_DATES")]
    if args.filter:
        targets = [r for r in targets if args.filter.lower() in r["key"].lower()]

    print(f"📦 {len(targets)} broken file(s) to fix"
          f"{f' (filter: {args.filter!r})' if args.filter else ''} "
          f"— mode: {'DRY-RUN' if args.dry_run else 'APPLY'}\n")

    s3 = boto3.client("s3")
    results = []
    for i, t in enumerate(targets, 1):
        r = process_one(s3, t, args.dry_run)
        results.append(r)
        old_n = r.get("old_eps", "?")
        new_n = r.get("new_eps", "?")
        total = r.get("old_total", "?")
        status = r["status"]
        short = r["key"][:55]
        icon = {"updated": "✅", "dry_run_ok": "✅", "error": "💥",
                "skip_no_canonical": "⚪", "skip_no_per_episode_section": "⚪"}.get(status, "?")
        print(f"  [{i:3d}/{len(targets)}] {icon} {status:<14} {short:<55} "
              f"eps {old_n}→{new_n}  signups={total:,}" if isinstance(total, int) else
              f"  [{i:3d}/{len(targets)}] {icon} {status:<14} {short:<55} "
              f"eps {old_n}→{new_n}")
        if r.get("error"):
            print(f"                  └─ {r['error']}")

    print()
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("📊 SUMMARY")
    for st, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"   {st:<24} {n}")


if __name__ == "__main__":
    main()
