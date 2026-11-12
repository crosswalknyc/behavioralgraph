"""A cut never carries its parent's people counts on a smaller sample (2026-11-12).

`HAPPYS PLACE - Avid Fan.csv` holds 73,334 people against its parent's
231,357. Of the 4,861 rows the two files share, 3,675 carried exactly
the parent's `Original Raw Numbers`: Tubi 48,290 people in both files
(20.87 on the parent, 65.85 on the cut), MGM+ 8,555 in both (3.70 and
11.67). Each such row's reach is the parent's times the sample ratio
(3.155), a flat multiplier, and the file says every person in the
parent who touched a brand is in the cut. Rows whose multiple passed
100 were clamped under it: Snack Food, American Food, Technology, Jelly
Roll, Gordon Ramsay and Snoop Dogg read 99 or higher.

The signature, in one place (pure; no network):

    measure(parent_df, cut_df)        how much of the cut carries the parent's counts
    carried_rows(parent_df, cut_df)   the rows that do
    has_signature(m)                  is this the defect

A row "carries the parent's count" when the cut's sample is smaller
than the parent's and the row's people count equals the parent's for
the same category and value. A count under five is not counted (small
counts match by chance), nor is a pin (a row the parent holds at 100:
everybody in the parent, so everybody in the cut), nor a metadata row.
A file has the signature when a fifth or more of the rows it shares
with its parent carry the parent's count: a reasoned cut shares a
handful by chance, never a fifth. The same files' demographic groups
are the parent's, share for share ('demos_copied'): the build that left
the counts in place never set the demographics for the cut either.

The correction is not arithmetic. `migration/cut_parent_counts_fix.py`
re-reasons every carried row for the cut's own audience with the house
avid reasoning, row by row, and the writer's gate calls it on any cut
that reaches it with the signature (corrected in place, never held).
"""
from __future__ import annotations

import re

MIN_COUNT = 25           # a parent count under this matches a cut's by chance (small samples round alike)
SHARE_FLAG = 0.20        # a fifth of the shared rows carrying the parent's count is the defect
MIN_SHARED = 40          # too few shared rows to say anything
SMALLER = 0.98           # the cut's sample is smaller than the parent's by more than rounding
MIN_MULTIPLE = 1.05      # a flat multiple this close to 1 is a cut that reads like its parent, another matter
PIN = 99.995
NEAR_TOTAL = 99.0        # a row the parent holds this high is a pin (its own property, its seed service): never a carried row
META = frozenset({"BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT", "INPUT_METADATA", "INPUT METADATA",
                  "BRAND ID", "REPORT INPUT", "AVID FAN", "CASUAL FAN"})
DEMOS = frozenset({"GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION", "PARENTAL_STATUS", "RELATIONSHIP",
                   "SEXUAL_ORIENTATION"})
GEO = frozenset({"LOCATION", "DMA", "REGION"})
_NUM_RX = re.compile(r"[%,\s]")


def num(v):
    try:
        s = _NUM_RX.sub("", str(v))
        if s in ("", "nan", "None"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def reach_from_count(count, sample):
    """A row's reach from its people count over the sample that count
    was taken on. For a parent row whose penetration cell cannot be
    read: the reach is the PARENT's count over the PARENT's sample, so
    the cut engines can reason the row like any other. None when it
    cannot be worked out."""
    n, s = num(count), num(sample)
    if n is None or not s or s <= 0 or n < 0:
        return None
    v = n / s * 100.0
    return round(v, 4) if 0.0 <= v <= 100.0 else None


def _key(col, val):
    return (str(col or "").strip().upper(), re.sub(r"\s+", " ", str(val or "").strip().upper()))


def _table(rows):
    """{(COLUMN, VALUE): (bp, raw)} and the sample, from CSV rows
    (Column, Value, BP, Category Share, Raw, Projection, ...)."""
    out, sample = {}, None
    for r in rows:
        if len(r) < 5:
            continue
        cu = str(r[0]).strip().upper()
        if cu == "SAMPLE SIZE" and sample is None:
            sample = num(r[4])
        if cu == "BRAND INPUT" and sample is None:
            sample = num(r[4])
        if cu in META:
            continue
        bp, raw = num(r[2]), num(r[4])
        if bp is None:
            continue
        out.setdefault(_key(r[0], r[1]), (bp, raw))
    return out, sample


def _rows_of_df(df):
    cols = list(df.columns)
    bp = next((c for c in cols if "Brand Penetration" in c), cols[2])
    raw = next((c for c in cols if "Original Raw" in c), cols[4])
    return [[r["Column"], r["Value"], r[bp], "", r[raw]] for _, r in df.iterrows()]


def _pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * q))], 4)


