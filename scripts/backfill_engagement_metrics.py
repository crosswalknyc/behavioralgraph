#!/usr/bin/env python3
"""Backfill Completion Rate + Second Screen Activity engagement KPIs across
every CSV in s3://svod-acquisition/.

These two metrics were added to the synthetic pipeline on 2026-06-18, so
fresh runs already include them in the KEY METRICS section. This script
walks every legacy CSV in the bucket and:

  1. Skips files that already have both rows (idempotent).
  2. Parses the title's metadata (show name, platform, genre, cadence,
     episode count, is-movie heuristic) from the CSV header.
  3. Calls _research_engagement_metrics() — the same per-title Claude
     Sonnet 4.5 research function the live pipeline uses — to get the
     two percentages plus reasoning + cited sources.
  4. Splices two new rows into the CSV's KEY METRICS section immediately
     after "Total Show Conversion Rate".
  5. Re-uploads the modified CSV.
  6. Merges the engagement research into the .research.json sidecar
     (creating one if it doesn't exist) so the audit trail is preserved.

Parallelism: ThreadPoolExecutor with conservative max_workers (Claude
Sonnet 4.5 messages API rate-limits at ~50 RPM at the messages tier).

Usage:
    # Pilot mode (process 3 files, dry-run shows what would change):
    python3 scripts/backfill_engagement_metrics.py --limit 3 --dry-run

    # Pilot mode (process 3 files, actually upload):
    python3 scripts/backfill_engagement_metrics.py --limit 3

    # Full backfill:
    python3 scripts/backfill_engagement_metrics.py

    # Resume / retry only files missing engagement rows:
    python3 scripts/backfill_engagement_metrics.py   # (idempotent)
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv as _csv
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import boto3

# Make SVOD_Churn_Attribution importable when run from anywhere.
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

# Ensure Claude reasoning is on for the imported research function.
os.environ.setdefault("USE_CLAUDE_REASONING", "1")

from SVOD_Churn_Attribution import _research_engagement_metrics  # noqa: E402

BUCKET = "svod-acquisition"
s3 = boto3.client("s3")


# ─── CSV header parsing ────────────────────────────────────────────────

def _csv_field(row: list[str], idx: int) -> str:
    if idx < len(row):
        return row[idx].strip()
    return ""


def parse_metadata(csv_text: str) -> dict:
    """Extract the fields _research_engagement_metrics() needs from a CSV.

    Returns dict with: show_name, platform_name, genre, content_cadence,
    episode_count, is_movie, release_date, has_engagement_rows.
    """
    show = platform = genre = cadence = ""
    release = None
    episode_dates: list[str] = []
    in_per_episode = False
    has_completion = has_second_screen = False

    reader = _csv.reader(io.StringIO(csv_text))
    for row in reader:
        if not row:
            continue
        c0 = _csv_field(row, 0)
        c1 = _csv_field(row, 1)
        c2 = _csv_field(row, 2)
        c3 = _csv_field(row, 3)
        upper_strip = c0.strip().upper()
        combined_upper = (c0 + " " + c1 + " " + c2).upper()

        if c0 == "Show/Content Tracked":
            show = c3
        elif c0 == "Platform Tracked":
            platform = c3
        elif c0.startswith("Genre"):
            genre = c3
        elif c0.startswith("Content Cadence"):
            cadence = c3
        elif c0 == "Analysis Date Range":
            # Use the start date as release_date hint (falls back to first
            # episode air date if missing later).
            m = re.match(r"(\d{4}-\d{2}-\d{2})", c3)
            if m:
                release = m.group(1)
        elif c0.strip() == "Completion Rate":
            has_completion = True
        elif c0.strip() == "Second Screen Activity":
            has_second_screen = True
        elif (
            "PER-EPISODE ATTRIBUTION" in upper_strip
            or "PER-EPISODE ATTRIBUTION" in combined_upper
            or "PER-DATE ATTRIBUTION" in upper_strip
            or "PER-DATE ATTRIBUTION" in combined_upper
        ):
            in_per_episode = True
        elif "ATTRIBUTION SUMMARY" in upper_strip or "ATTRIBUTION SUMMARY" in combined_upper:
            in_per_episode = False
        elif in_per_episode:
            # Count episodes — both "Episode N" rows AND date-form rows.
            label = c0.strip()
            ep_date = c1.strip()
            if label.startswith("Episode "):
                episode_dates.append(ep_date)
            elif ep_date and re.match(r"\d+/\d+/\d+", ep_date):
                # Has a date column → it's an episode row
                episode_dates.append(ep_date)

    episode_count = len([d for d in episode_dates if d])
    if episode_count == 0:
        episode_count = 1  # treat as single-piece content

    genre_lc = (genre or "").lower()
    is_movie = ("movie" in genre_lc) or ("film" in genre_lc) or (episode_count <= 1)

    return {
        "show_name": show,
        "platform_name": platform,
        "genre": genre,
        "content_cadence": cadence,
        "episode_count": episode_count,
        "is_movie": is_movie,
        "release_date": release,
        "has_engagement_rows": has_completion and has_second_screen,
    }


# ─── CSV row splicing ──────────────────────────────────────────────────

_TOTAL_CONV_LINE_RE = re.compile(r"^Total Show Conversion Rate,", re.MULTILINE)


def splice_engagement_rows(csv_text: str, completion_pct: float, second_screen_pct: float) -> str:
    """Insert "Completion Rate" + "Second Screen Activity" rows immediately
    after the "Total Show Conversion Rate" row. Preserves the file's
    original line endings (LF vs CRLF) and trailing newline.
    """
    # Detect line ending.
    newline = "\r\n" if "\r\n" in csv_text else "\n"
    trailing = csv_text.endswith(newline)
    lines = csv_text.split(newline)
    if trailing:
        # When we split a file ending in \n, the last element is "" — drop it
        # and re-add at join time.
        lines = lines[:-1]

    out: list[str] = []
    inserted = False
    for ln in lines:
        out.append(ln)
        if not inserted and ln.startswith("Total Show Conversion Rate,"):
            out.append(f"Completion Rate,,,,,,,,{completion_pct:.1f}%,")
            out.append(f"Second Screen Activity,,,,,,,,{second_screen_pct:.1f}%,")
            inserted = True

    if not inserted:
        # No "Total Show Conversion Rate" row to anchor against — fall back to
        # inserting before the first blank line after KEY METRICS, or just
        # before the PER-EPISODE section.
        anchored = False
        new_out: list[str] = []
        in_key_metrics = False
        for ln in out:
            if "KEY METRICS" in ln.upper():
                in_key_metrics = True
            if in_key_metrics and not anchored and (
                ln.startswith("Average Days")
                or ln.upper().startswith(",,PER-")
                or ln.upper().startswith(",,ATTRIBUTION")
            ):
                new_out.append(f"Completion Rate,,,,,,,,{completion_pct:.1f}%,")
                new_out.append(f"Second Screen Activity,,,,,,,,{second_screen_pct:.1f}%,")
                anchored = True
            new_out.append(ln)
        if anchored:
            out = new_out
            inserted = True

    return newline.join(out) + (newline if trailing else "")


# ─── Sidecar JSON update ───────────────────────────────────────────────


def update_sidecar(key: str, engagement: dict) -> tuple[str, str]:
    """Add engagement_metrics to the .research.json sidecar; create if absent.

    Returns (status, message).
    """
    side_key = key.rsplit(".", 1)[0] + ".research.json"
    try:
        body = s3.get_object(Bucket=BUCKET, Key=side_key)["Body"].read()
        side = json.loads(body)
        if "research" not in side or not isinstance(side["research"], dict):
            side["research"] = {}
        side["research"]["engagement_metrics"] = engagement
    except s3.exceptions.NoSuchKey:
        # No existing sidecar; create a minimal one.
        side = {
            "show": None,
            "platform": None,
            "research": {"engagement_metrics": engagement},
            "backfill_only": True,
        }
    except Exception as e:
        return ("warn", f"sidecar read failed: {e}; creating fresh one")

    try:
        s3.put_object(
            Bucket=BUCKET,
            Key=side_key,
            Body=json.dumps(side, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return ("ok", "sidecar updated")
    except Exception as e:
        return ("warn", f"sidecar write failed: {e}")


# ─── Per-CSV worker ────────────────────────────────────────────────────


def process_one(key: str, *, dry_run: bool = False) -> tuple[str, str, str]:
    """Returns (status, key, message). Status ∈ {ok, skip, fail}."""
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode(
            "utf-8", errors="replace"
        )
    except Exception as e:
        return ("fail", key, f"download error: {e}")

    meta = parse_metadata(body)
    if meta["has_engagement_rows"]:
        return ("skip", key, "already has engagement rows")
    if not meta["show_name"]:
        return ("fail", key, "could not parse Show/Content Tracked")

    research = _research_engagement_metrics(
        show_name=meta["show_name"],
        platform_name=meta["platform_name"],
        genre=meta["genre"],
        content_cadence=meta["content_cadence"],
        episode_count=meta["episode_count"],
        is_movie=meta["is_movie"],
        runtime_minutes=None,
        release_date=meta["release_date"],
    )
    if not research or (
        research.get("completion_rate_pct") is None
        and research.get("second_screen_pct") is None
    ):
        return ("fail", key, "claude returned no engagement values")

    cr = research.get("completion_rate_pct")
    ss = research.get("second_screen_pct")
    if cr is None or ss is None:
        return ("fail", key, f"missing field — cr={cr} ss={ss}")

    new_body = splice_engagement_rows(body, cr, ss)
    if new_body == body:
        return ("fail", key, "splice produced no change (anchor row missing)")

    msg = f"cr={cr}% ss={ss}% conf={research.get('confidence','?')}"
    if dry_run:
        return ("ok", key, f"[dry-run] would update — {msg}")

    try:
        s3.put_object(
            Bucket=BUCKET, Key=key,
            Body=new_body.encode("utf-8"),
            ContentType="text/csv",
        )
    except Exception as e:
        return ("fail", key, f"upload error: {e}")

    side_status, side_msg = update_sidecar(key, research)
    if side_status != "ok":
        msg += f"; sidecar: {side_msg}"

    return ("ok", key, msg)


# ─── Driver ────────────────────────────────────────────────────────────


def list_root_csvs() -> list[str]:
    pager = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in pager.paginate(Bucket=BUCKET):
        for o in page.get("Contents") or []:
            k = o["Key"]
            if "/" in k:
                continue
            if k.endswith(".csv"):
                keys.append(k)
    keys.sort()
    return keys


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only first N CSVs (after sort) for piloting")
    ap.add_argument("--workers", type=int, default=6,
                    help="Parallel Claude calls (Sonnet 4.5 RPM ~50; default 6)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Research + log results, but don't write to S3")
    ap.add_argument("--only", nargs="*", default=None,
                    help="Only process these S3 keys (whitespace-separated)")
    args = ap.parse_args()

    if args.only:
        keys = args.only
    else:
        all_keys = list_root_csvs()
        # Skip files that already have both rows (cheap heuristic — proper check
        # happens inside process_one, this is just to keep the progress bar tidy).
        keys = all_keys
    if args.limit:
        keys = keys[: args.limit]

    print(f"📋 Backfilling engagement metrics on {len(keys)} CSVs "
          f"({'DRY-RUN' if args.dry_run else 'WRITE'} mode, workers={args.workers})")
    t0 = time.time()
    counts = {"ok": 0, "skip": 0, "fail": 0}
    failures: list[tuple[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_one, k, dry_run=args.dry_run): k for k in keys}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            status, key, msg = fut.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            tag = {"ok": "✅", "skip": "⏭️", "fail": "❌"}.get(status, "?")
            print(f"  [{done:>3}/{len(keys)}] {tag} {key:<55} {msg}")
            if status == "fail":
                failures.append((key, msg))

    dt = time.time() - t0
    print()
    print(f"Done in {dt/60:.1f} min — ok={counts['ok']}, skip={counts['skip']}, "
          f"fail={counts['fail']}")
    if failures:
        print("\nFailures:")
        for k, m in failures:
            print(f"   ❌ {k}: {m}")


if __name__ == "__main__":
    main()
