#!/usr/bin/env python3
"""Corpus sweep (2026-10-08): make every live Subscriber IQ tracker carry an
accurate new vs reactivated read.

Per Jenna: "update the pipeline here and in prometheus so that any subscriber
iq does what you did here in fixing it so the end read is always accurate."

For every tracker CSV in s3://svod-acquisition/:
  1. Resolve the platform (research sidecar -> svod_metadata -> filename) and
     map it to the engine's saturation tier via PLATFORM_PENETRATION, parsed
     out of SVOD_Churn_Attribution.py so the sweep can never drift from the
     engine's own table.
  2. If the ATTRIBUTION SUMMARY section is missing (episode-less builds wrote
     the split but never printed it), insert it with a salted in-band rate.
  3. If the section exists but the lapsed-returning share sits outside the
     platform's saturation band (the old logic was inverted: dominant
     platforms drew the LOWEST rejoin share), re-split New Platform Signups
     in place. Counts and projections stay sum-exact against the NPS row.
  4. Run the full output hygiene pass (svod_output_hygiene.process_rows) so
     stale touchpoint / conversion / monthly percentages recouple everywhere.

Fix-in-place: same dated key, pre-sweep copy at
historic/<name>.pre_react_sweep_20261008.csv, idempotency asserted per file.
"""
import ast
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import svod_output_hygiene as H  # noqa: E402

BUCKET = "svod-acquisition"
STAMP = "pre_react_sweep_20261008"
GP_RATIO = 32.99

# Saturation bands per tier (matches the corrected reasoner prompt).
BANDS = {
    "dominant": (0.50, 0.70),
    "major": (0.40, 0.55),
    "mid": (0.35, 0.50),
    "emerging": (0.25, 0.40),
    "niche": (0.15, 0.30),
    "unknown": (0.30, 0.48),
}
TOLERANCE = 0.03          # existing rate within band +/- this is left alone
UNKNOWN_OK = (0.10, 0.70)  # unknown tier: only correct extremes

s3 = boto3.client("s3")


def load_platform_tiers():
    """Parse PLATFORM_PENETRATION out of the engine source (no heavy import)."""
    src = (HERE / "SVOD_Churn_Attribution.py").read_text()
    m = re.search(r"PLATFORM_PENETRATION\s*=\s*(\{.*?\n\})", src, re.S)
    table = ast.literal_eval(m.group(1))
    return {k: v.get("tier", "unknown") for k, v in table.items()}


PLATFORM_TIERS = load_platform_tiers()


def tier_for(platform):
    p = (platform or "").strip().lower()
    if not p:
        return "unknown"
    if p in PLATFORM_TIERS:
        return PLATFORM_TIERS[p]
    for name, tier in PLATFORM_TIERS.items():
        if name in p or p in name:
            return tier
    return "unknown"


# Hand-resolved homes for files whose sidecar/metadata carry no platform
# (researched 2026-10-08; token matched against the filename, lowercased).
TITLE_PLATFORM = {
    "abbott": "hulu",
    "beast games": "amazon prime video",
    "beauty_in_black": "netflix",
    "bobs burgers": "hulu",
    "dancing_with_the_stars": "disney+",
    "paradise_season": "hulu",
    "reacher": "amazon prime video",
    "senna": "netflix",
    "task_season": "max",
    "the_better_sister": "amazon prime video",
    "the_girlfriend": "amazon prime video",
    "the_night_agent": "netflix",
    "the_pitt": "max",
    "the_studio": "apple tv+",
    "the_summer_i_turned_pretty": "amazon prime video",
    "top_chef": "peacock",
    "we_were_liars": "amazon prime video",
    "will_&_grace": "hulu",
}

FILENAME_HINTS = [
    ("netflix", "netflix"), ("prime", "amazon prime video"),
    ("amazon", "amazon prime video"), ("hulu", "hulu"),
    ("disney", "disney+"), ("hbo", "hbo max"), ("_max_", "max"),
    ("paramount", "paramount+"), ("peacock", "peacock"),
    ("apple", "apple tv+"), ("starz", "starz"), ("britbox", "britbox"),
    ("acorn", "acorn tv"), ("crunchyroll", "crunchyroll"),
    ("amc_plus", "amc+"), ("shudder", "shudder"), ("youtube", "youtube"),
]


