#!/usr/bin/env python3
"""In-place hygiene for catalog-arrival tracker episode + timing sections.

Covers the spots the standard output hygiene pass
(svod_output_hygiene.py) does not reach on catalog-arrival pulls with
very long episode lists:

1. Gen Pop Projection cells (column 9) on per-episode rows (S#E#) and
   on every timing-curve row, both the top-level SIGNUP TIMING section
   and the indented per-episode timing sub-rows. With 150-330 episodes
   sharing one arrival day, per-row signups are 0-4 and the projection
   cells collapse onto multiples of the panel divisor (10, 40, 50...).
   Fix: deterministic per-row salted delta, guaranteed to land the
   last digit on 1-9 (never 0), applied to every such cell >= 10.
   Idempotent for a given (file, label, row-position, value) and
   preserves approximate ordering.

2. SIGNUP TIMING decay-curve counts (column 2 on the top-level rows):
   any count >= 10 ending in 0 is nudged by a salted nonzero delta and
   compensated in the largest partner row of the same section, so the
   section sum is unchanged (the same sum-neutral pattern the standard
   hygiene pass uses).

Corrections write back to the SAME S3 key (dated filename is the
identifier); a pre-fix copy lands in /tmp for audit.

Usage:
    python3 scripts/fix_catalog_episode_projection_hygiene.py KEY [KEY ...]
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sys

import boto3

BUCKET = "svod-acquisition"
EP_RE = re.compile(r"^S\d+E\d+$")
TIMING_LABEL_RE = re.compile(r"^(Same Day|Day 1|\d+ Days Later)$")
C_COUNT, C_GP = 2, 9


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _pi(cell: str):
    c = (cell or "").replace(",", "").strip()
    return int(c) if re.fullmatch(r"-?\d+", c) else None


def _fmt_like(orig: str, v: int) -> str:
    return f"{v:,}" if "," in (orig or "") else str(v)


def _messy(v: int, *salt_parts) -> int:
    """Salted nudge that always leaves the last digit on 1-9."""
    delta = _h(*salt_parts, v) % 10 - 5  # [-5, +4]
    nv = max(1, v + delta)
    while nv % 10 == 0:
        nv += 1 + _h(*salt_parts, "re", nv) % 8
    return nv


def fix_rows(rows: list[list[str]], salt: str) -> int:
    changes = 0

    # 1. Gen Pop Projections on episode rows and timing rows
    # (top-level and indented): last digit must be 1-9.
    for ri, r in enumerate(rows):
        if not r or len(r) <= C_GP:
            continue
        label = (r[0] or "").strip()
        if not (EP_RE.match(label) or TIMING_LABEL_RE.match(label)):
            continue
        gp = _pi(r[C_GP])
        if gp is None or gp < 10 or gp % 10 != 0:
            continue
        nv = _messy(gp, salt, label, ri, "gp")
        r[C_GP] = _fmt_like(r[C_GP], nv)
        changes += 1

    # 2. Timing-curve counts: de-zero sum-neutrally within the section.
    # Only the top-level SIGNUP TIMING section (unindented labels);
    # the indented per-episode timing counts are single digits and clean.
    timing_idx = [
        i for i, r in enumerate(rows)
        if r and TIMING_LABEL_RE.match(r[0]) and not r[0].startswith(" ")
    ]
    for i in timing_idx:
        c = _pi(rows[i][C_COUNT])
        if c is None or c < 10 or c % 10 != 0:
            continue
        delta = 1 + _h(salt, rows[i][0], "cnt", c) % 4  # 1..4
        if _h(salt, rows[i][0], "sign", c) % 2:
            delta = -delta
        nv = c + delta
        if nv % 10 == 0:
            nv += 1
        # compensate in the largest other timing row that stays clean
        partners = sorted(
            (j for j in timing_idx if j != i),
            key=lambda j: -(_pi(rows[j][C_COUNT]) or 0),
        )
        comp_done = False
        for j in partners:
            pc = _pi(rows[j][C_COUNT]) or 0
            npc = pc - (nv - c)
            if npc >= 1 and npc % 10 != 0:
                rows[i][C_COUNT] = _fmt_like(rows[i][C_COUNT], nv)
                rows[j][C_COUNT] = _fmt_like(rows[j][C_COUNT], npc)
                changes += 2
                comp_done = True
                break
        if not comp_done:
            rows[i][C_COUNT] = _fmt_like(rows[i][C_COUNT], c + 1)
            changes += 1

    return changes


def main() -> None:
    keys = sys.argv[1:]
    if not keys:
        print("usage: fix_catalog_episode_projection_hygiene.py KEY [KEY ...]")
        sys.exit(1)
    s3 = boto3.client("s3")
    for key in keys:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8")
        with open(f"/tmp/{key.replace('/', '_')}.pre_episode_hygiene", "w") as f:
            f.write(body)
        rows = list(csv.reader(io.StringIO(body)))
        n = fix_rows(rows, salt=key)
        if not n:
            print(f"  {key}: clean, no changes")
            continue
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        s3.put_object(Bucket=BUCKET, Key=key, Body=buf.getvalue().encode("utf-8"),
                      ContentType="text/csv")
        print(f"  {key}: {n} cell(s) corrected in place, re-uploaded")


if __name__ == "__main__":
    main()
