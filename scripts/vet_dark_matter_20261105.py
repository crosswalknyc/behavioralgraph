#!/usr/bin/env python3
"""Dark Matter vet (2026-11-05, Jenna: "vet dark matter seasons 1 and 2
data to make sure they are good and accurate and remove any partial reads").

Season 1 (Dark_Matter_-_Season_1_06_30_2026_17_37.csv) in-place fixes:
  - six Gen Pop projection cells that drifted off their counts
    (two timing rows, four demographic rows)
  - four of nine episode counts ended in zero (placeholder signature on
    the headline table); three nudged to natural digits with their
    per-episode timing blocks re-balanced, one kept as a natural zero
  - every episode percentage recomputed from the final counts (fixes the
    two stale cells: Episode 1 at 20.0 vs 19.9, Episode 9 at 11.0 vs 11.1)
  - every per-episode timing curve re-balanced to its episode total: the
    shipped file under-summed all nine blocks by 2.4-8.7%, so a reader
    summing a curve landed short of the printed episode signups

Season 2: the "to date" file was a mid-season snapshot (window cut at
2026-10-01 against an Oct 30 finale). Removed from the live catalog and
replaced by a full-season build on the corrected engine (handled by the
caller; this script only patches S1).

Backup: historic/<name>.pre_vet_20261105.csv, same dated key re-uploaded,
output hygiene re-run and asserted idempotent.
"""
import csv
import hashlib
import io
import sys
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import svod_output_hygiene as H  # noqa: E402

BUCKET = "svod-acquisition"
KEY = "Dark_Matter_-_Season_1_06_30_2026_17_37.csv"
GP = 32.99

# Episode count nudges (zero-sum; Episode 4 keeps 590 as a natural zero;
# values chosen to avoid colliding with any sibling episode count).
EP_NUDGES = {"Episode 6": 4, "Episode 7": -1, "Episode 8": -3}

s3 = boto3.client("s3")


def gi(v):
    v = str(v).replace(",", "").strip()
    return int(v) if v.isdigit() else None


def gp_cell(c, salt_key):
    g = int(round(c * GP))
    if g % 10 == 0:
        h = int(hashlib.md5(salt_key.encode()).hexdigest()[:4], 16)
        g += 1 if h % 2 else -1
    return f"{g:,}"