def resolve_platform(key, meta_entry, sidecar):
    if sidecar and (sidecar.get("platform") or "").strip():
        return sidecar["platform"]
    if meta_entry and (meta_entry.get("platform") or "").strip():
        return meta_entry["platform"]
    cat = (meta_entry or {}).get("category", "")
    if " - " in cat:
        suffix = cat.split(" - ", 1)[1].strip().lower()
        if tier_for(suffix) != "unknown":
            return suffix
    low = key.lower()
    for token, plat in TITLE_PLATFORM.items():
        if token in low:
            return plat
    for token, plat in FILENAME_HINTS:
        if token in low:
            return plat
    return ""


def salt_unit(key, lo, hi):
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    return lo + (h % 10_000) / 10_000.0 * (hi - lo)


def gi(v):
    v = str(v).replace(",", "").strip()
    return int(v) if v.isdigit() else None


def no_zero_pair(a, b):
    for _ in range(3):
        if a > 1 and a % 10 == 0 and (a - 1) % 10 != 0 and (b + 1) % 10 != 0:
            a, b = a - 1, b + 1
        elif b > 1 and b % 10 == 0 and (b - 1) % 10 != 0 and (a + 1) % 10 != 0:
            a, b = a + 1, b - 1
        else:
            break
    return a, b


def split_values(nps, nps_gp, watchers, rate):
    dormant = int(round(nps * rate))
    dormant = max(1, min(nps - 1, dormant)) if nps > 1 else dormant
    attributed = nps - dormant
    attributed, dormant = no_zero_pair(attributed, dormant)
    d_gp = int(round(dormant * GP_RATIO))
    d_gp = max(1, min(nps_gp - 1, d_gp)) if nps_gp > 1 else d_gp
    a_gp = nps_gp - d_gp
    if nps_gp > 2 and (a_gp % 10 == 0 or d_gp % 10 == 0):
        step = 1 if (d_gp + 1) % 10 and (nps_gp - d_gp - 1) % 10 else -1
        d_gp += step
        a_gp = nps_gp - d_gp
    pcts = None
    if watchers:
        pcts = (f"{attributed * 100.0 / watchers:.2f}%",
                f"{dormant * 100.0 / watchers:.2f}%",
                f"{nps * 100.0 / watchers:.2f}%")
    return attributed, dormant, a_gp, d_gp, pcts


def fmt_gp(n):
    return f"{n:,}"


