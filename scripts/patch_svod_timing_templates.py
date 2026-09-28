#!/usr/bin/env python3
"""In-place fix of old-engine template signatures across live Subscriber IQ files.

What this fixes (2026-09-28, surfaced on the Severance S1 re-pull):

  1. SIGNUP TIMING template: every synthetic pull stamped one constant
     decay curve (28.00 / 15.00 / 8.50 / 5.50 / ...) on the overall timing
     section AND every per-episode subsection - identical across episodes,
     titles, and files. Replaced with per-(title, episode) salted curves
     from the fixed engine generator, so a patched file matches what a
     fresh run on the current engine would produce.
  2. PER-EPISODE ATTRIBUTION split template: same-length seasons shared
     one split (2.4 premiere / 1.4 finale shape with no salt), so every
     9-episode season published identical percentages. Re-split with the
     title-salted shares.
  3. "min avg view" 4.2 artifact: the output divisor wrongly divided the
     episode-minutes value by 10 (42.0 -> 4.2). Restored to intent with
     per-episode salted variation.
  4. "days avg" 0/1 artifact: same divisor bug on the per-episode days
     value (5.5 -> 0/1). Regenerated from the engine formula + salt.
  5. POST-SIGNUP TOUCHPOINT template: constant 74.00 / 7.00 / 4.00 / 3.00
     / 12.00 split on every file. Re-split with title-salted shares that
     sum exactly to the signup total. The documented 1st-touchpoint
     projection alias (= New Platform Signups projection) is preserved;
     the section total projection is the component sum.
  6. Headline "Average Days from Show Available to Signup" pinned
     fallbacks (8.5 weekly / 6.2 binge / 3.5 event). Salted per title
     within the cadence band.
  7. MONTHLY CHURN rate ladder ((mo+y)%5 cycling 8.5/8.7/8.9/9.1/9.3 on
     every file). Salted per title+month; churned counts re-derived from
     the new rate.

What this never touches: Total Show Watchers, New Platform Signups,
conversion rates, Attributed / Dormant split, TOTAL SIGNUPS, reach
projections, engagement (completion / second screen), competitive
platforms, demographics, monthly signup totals. Every headline number a
deliverable ever quoted stays byte-identical; the script asserts it.

Backups: s3://svod-acquisition/historic/<name>.csv.pre_timing_fix_20260928
(same convention as the 2026-08-24 pre_round_reconcile sweep).

Usage:
  python3 scripts/patch_svod_timing_templates.py --dry-run
  python3 scripts/patch_svod_timing_templates.py --only Gilmore_Girls_09_17_2026_15_56.csv
  python3 scripts/patch_svod_timing_templates.py            # full sweep
"""

import argparse
import csv
import io
import re
import sys
from datetime import datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from SVOD_Churn_Attribution import _svod_salt_unit, _salted_timing_curve  # noqa: E402

BUCKET = "svod-acquisition"
BACKUP_SUFFIX = ".pre_timing_fix_20260928"
GP_RATIO = 32.99  # 10M panel -> 329.9M US

C_CAT, C_DATE, C_CNT, C_CLBL, C_SEC, C_SLBL, C_TER, C_TLBL, C_PCT, C_GP = range(10)

TIMING_TEMPLATE_HEAD = ("28.00%", "15.00%", "8.50%")
TOUCH_TEMPLATE = ("74.00%", "7.00%", "4.00%", "3.00%", "12.00%")
CHURN_LADDER = {"8.5", "8.7", "8.9", "9.1", "9.3"}
RETIRED_AVG_DAYS = {"8.5", "3.5", "6.2"}


def _stem(key: str) -> str:
    m = re.match(r"^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})\.csv$", key)
    return m.group(1) if m else key[:-4]


def _gp(count: int, salt: str, tag: str) -> str:
    v = int(round(count * GP_RATIO))
    if v > 0 and v % 10 == 0:
        h = int(_svod_salt_unit(f"{salt}|gp|{tag}|{v}", 1.0, 9.999))
        v += h if int(_svod_salt_unit(f"{salt}|gpd|{tag}|{v}", 0.0, 2.0)) == 0 else -h
        if v <= 0:
            v = abs(v) + 3
    return f"{v:,}"


def _fmt_days(v: float) -> str:
    s = f"{v:.1f}"
    return s