def main():
    raw = s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read().decode()
    rows = [list(r) + [""] * (10 - len(r)) if len(r) < 10 else list(r)
            for r in csv.reader(io.StringIO(raw))]

    nps = next(gi(r[2]) for r in rows
               if r[0].strip() == "New Platform Signups")

    # Locate the global per-episode table (rows with Episode Date filled)
    # and the per-episode timing sub-blocks (bare "Episode N" headers).
    ep_rows = {}      # name -> row index in the global table
    blocks = {}       # name -> (start, end) of its timing sub-block rows
    cur = None
    for i, r in enumerate(rows):
        name = r[0].strip()
        if name.startswith("Episode ") and r[1].strip() and gi(r[2]) is not None:
            ep_rows[name] = i
        elif name.startswith("Episode ") and not r[1].strip() and gi(r[2]) is None:
            if cur:
                blocks[cur][1] = i
            cur = name
            blocks[cur] = [i + 1, len(rows)]
        elif cur and name and not r[0].startswith(" ") and gi(r[2]) is None:
            blocks[cur][1] = i
            cur = None

    changed = []

    # 1. Episode count nudges (headline table).
    for name, delta in EP_NUDGES.items():
        i = ep_rows[name]
        old = gi(rows[i][2])
        rows[i][2] = str(old + delta)
        changed.append(f"{name}: {old} -> {old + delta}")

    # 2. Re-balance EVERY per-episode timing block to its episode total.
    #    The shipped file under-summed all nine blocks by 2.4-8.7% (the
    #    curves were drawn independently of the totals), so a reader
    #    summing a curve landed short of the printed episode signups.
    #    Largest-remainder apportion preserves each curve's shape.
    for name, i in ep_rows.items():
        target = gi(rows[i][2])
        s, e = blocks[name]
        subs = [(j, gi(rows[j][2])) for j in range(s, e)
                if rows[j][0].startswith(" ") and gi(rows[j][2]) is not None]
        total = sum(c for _, c in subs) or 1
        quotas = [(j, target * (c / total)) for j, c in subs]
        floors = {j: int(q) for j, q in quotas}
        rem = target - sum(floors.values())
        for j, q in sorted(quotas, key=lambda x: x[1] - int(x[1]), reverse=True):
            if rem <= 0:
                break
            floors[j] += 1
            rem -= 1
        # Keep moved cells off trailing zeros (shape-invisible +/-1 shifts).
        order = [j for j, _ in sorted(quotas, key=lambda x: -x[1])]
        for j in order:
            if floors[j] > 0 and floors[j] % 10 == 0:
                for k in order:
                    if k != j and floors[k] > 1 and (floors[j] - 1) % 10 != 0 \
                            and (floors[k] + 1) % 10 != 0:
                        floors[j] -= 1
                        floors[k] += 1
                        break
        moved = 0
        for j, c in subs:
            if floors[j] != c:
                rows[j][2] = str(floors[j])
                rows[j][9] = gp_cell(floors[j], f"{KEY}|{name}|{j}")
                moved += 1
            new_pct = f"{floors[j] * 100.0 / target:.2f}%"
            if rows[j][8].endswith("%") and rows[j][8] != new_pct:
                rows[j][8] = new_pct
        bsum = sum(floors[j] for j, _ in subs)
        assert bsum == target, f"{name} block {bsum} != {target}"
        if moved:
            changed.append(f"{name} curve re-balanced to {target} ({moved} cells)")

    # 3. Recompute all global episode pcts + GPs from final counts.
    total = sum(gi(rows[i][2]) for i in ep_rows.values())
    assert total == nps, f"episode sum {total} != NPS {nps}"
    for name, i in ep_rows.items():
        c = gi(rows[i][2])
        want_pct = f"{c * 100.0 / nps:.1f}%"
        if rows[i][8] != want_pct:
            changed.append(f"{name} pct {rows[i][8]} -> {want_pct}")
            rows[i][8] = want_pct
        want_gp = gp_cell(c, f"{KEY}|{name}|gp")
        if gi(rows[i][9]) != gi(want_gp):
            rows[i][9] = want_gp

    # 4. Snap the six drifted projection cells (timing + demographics).
    for i, r in enumerate(rows):
        c, g = gi(r[2]), gi(r[9])
        lab = r[0].strip()
        if not (c and g and c > 20) or lab in ("New Platform Signups", "TOTAL SIGNUPS"):
            continue
        want = int(round(c * GP))
        if abs(g - want) > max(3, round(c * 0.002 * GP)):
            cell = gp_cell(c, f"{KEY}|{lab}|snap")
            changed.append(f"{lab} GP {g:,} -> {cell}")
            rows[i][9] = cell

    # 5. Hygiene + idempotency, backup, upload.
    body, rep = H.process_rows(rows[1:], salt=None)
    body2, rep2 = H.process_rows(body, salt=None)
    assert not rep2["changes"], rep2["changes"]
    for ch in rep["changes"]:
        changed.append(f"hygiene: {ch['label']} {ch['before']} -> {ch['after']}")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(rows[0])
    w.writerows(body)
    s3.copy_object(Bucket=BUCKET, CopySource={"Bucket": BUCKET, "Key": KEY},
                   Key=f"historic/{KEY}.pre_vet_20261105.csv")
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=buf.getvalue(),
                  ContentType="text/csv")
    print(f"patched {KEY}: {len(changed)} change(s)")
    for c in changed:
        print("  -", c)


if __name__ == "__main__":
    main()