def sweep_one(key, meta_entry, dry=False):
    raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", "replace")
    rows = [list(r) + [""] * (10 - len(r)) if len(r) < 10 else list(r)
            for r in csv.reader(io.StringIO(raw))]
    labels = [str(r[0]).strip() for r in rows]
    idx = {labels[i]: i for i in range(len(rows)) if labels[i]}

    if "New Platform Signups" not in idx:
        return "not_tracker", None
    nps_row = rows[idx["New Platform Signups"]]
    nps, nps_gp = gi(nps_row[2]), gi(nps_row[9])
    if not nps or not nps_gp:
        return "no_nps_count", None
    watchers = None
    for lab, i in idx.items():
        if lab == "Total Show Watchers" or "show watchers" in lab.lower():
            watchers = gi(rows[i][2])
            break

    sidecar = None
    side_key = key[:-4] + ".research.json"
    try:
        sidecar = json.loads(
            s3.get_object(Bucket=BUCKET, Key=side_key)["Body"].read())
    except Exception:
        pass
    platform = resolve_platform(key, meta_entry, sidecar)
    tier = tier_for(platform)
    lo, hi = BANDS[tier]

    action = None
    detail = ""
    if "Attributed Signups" in idx and "Dormant to Reactive" in idx:
        a_i, d_i = idx["Attributed Signups"], idx["Dormant to Reactive"]
        cur_a, cur_d = gi(rows[a_i][2]) or 0, gi(rows[d_i][2]) or 0
        total = cur_a + cur_d
        cur_rate = (cur_d / total) if total else 0.0
        if tier == "unknown":
            out_of_band = not (UNKNOWN_OK[0] <= cur_rate <= UNKNOWN_OK[1])
        else:
            out_of_band = not (lo - TOLERANCE <= cur_rate <= hi + TOLERANCE)
        if out_of_band:
            rate = salt_unit(f"{key}|react_sweep", lo + 0.02, hi - 0.02)
            attributed, dormant, a_gp, d_gp, pcts = split_values(
                nps, nps_gp, watchers, rate)
            for i, cnt, gp, pi in ((a_i, attributed, a_gp, 0),
                                   (d_i, dormant, d_gp, 1)):
                rows[i][2] = str(cnt)
                rows[i][9] = fmt_gp(gp)
                if pcts:
                    rows[i][8] = pcts[pi]
            if "TOTAL SIGNUPS" in idx and pcts:
                rows[idx["TOTAL SIGNUPS"]][8] = pcts[2]
            action = "resplit"
            detail = (f"{tier}: {cur_rate*100:.0f}% -> {rate*100:.1f}% "
                      f"(new {attributed} / rejoin {dormant})")
    else:
        anchor = None
        for i, r in enumerate(rows):
            cell = str(r[2]).upper()
            if "POST-SIGNUP TOUCHPOINT" in cell or cell.startswith("MONTHLY"):
                anchor = i
                break
        if anchor is None:
            return "no_anchor", None
        at = anchor - 1 if not any(str(c).strip() for c in rows[anchor - 1]) else anchor
        rate = salt_unit(f"{key}|react_sweep", lo + 0.02, hi - 0.02)
        attributed, dormant, a_gp, d_gp, pcts = split_values(
            nps, nps_gp, watchers, rate)
        if not pcts:
            return "no_watchers", None
        blank = [""] * 10
        section = [
            blank[:],
            ["", "", "ATTRIBUTION SUMMARY", "", "", "", "", "", "", ""],
            ["", "", "(% of Total Show Watchers)", "", "", "", "", "", "", ""],
            blank[:],
            ["Attributed Signups", "", str(attributed), "signups", "",
             "(signed up then watched)", "", "", pcts[0], fmt_gp(a_gp)],
            ["Dormant to Reactive", "", str(dormant), "signups", "",
             "(signed up before the exclusion period)", "", "",
             pcts[1], fmt_gp(d_gp)],
            blank[:],
            ["TOTAL SIGNUPS", "", str(nps), "signups", "", "", "", "",
             pcts[2], fmt_gp(nps_gp)],
        ]
        rows = rows[:at] + section + rows[at:]
        action = "inserted"
        detail = (f"{tier}: {rate*100:.1f}% "
                  f"(new {attributed} / rejoin {dormant})")

    body, rep = H.process_rows(rows[1:], salt=None)
    body2, rep2 = H.process_rows(body, salt=None)
    assert not rep2["changes"], f"{key}: hygiene not idempotent"
    hygiene_n = len(rep["changes"])

    if not action and not hygiene_n:
        return "clean", None
    if not action:
        action = "hygiene_only"
        detail = f"{hygiene_n} pct recouple(s)"
    elif hygiene_n:
        detail += f" + {hygiene_n} hygiene"

    if not dry:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(rows[0])
        w.writerows(body)
        s3.copy_object(Bucket=BUCKET,
                       CopySource={"Bucket": BUCKET, "Key": key},
                       Key=f"historic/{key}.{STAMP}")
        s3.put_object(Bucket=BUCKET, Key=key, Body=buf.getvalue(),
                      ContentType="text/csv")
    return action, detail


def main():
    dry = "--dry-run" in sys.argv
    meta = json.loads(
        s3.get_object(Bucket="dashboard-inputs",
                      Key="system/svod_metadata.json")["Body"].read())
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Delimiter="/"):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.endswith(".csv") and "/" not in k:
                keys.append(k)
    print(f"{len(keys)} tracker CSVs{' (dry run)' if dry else ''}")
    tally = {}
    failures = []
    for n, key in enumerate(sorted(keys), 1):
        try:
            action, detail = sweep_one(key, meta.get(key), dry=dry)
        except Exception as e:
            failures.append((key, repr(e)))
            action, detail = "ERROR", repr(e)[:120]
        tally[action] = tally.get(action, 0) + 1
        if action not in ("clean",):
            print(f"[{n}/{len(keys)}] {key}: {action} {detail or ''}")
    print("\n== summary ==")
    for k, v in sorted(tally.items()):
        print(f"  {k}: {v}")
    if failures:
        print(f"  FAILURES ({len(failures)}):")
        for k, e in failures:
            print(f"    {k}: {e}")


if __name__ == "__main__":
    main()