def _multiple(pairs):
    """The one multiple most rows share, if there is one: the mode of
    cut reach over parent reach across rows read precisely enough to
    tell (parent reach of half a point or more). None when no multiple
    stands out."""
    buckets = {}
    for pbp, cbp in pairs:
        if pbp >= 0.5:
            b = round(cbp / pbp, 3)
            buckets[b] = buckets.get(b, 0) + 1
    if not buckets:
        return None
    k = max(buckets, key=lambda b: buckets[b] + buckets.get(round(b - 0.001, 3), 0) + buckets.get(round(b + 0.001, 3), 0))
    near = [(pbp, cbp) for pbp, cbp in pairs if pbp >= 0.5 and abs(cbp / pbp - k) <= 0.0015]
    if len(near) < 20:
        return None
    return sum(c for _, c in near) / sum(p_ for p_, _ in near)


def _on_multiple(pbp, cbp, k, cut_sample=0) -> bool:
    """The cut's reach is the parent's times ``k``: to the four decimals
    both are written in, and to the one person a count is rounded to
    (a reach rebuilt from a whole count moves by up to half a person)."""
    if k is None:
        return False
    tol = 0.00005 * (k + 1) + (60.0 / cut_sample if cut_sample else 0.0) + 1e-9
    return abs(cbp - k * pbp) <= tol


def _near_multiple(pbp, cbp, k) -> bool:
    """A carried row that a later pass nudged: still within four percent
    of the file's multiple, where that multiple is far from 1 (1.5 or
    more). No reasoned cut row sits at three times its parent's reach by
    chance; in a file with the signature, one that does is the defect."""
    return k is not None and k >= 1.5 and pbp > 0 and abs(cbp / pbp - k) <= 0.04 * k


def measure_rows(parent_rows, cut_rows) -> dict:
    """The signature's measure from two files' rows (see ``measure``)."""
    P, ps = _table(parent_rows[1:] if parent_rows and str(parent_rows[0][0]).strip() == "Column" else parent_rows)
    C, cs = _table(cut_rows[1:] if cut_rows and str(cut_rows[0][0]).strip() == "Column" else cut_rows)
    out = {"parent_sample": int(ps or 0), "cut_sample": int(cs or 0), "shared": 0, "same": 0, "share": 0.0,
           "flat": 0, "flat_share": 0.0, "multiple": None, "carried": 0, "carried_share": 0.0,
           "demo_shared": 0, "demo_same": 0, "demo_equal": 0, "demos_copied": False,
           "rows_90": 0, "rows_99": 0, "ratio_p10": None, "ratio_median": None,
           "ratio_p90": None, "sample_ratio": None, "flag": False}
    if not ps or not cs:
        return out
    out["sample_ratio"] = round(ps / cs, 4)
    smaller = cs < ps * SMALLER
    pairs, rows = [], []
    for k, (cbp, craw) in C.items():
        if k[0] in DEMOS:
            if k in P:
                pbp, praw = P[k]
                out["demo_shared"] += 1
                if smaller and praw is not None and craw is not None and praw >= MIN_COUNT and round(praw) == round(craw):
                    out["demo_same"] += 1
                if f"{cbp:.4f}" == f"{pbp:.4f}":
                    out["demo_equal"] += 1
            continue
        if cbp >= 90.0 and cbp < PIN and k[0] not in GEO:
            out["rows_90"] += 1
            if cbp >= 99.0:
                out["rows_99"] += 1
        if k not in P:
            continue
        pbp, praw = P[k]
        if pbp >= NEAR_TOTAL or praw is None or craw is None or praw < MIN_COUNT:
            continue
        out["shared"] += 1
        same = smaller and round(praw) == round(craw)
        out["same"] += 1 if same else 0
        if pbp > 0:
            pairs.append((pbp, cbp))
        rows.append((pbp, cbp, same))
    mult = _multiple(pairs) if smaller else None
    if mult is not None and mult < MIN_MULTIPLE:
        mult = None
    out["multiple"] = round(mult, 4) if mult else None
    for pbp, cbp, same in rows:
        flat = pbp > 0 and _on_multiple(pbp, cbp, mult, cs)
        out["flat"] += 1 if flat else 0
        out["carried"] += 1 if (same or flat) else 0
    if out["shared"]:
        out["share"] = round(out["same"] / out["shared"], 4)
        out["flat_share"] = round(out["flat"] / out["shared"], 4)
        out["carried_share"] = round(out["carried"] / out["shared"], 4)
    # the cut's demographics are the parent's, share for share: never set for the cut's own audience
    out["demos_copied"] = bool(smaller and out["demo_shared"] >= 10
                               and (out["demo_equal"] >= 0.8 * out["demo_shared"] or out["demo_same"] >= 0.5 * out["demo_shared"]))
    ratios = [c / p_ for p_, c in pairs]
    out["ratio_p10"], out["ratio_median"], out["ratio_p90"] = _pct(ratios, 0.10), _pct(ratios, 0.50), _pct(ratios, 0.90)
    out["flag"] = has_signature(out)
    return out