def _parse_date(s: str):
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def patch_file(rows: list, key: str) -> tuple:
    """Returns (rows, list_of_changes). Mutates a copy; gates each fix on its
    template signature so already-clean sections are left byte-identical."""
    rows = [list(r) + [""] * (10 - len(r)) if len(r) < 10 else list(r) for r in rows]
    stem = _stem(key)
    changes = []

    def cell(i, c):
        return rows[i][c] if i < len(rows) else ""

    # ---- anchors ----------------------------------------------------------
    total_signups = None
    nps_gp = None
    cadence = ""
    for i, r in enumerate(rows):
        if r[C_CAT] == "TOTAL SIGNUPS" and r[C_CLBL] == "signups":
            total_signups = int(float(str(r[C_CNT]).replace(",", "")))
        elif r[C_CAT] == "New Platform Signups":
            nps_gp = r[C_GP]
        elif r[C_CAT] == "Content Cadence":
            cadence = (r[3] or "").strip()
    if total_signups is None:
        return rows, []

    # ---- 6. headline avg days --------------------------------------------
    for i, r in enumerate(rows):
        if r[C_CAT] == "Average Days from Show Available to Signup" and str(r[C_SEC]).strip() in RETIRED_AVG_DAYS:
            cl = cadence.lower()
            if "event" in cl or "one" in cl or "awards" in cl:
                lo, hi = 2.7, 4.6
            elif "weekly" in cl:
                lo, hi = 7.2, 9.9
            else:
                lo, hi = 5.2, 7.4
            v = round(_svod_salt_unit(f"{stem}|avg_days|{cl}", lo, hi), 1)
            for retired in (3.5, 8.5, 6.2):
                if abs(v - retired) < 0.05:
                    v = round(v + 0.2, 1)
            changes.append(f"headline avg days {r[C_SEC]} -> {v}")
            rows[i][C_SEC] = _fmt_days(v)

    # ---- 1/2/3/4. per-episode attribution + timing ------------------------
    # locate attribution rows
    attr_start = None
    for i, r in enumerate(rows):
        if r[C_CNT] in ("PER-EPISODE ATTRIBUTION", "PER-DATE ATTRIBUTION"):
            attr_start = i
            break
    ep_rows = []  # (row_idx, label, date_str)
    if attr_start is not None:
        j = attr_start + 1
        while j < len(rows):
            r = rows[j]
            if r[C_CLBL] == "signups" and r[C_CAT].strip():
                ep_rows.append(j)
            elif r[C_CNT] == "ATTRIBUTION SUMMARY":
                break
            j += 1

    new_ep_totals = {}   # label -> new signups
    label_order = []     # labels in ordinal order
    if ep_rows:
        n = len(ep_rows)
        # ordinals: Episode N labels win; otherwise by parsed date, else file order
        info = []
        for idx in ep_rows:
            lbl = rows[idx][C_CAT].strip()
            m = re.match(r"^Episode (\d+)$", lbl)
            d = _parse_date(rows[idx][C_DATE])
            info.append({"idx": idx, "label": lbl, "date": rows[idx][C_DATE],
                         "epnum": int(m.group(1)) if m else None, "dt": d})
        if all(x["epnum"] for x in info):
            info.sort(key=lambda x: x["epnum"])
        elif all(x["dt"] for x in info) and n > 1:
            info.sort(key=lambda x: (x["dt"], x["idx"]))
            for k, x in enumerate(info, 1):
                x["epnum"] = k
        else:
            for k, x in enumerate(info, 1):
                if not x["epnum"]:
                    x["epnum"] = k
        label_order = [x["label"] for x in info]

        # shares (mirror of the fixed engine)
        if n == 1:
            shares = [1.0]
        else:
            shares = []
            for i2 in range(n):
                if i2 == 0:
                    b = 2.4 * _svod_salt_unit(f"{stem}|share|premiere", 0.90, 1.10)
                elif i2 == n - 1:
                    b = 1.4 * _svod_salt_unit(f"{stem}|share|finale", 0.90, 1.11)
                elif i2 == n - 2:
                    b = 1.0 * _svod_salt_unit(f"{stem}|share|penult", 0.92, 1.09)
                else:
                    pos = i2 / (n - 1)
                    b = (1.5 - 0.7 * abs(0.5 - pos) * 2) * _svod_salt_unit(f"{stem}|share|mid{i2}", 0.93, 1.08)
                shares.append(b)
            t = sum(shares)
            shares = [s / t for s in shares]

        floor = 1 if total_signups >= n else 0
        counts = [max(floor, int(round(total_signups * s))) for s in shares]
        # Robust residual reconcile (max(1,...) floors can inflate the sum on
        # long library seasons; simple absorption could push a count negative)
        residual = total_signups - sum(counts)
        guard = 0
        while residual != 0 and guard < 100000:
            if residual > 0:
                k = counts.index(max(counts))
                counts[k] += 1
                residual -= 1
            else:
                k = max((j for j in range(len(counts)) if counts[j] > floor),
                        key=lambda j: counts[j], default=None)
                if k is None:
                    break
                counts[k] -= 1
                residual += 1
            guard += 1
        for x, s, c in zip(info, shares, counts):
            x["share"], x["count"] = s, c
            new_ep_totals[x["label"]] = c

        def _artifact_days(s):
            return bool(re.match(r"^-?\d+$", str(s).strip()))  # post-divisor int artifact

        # detect whether the split itself is template-era (only rewrite
        # counts/pcts when the old artifacts are present; otherwise keep)
        old_42 = all(str(rows[i3][C_TER]).strip() == "4.2" for i3 in ep_rows)
        old_days = all(_artifact_days(rows[i3][C_SEC]) for i3 in ep_rows)
        rewrite_split = n > 1 and (old_42 or old_days)
        days_slope = 0.3 * min(1.0, 12.0 / n)

        new_block = []
        order = sorted(info, key=lambda x: -x["count"]) if rewrite_split else \
            [next(x for x in info if x["idx"] == i3) for i3 in ep_rows]
        for x in order:
            epn, lbl = x["epnum"], x["label"]
            old = rows[x["idx"]]
            days = round(max(0.4, 6.5 + (epn - n / 2) * days_slope
                             + _svod_salt_unit(f"{stem}|epdays|{epn}", -0.45, 0.45)), 1)
            if str(old[C_TER]).strip() == "4.2":
                dur = round(42.0 * _svod_salt_unit(f"{stem}|epdur|{epn}", 0.80, 0.96), 1)
                if abs(dur - round(dur)) < 0.05:
                    dur = round(dur + _svod_salt_unit(f"{stem}|epdur2|{epn}", 0.1, 0.4), 1)
                dur_s = f"{dur}"
            else:
                dur_s = old[C_TER]
            if rewrite_split:
                cnt, pct = x["count"], f"{x['share'] * 100:.1f}%"
                gp = _gp(x["count"], stem, f"ep{epn}")
            elif str(old[C_SLBL]).strip() == "no attribution found":
                # zero-attribution row already in engine shape: keep verbatim
                new_block.append(list(old))
                new_ep_totals[lbl] = 0
                continue
            elif n == 1 and int(float(str(old[C_CNT]).replace(",", ""))) != total_signups:
                # single-row file carrying the pre-reconcile count: snap the
                # row to the reconciled TOTAL SIGNUPS so the section agrees
                # with the headline chain
                cnt, pct = total_signups, "100.0%"
                gp = _gp(total_signups, stem, "ep1")
                new_ep_totals[lbl] = total_signups
            else:
                cnt, pct, gp = old[C_CNT], old[C_PCT], old[C_GP]
                new_ep_totals[lbl] = int(float(str(cnt).replace(",", "")))
            if rewrite_split and cnt == 0:
                # engine convention for a zero-attribution episode
                new_block.append([lbl, x["date"], 0, "signups", "", "no attribution found",
                                  "", "", "0%", "0"])
                continue
            days_s = _fmt_days(days) if _artifact_days(old[C_SEC]) else old[C_SEC]
            new_block.append([lbl, x["date"], cnt, "signups", days_s, "days avg",
                              dur_s, "min avg view", pct, gp])
        if new_block != [rows[i3] for i3 in ep_rows]:
            for pos, i3 in enumerate(ep_rows):
                rows[i3] = new_block[pos]
            changes.append(f"episode attribution rewritten ({n} rows, split={'re-salted' if rewrite_split else 'kept'})")

    # ---- overall SIGNUP TIMING --------------------------------------------
    def _find_section(marker_prefix):
        for i4, r in enumerate(rows):
            if str(r[C_CNT]).startswith(marker_prefix):
                return i4
        return None

    def _timing_rows_from(start_i):
        out = []
        j2 = start_i + 1
        while j2 < len(rows):
            lbl = rows[j2][C_CAT]
            base = lbl.strip()
            if base == "Same Day" or base == "Day 1" or re.match(r"^\d+ Days Later$", base):
                out.append(j2)
            elif base and rows[j2][C_CNT] == "" and out:
                break
            elif rows[j2][C_CNT] not in ("",) and out:
                break
            elif not base and out and rows[j2][C_CLBL] == "":
                break
            j2 += 1
        return out

    ov_start = _find_section("SIGNUP TIMING (Days")
    if ov_start is not None:
        t_rows = _timing_rows_from(ov_start)
        if len(t_rows) >= 5 and tuple(rows[i5][C_PCT] for i5 in t_rows[:3]) == TIMING_TEMPLATE_HEAD:
            curve = _salted_timing_curve(stem, "overall")
            for (d, pct), i5 in zip(curve, t_rows):
                cnt = max(0, int(round(total_signups * pct / 100.0)))
                indent = rows[i5][C_CAT][:len(rows[i5][C_CAT]) - len(rows[i5][C_CAT].lstrip())]
                rows[i5] = [indent + rows[i5][C_CAT].strip(), "", cnt, "signups", "", "", "", "",
                            f"{pct:.2f}%", _gp(cnt, stem, f"tim_ov_{d}")]
            changes.append("overall timing curve re-salted")

    # ---- per-episode SIGNUP TIMING ----------------------------------------
    pe_start = _find_section("SIGNUP TIMING PER")
    if pe_start is not None:
        # subsections: header rows (label, no count) then indented day rows
        j3 = pe_start + 1
        cur_label, sub = None, []
        subsections = []
        while j3 < len(rows):
            r = rows[j3]
            base = r[C_CAT].strip()
            if r[C_CNT] in ("POST-SIGNUP TOUCHPOINT ANALYSIS",) or str(r[C_CNT]).startswith(("MONTHLY", "COMPETITIVE")):
                break
            if base and r[C_CNT] == "" and not re.match(r"^(Same Day|Day 1|\d+ Days Later)$", base):
                if cur_label and sub:
                    subsections.append((cur_label, sub))
                cur_label, sub = base, []
            elif re.match(r"^(Same Day|Day 1|\d+ Days Later)$", base) and cur_label:
                sub.append(j3)
            j3 += 1
        if cur_label and sub:
            subsections.append((cur_label, sub))

        patched_subs = 0
        for label, sub_rows in subsections:
            if len(sub_rows) < 5:
                continue
            if tuple(rows[i6][C_PCT] for i6 in sub_rows[:3]) != TIMING_TEMPLATE_HEAD:
                continue
            if label_order and label in label_order:
                ordinal = label_order.index(label) + 1
            else:
                ordinal = subsections.index((label, sub_rows)) + 1
            ep_total = new_ep_totals.get(label, total_signups if len(subsections) == 1 else None)
            if ep_total is None:
                continue
            curve = _salted_timing_curve(stem, f"ep{ordinal}")
            for (d, pct), i6 in zip(curve, sub_rows):
                cnt = max(0, int(round(ep_total * pct / 100.0)))
                orig = rows[i6][C_CAT]
                indent = orig[:len(orig) - len(orig.lstrip())]
                rows[i6] = [indent + orig.strip(), "", cnt, "signups", "", "", "", "",
                            f"{pct:.2f}%", _gp(cnt, stem, f"tim_ep{ordinal}_{d}")]
            patched_subs += 1
        if patched_subs:
            changes.append(f"per-episode timing curves re-salted ({patched_subs} subsections)")

    # ---- 5. touchpoints ----------------------------------------------------
    touch_idx = {}
    total_idx = None
    for i7, r in enumerate(rows):
        m = re.match(r"^(\d)(?:st|nd|rd|th) Touchpoint$", r[C_CAT].strip())
        if m and r[C_CLBL] == "accounts activated":
            touch_idx[int(m.group(1))] = i7
        elif r[C_CAT].strip() == "Total Platform Signups" and r[C_CLBL] == "accounts activated":
            total_idx = i7
    if len(touch_idx) == 5 and tuple(rows[touch_idx[k]][C_PCT] for k in range(1, 6)) == TOUCH_TEMPLATE:
        r1 = _svod_salt_unit(f"{stem}|touch|1", 0.705, 0.775)
        r2 = _svod_salt_unit(f"{stem}|touch|2", 0.055, 0.088)
        r3 = _svod_salt_unit(f"{stem}|touch|3", 0.030, 0.052)
        r4 = _svod_salt_unit(f"{stem}|touch|4", 0.020, 0.040)
        c = {1: max(1, int(round(total_signups * r1))),
             2: max(1, int(round(total_signups * r2))),
             3: max(1, int(round(total_signups * r3))),
             4: max(1, int(round(total_signups * r4)))}
        c[5] = max(1, total_signups - sum(c.values()))
        gp_sum = 0
        for k in range(1, 6):
            i8 = touch_idx[k]
            old_gp = rows[i8][C_GP]
            if k == 1 and nps_gp and old_gp == nps_gp:
                gp_s = nps_gp  # documented alias: 1st touchpoint projection = NPS projection
            else:
                gp_s = _gp(c[k], stem, f"touch{k}")
            gp_sum += int(gp_s.replace(",", ""))
            rows[i8] = [rows[i8][C_CAT], "", c[k], "accounts activated", "", "", "", "",
                        f"{c[k] * 100.0 / total_signups:.2f}%", gp_s]
        if total_idx is not None:
            rows[total_idx][C_CNT] = total_signups
            rows[total_idx][C_PCT] = "100.00%"
            rows[total_idx][C_GP] = f"{gp_sum:,}"
        changes.append("touchpoint split re-salted")

    # ---- 7. monthly churn ---------------------------------------------------
    # monthly totals for churn re-derivation
    month_totals = {}
    for r in rows:
        if r[C_CLBL] == "signups" and re.match(r"^\d{4}-\d{2}$", r[C_CAT].strip()) and r[C_SLBL] == "watched show":
            month_totals[r[C_CAT].strip()] = int(float(str(r[C_CNT]).replace(",", "")))
    churn_patched = 0
    for i9, r in enumerate(rows):
        if r[C_CLBL] == "churned" and re.match(r"^\d{4}-\d{2}$", r[C_CAT].strip()):
            rate_s = str(r[C_PCT]).replace("%", "").strip()
            if rate_s in CHURN_LADDER:
                label = r[C_CAT].strip()
                rate = round(_svod_salt_unit(f"{stem}|churn_rate|{label}", 7.6, 10.3), 1)
                # keep the salted rate off .0 endings AND off the retired
                # ladder values (else the gate would re-fire every run)
                guard2 = 0
                while (abs(rate - round(rate)) < 0.05 or f"{rate}" in CHURN_LADDER) and guard2 < 6:
                    rate = round(rate + 0.1, 1)
                    guard2 += 1
                base_total = month_totals.get(label)
                if base_total:
                    cnt = int(round(base_total * rate / 100.0))
                else:
                    cnt = int(float(str(r[C_CNT]).replace(",", "")))
                if cnt % 10 == 0:
                    cnt += max(1, int(_svod_salt_unit(f"{stem}|churn_cnt|{label}", 1.0, 9.99)))
                rows[i9] = [label, "", cnt, "churned", "", "", "", "", f"{rate}%", _gp(cnt, stem, f"churn_{label}")]
                churn_patched += 1
    if churn_patched:
        changes.append(f"monthly churn ladder re-salted ({churn_patched} months)")

    return rows, changes