def measure(parent_df, cut_df) -> dict:
    """How much of a cut carries its parent's counts: {'cut_sample',
    'parent_sample', 'shared' (rows both files hold, pins and small
    counts aside), 'same' (of those, the rows carrying exactly the
    parent's count), 'flat' (the rows whose reach is the parent's times
    one multiple, 'multiple'), 'carried' (either), their shares,
    'demo_same', 'rows_90', 'rows_99', 'ratio_p10' / 'ratio_median' /
    'ratio_p90' (the cut's reach over the parent's, row by row),
    'sample_ratio', 'flag'}."""
    return measure_rows(_rows_of_df(parent_df), _rows_of_df(cut_df))


def has_signature(m) -> bool:
    """A fifth or more of the shared rows carry the parent's count, or
    sit on one flat multiple of the parent's reach (the same defect
    after the cut's sample was later re-jittered, so the counts no
    longer match to the person)."""
    if not m or m.get("shared", 0) < MIN_SHARED or not m.get("cut_sample") or not m.get("parent_sample"):
        return False
    if not m["cut_sample"] < m["parent_sample"] * SMALLER:
        return False
    by_count = m.get("share", 0.0) >= SHARE_FLAG
    by_multiple = bool(m.get("multiple")) and m.get("flat_share", 0.0) >= SHARE_FLAG
    return bool(by_count or by_multiple)


def carried_rows(parent_df, cut_df, include_demos=False, multiple=None) -> list:
    """[(index in cut_df, COLUMN, VALUE, cut bp, parent bp, how)] for
    every row of the cut that is the parent's row carried over: it
    holds exactly the parent's count ('count'), its reach is the
    parent's times the file's flat multiple ('multiple'), or the
    parent's count is larger than the whole cut ('over': the multiple
    passed 100 and the row was clamped or patched under it), or it
    still sits within four percent of the multiple after a later pass
    nudged it ('near the multiple')."""
    P, ps = _table(_rows_of_df(parent_df))
    cols = list(cut_df.columns)
    bp = next((c for c in cols if "Brand Penetration" in c), cols[2])
    raw = next((c for c in cols if "Original Raw" in c), cols[4])
    C, cs = _table(_rows_of_df(cut_df))
    if not ps or not cs or not cs < ps * SMALLER:
        return []
    pairs = [(P[k][0], cbp) for k, (cbp, craw) in C.items()
             if k[0] not in DEMOS and k in P and P[k][0] > 0 and P[k][0] < NEAR_TOTAL and (P[k][1] or 0) >= MIN_COUNT]
    mult = float(multiple) if multiple else _multiple(pairs)     # a second pass names the multiple the first one removed
    if mult is not None and mult < MIN_MULTIPLE:
        mult = None
    if not multiple and not measure_rows(_rows_of_df(parent_df), _rows_of_df(cut_df)).get("flag"):
        return []                # a file without the signature has no carried rows: a count that matches by chance is not one
    out = []
    for idx, r in cut_df.iterrows():
        k = _key(r["Column"], r["Value"])
        if k[0] in META or (k[0] in DEMOS and not include_demos) or k not in P:
            continue
        pbp, praw = P[k]
        cbp, craw = num(r[bp]), num(r[raw])
        if cbp is None or praw is None or pbp >= NEAR_TOTAL or pbp <= 0:
            continue
        how = ""
        if craw is not None and praw >= 1 and round(praw) == round(craw):
            how = "count"
        elif _on_multiple(pbp, cbp, mult, cs):
            how = "multiple"
        elif _near_multiple(pbp, cbp, mult):
            how = "near the multiple"
        elif praw > cs or (mult and mult * pbp >= 100.0):
            how = "over"
        if how:
            out.append((idx, k[0], k[1], cbp, pbp, how))
    return out