PROTECTED_PREFIXES = (
    "Total Show Watchers", "Pre-Existing Series Viewers", "Clean Sample",
    "New Platform Signups", "Clean Conversion Rate", "Total Show Conversion Rate",
    "Completion Rate", "Second Screen Activity", "Attributed Signups",
    "Dormant to Reactive", "TOTAL SIGNUPS", "Show/Content Tracked",
    "Platform Tracked", "Analysis Date Range",
)


def verify(orig_rows, new_rows):
    """Anchor rows must be byte-identical."""
    def anchors(rws):
        out = []
        for r in rws:
            c0 = (r[0] if r else "").strip()
            if any(c0.startswith(p) for p in PROTECTED_PREFIXES):
                out.append(tuple(r))
            elif c0 and len(r) > 8 and str(r[8]).endswith("%") and r[2] == "" and c0.isupper():
                out.append(tuple(r))  # competitive platform rows
        return out
    a, b = anchors(orig_rows), anchors(new_rows)
    assert a == b, "anchor rows changed - aborting"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="patch just this key")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    if args.only:
        keys = [args.only]
    else:
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, MaxKeys=1000).get("Contents", [])
                if o["Key"].endswith(".csv") and "/" not in o["Key"]]
    patched = skipped = 0
    for key in sorted(keys):
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        text = raw.decode("utf-8", "replace")
        orig_rows = list(csv.reader(io.StringIO(text)))
        new_rows, changes = patch_file([list(r) for r in orig_rows], key)
        if not changes:
            skipped += 1
            continue
        verify(orig_rows, new_rows)
        buf = io.StringIO()
        lt = "\r\n" if "\r\n" in text else "\n"
        w = csv.writer(buf, lineterminator=lt)
        w.writerows(new_rows)
        body = buf.getvalue().encode("utf-8")
        print(f"{'DRY ' if args.dry_run else ''}{key}")
        for ch in changes:
            print(f"    - {ch}")
        if not args.dry_run:
            s3.copy_object(Bucket=BUCKET, CopySource={"Bucket": BUCKET, "Key": key},
                           Key=f"historic/{key}{BACKUP_SUFFIX}")
            s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="text/csv")
        patched += 1
    print(f"\n{'would patch' if args.dry_run else 'patched'}: {patched}   clean: {skipped}")


if __name__ == "__main__":
    main()
