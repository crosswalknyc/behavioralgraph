#!/usr/bin/env python3
"""Final-pass count hygiene for Subscriber IQ tracker CSVs.

One implementation shared by:
  * the engine (SVOD_Churn_Attribution.write_output calls
    apply_to_dataframe on df_out right before to_csv), and
  * the shipped-corpus sweep (scripts/sweep_svod_round_reconcile.py
    calls process_rows on parsed CSV rows from s3://svod-acquisition/).

Guarantees enforced on the summary-level sections (KEY METRICS,
ATTRIBUTION SUMMARY, POST-SIGNUP TOUCHPOINT ANALYSIS, MONTHLY PLATFORM
SIGNUPS / CHURN, DEMOGRAPHICS):

1. Exact reconciliation of printed sums:
   - 1st-5th touchpoint counts sum exactly to the printed
     "Total Platform Signups" count (largest component absorbs the
     rounding residual);
   - the touchpoint total projection equals the sum of the component
     projections;
   - Pre-Existing + Clean Sample = Total Show Watchers;
   - Attributed Signups + Dormant to Reactive = New Platform Signups =
     TOTAL SIGNUPS (counts, and the projection chain where the file
     carries it);
   - each demographic group sums to New Platform Signups when the file
     is anchored to that base. Legacy files anchored to a different but
     internally consistent base keep their base (flagged, not guessed).
2. No positive displayed integer count or projection in those sections
   ends in 0 (trailing zero reads as a placeholder; workspace standard).
   Every nudge is deterministic (md5 of show|platform|label|value, max 9
   units) and paired with a compensating adjustment inside the same sum
   group so every printed total stays exact. Cells that are legitimately
   zero stay zero. Percentages are never touched.
3. COMPETITIVE PLATFORMS rows are listed in descending percentage order.

Deterministic (same file content always produces the same output) and
idempotent (processing a clean file is a no-op). Stdlib only.

Only defective cells are modified; everything else round-trips
byte-identically through the caller.
"""
from __future__ import annotations

import hashlib
import re

# CSV column indexes (Landman 10-column format).
C_CAT = 0      # Category / row label
C_COUNT = 2    # Count
C_CLABEL = 3   # Count Label
C_SEC = 4      # Secondary Count
C_SLABEL = 5   # Secondary Label
C_PCT = 8     # Percentage
C_GP = 9      # Gen Pop Projection
N_COLS = 10

_TOUCH_RE = re.compile(r"^(\d+)(?:st|nd|rd|th) Touchpoint$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# Residual tolerances: differences at or below these are rounding
# artifacts of the panel-scale divisor and are safe to reconcile in
# place. Anything larger means the file is structurally anchored to a
# different base; those are flagged, never auto-patched.
TOL_COUNT = 5          # watchers / signups / demographics count residual
TOL_TOUCH_COUNT = 10   # touchpoint component-sum residual
TOL_GP_ABS = 50        # projection chain residual (observed 1-20)


# ---------------------------------------------------------------------------
# Cell parsing / formatting
# ---------------------------------------------------------------------------

def _pi(cell) -> int | None:
    """Parse a displayed integer (handles commas and float-strings like
    '5824.0'). Returns None when the cell is not an integer value."""
    if cell is None:
        return None
    s = str(cell).strip().strip('"').replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if f != int(f):
        return None
    return int(f)


def _fmt_like(orig, value: int) -> str:
    """Render `value` in the same style as the original cell."""
    s = str(orig).strip()
    if "," in s:
        return f"{value:,}"
    if s.endswith(".0"):
        return f"{value}.0"
    return str(value)


def _pct_val(cell) -> float | None:
    s = str(cell or "").strip()
    if not s.endswith("%"):
        return None
    try:
        return float(s[:-1])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Deterministic nudges
# ---------------------------------------------------------------------------

def _hash(salt: str, *parts) -> int:
    key = "|".join([salt] + [str(p) for p in parts])
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)


def _delta_candidates(h: int):
    """Ordered candidate deltas for a value ending in 0. Every candidate
    leaves the value ending in 1-9 and moves it by at most 9 units. The
    hash rotates the target digit and the preferred direction so nudges
    differ across cells while staying reproducible."""
    start = h % 9
    prefer_up = (h >> 4) % 2 == 0
    out = []
    for k in range(9):
        t = 1 + ((start + k) % 9)
        pair = (t, t - 10) if prefer_up else (t - 10, t)
        out.extend(pair)
    return out


def _ok(v: int | None) -> bool:
    """A touched cell must stay a non-negative integer not ending in 0
    (zero itself ends in 0, so it is excluded too, which is what we want:
    nudges never zero a live cell and never resurrect a zero cell)."""
    return v is not None and v >= 1 and v % 10 != 0


def _pick_delta(h: int, checks) -> int | None:
    """First candidate delta for which every check passes. `checks` is a
    list of callables taking the delta and returning bool."""
    for d in _delta_candidates(h):
        if all(c(d) for c in checks):
            return d
    return None


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------

class _Doc:
    def __init__(self, rows, salt=None):
        # Never mutate caller rows in place.
        self.rows = [list(r) for r in rows]
        self.changes = []   # dicts: row, col, label, before, after, klass
        self.flags = []     # structural notes (no patch applied)
        self._parse()
        self.salt = salt if salt is not None else (
            f"{self.show_tracked}|{self.platform_tracked}")

    # -- helpers ------------------------------------------------------------
    def cell(self, i, col):
        r = self.rows[i]
        return r[col] if col < len(r) else ""

    def geti(self, i, col):
        return _pi(self.cell(i, col))

    def set(self, i, col, value: int, label: str, klass: str):
        r = self.rows[i]
        if len(r) < N_COLS:
            r += [""] * (N_COLS - len(r))
            self.rows[i] = r
        before = r[col]
        after = _fmt_like(before, value)
        if str(before).strip() == after:
            return
        r[col] = after
        self.changes.append({
            "row": i, "col": col, "label": label,
            "before": str(before), "after": after, "klass": klass,
        })

    def flag(self, msg):
        self.flags.append(msg)

    # -- parsing ------------------------------------------------------------
    def _parse(self):
        self.show_tracked = ""
        self.platform_tracked = ""
        self.headline = {}         # label -> row idx
        self.summary = {}          # label -> row idx
        self.touch = []            # (row idx, rank int)
        self.touch_total = None    # row idx
        self.monthly = []          # (row idx, 'signups'|'churned', month str)
        self.demo = {}             # group -> [row idx, ...]
        self.competitive = []      # row idxs in file order
        self.is_tracker = False

        headline_labels = {
            "Total Show Watchers", "Pre-Existing Series Viewers",
            "Clean Sample (New First Time Viewers)", "New Platform Signups",
        }
        summary_labels = {
            "Attributed Signups", "Dormant to Reactive", "TOTAL SIGNUPS",
        }

        section = ""
        demo_group = None
        for i, r in enumerate(self.rows):
            cat = str(r[C_CAT]).strip() if len(r) > C_CAT else ""
            col2 = str(r[C_COUNT]).strip() if len(r) > C_COUNT else ""
            if not cat and col2 and not col2.startswith("(") and _pi(col2) is None:
                section = col2
                if section.startswith("SHOW-TO-PLATFORM ATTRIBUTION"):
                    self.is_tracker = True
                continue
            if cat == "Show/Content Tracked":
                self.show_tracked = str(self.cell(i, C_CLABEL)).strip()
            elif cat == "Platform Tracked":
                self.platform_tracked = str(self.cell(i, C_CLABEL)).strip()
            elif cat in headline_labels:
                self.headline[cat] = i
            elif cat in summary_labels:
                self.summary[cat] = i
            elif _TOUCH_RE.match(cat):
                self.touch.append((i, int(_TOUCH_RE.match(cat).group(1))))
            elif cat == "Total Platform Signups":
                self.touch_total = i
            elif section.startswith("MONTHLY PLATFORM SIGNUPS") and _MONTH_RE.match(cat):
                self.monthly.append((i, "signups", cat))
            elif section.startswith("MONTHLY PLATFORM CHURN") and _MONTH_RE.match(cat):
                self.monthly.append((i, "churned", cat))
            elif section.startswith("DEMOGRAPHICS"):
                if cat in ("AGE", "GENDER") and not str(self.cell(i, C_COUNT)).strip():
                    demo_group = cat
                    self.demo.setdefault(demo_group, [])
                elif demo_group and str(self.cell(i, C_CLABEL)).strip() == "people":
                    self.demo[demo_group].append(i)
            elif section.startswith("COMPETITIVE PLATFORMS"):
                if cat and _pct_val(self.cell(i, C_PCT)) is not None:
                    self.competitive.append(i)

    # -- convenience --------------------------------------------------------
    def hval(self, label):
        i = self.headline.get(label)
        return (i, self.geti(i, C_COUNT)) if i is not None else (None, None)

    def sval(self, label):
        i = self.summary.get(label)
        return (i, self.geti(i, C_COUNT)) if i is not None else (None, None)

    def gp(self, i):
        return self.geti(i, C_GP) if i is not None else None


# ---------------------------------------------------------------------------
# Fix passes
# ---------------------------------------------------------------------------

def _fix_watchers_group(doc: _Doc):
    """Pre-Existing + Clean Sample must equal Total Show Watchers.
    Returns True when the group is sum-consistent after this pass."""
    ti, t = doc.hval("Total Show Watchers")
    pi_, p = doc.hval("Pre-Existing Series Viewers")
    ci, c = doc.hval("Clean Sample (New First Time Viewers)")
    if None in (t, p, c):
        return False
    diff = t - (p + c)
    if diff == 0:
        return True
    if abs(diff) <= TOL_COUNT:
        doc.set(ci, C_COUNT, c + diff, "Clean Sample (New First Time Viewers)",
                "reconciliation")
        return True
    doc.flag(f"watchers group off by {diff} (structural base, left as shipped)")
    return False


def _fix_signups_group(doc: _Doc):
    """Attributed + Dormant = New Platform Signups = TOTAL SIGNUPS."""
    ni, n = doc.hval("New Platform Signups")
    ai, a = doc.sval("Attributed Signups")
    di, d = doc.sval("Dormant to Reactive")
    tsi, ts = doc.sval("TOTAL SIGNUPS")
    if None in (n, a, d):
        return False
    diff = n - (a + d)
    if diff != 0 and abs(diff) <= TOL_COUNT:
        # Larger leg absorbs the residual.
        if a >= d:
            doc.set(ai, C_COUNT, a + diff, "Attributed Signups", "reconciliation")
        else:
            doc.set(di, C_COUNT, d + diff, "Dormant to Reactive", "reconciliation")
        diff = 0
    if diff != 0:
        doc.flag(f"signups group off by {diff} (structural base, left as shipped)")
        return False
    if ts is not None and ts != n:
        if abs(ts - n) <= TOL_COUNT:
            doc.set(tsi, C_COUNT, n, "TOTAL SIGNUPS", "reconciliation")
        else:
            doc.flag(f"TOTAL SIGNUPS {ts} != New Platform Signups {n} (left as shipped)")
    return True


def _fix_touch_counts(doc: _Doc):
    """Touchpoint components must sum exactly to the printed total."""
    if not doc.touch or doc.touch_total is None:
        return False
    vals = [(i, doc.geti(i, C_COUNT)) for i, _ in doc.touch]
    total = doc.geti(doc.touch_total, C_COUNT)
    if total is None or any(v is None for _, v in vals):
        return False
    residual = total - sum(v for _, v in vals)
    if residual == 0:
        return True
    if abs(residual) <= TOL_TOUCH_COUNT:
        li, lv = max(vals, key=lambda x: x[1])
        doc.set(li, C_COUNT, lv + residual,
                str(doc.cell(li, C_CAT)).strip(), "touchpoint_sum")
        return True
    doc.flag(f"touchpoint counts off from total by {residual} (structural, left as shipped)")
    return False


def _fix_demo_groups(doc: _Doc):
    """Each demographic group sums to New Platform Signups when it is
    anchored there. Returns {group: sum_locked_bool}."""
    _, n = doc.hval("New Platform Signups")
    locked = {}
    for group, idxs in doc.demo.items():
        vals = [(i, doc.geti(i, C_COUNT)) for i in idxs]
        if not vals or any(v is None for _, v in vals):
            locked[group] = False
            continue
        s = sum(v for _, v in vals)
        if n is None:
            locked[group] = False
            continue
        diff = n - s
        if diff == 0:
            locked[group] = True
        elif abs(diff) <= TOL_COUNT:
            li, lv = max(vals, key=lambda x: x[1])
            if lv + diff >= 0:
                doc.set(li, C_COUNT, lv + diff,
                        f"{group} {str(doc.cell(li, C_CAT)).strip()}", "reconciliation")
                locked[group] = True
            else:
                locked[group] = False
        else:
            doc.flag(f"{group} demographics sum {s} anchored off New Platform "
                     f"Signups {n} (legacy base, left on its own base)")
            locked[group] = False
    return locked


def _dezero_free(doc: _Doc, i, col, label, klass):
    v = doc.geti(i, col)
    if v is None or v <= 0 or v % 10 != 0:
        return
    h = _hash(doc.salt, label, col, v)
    d = _pick_delta(h, [lambda d: _ok(v + d)])
    if d is not None:
        doc.set(i, col, v + d, label, klass)


def _dezero_pair(doc: _Doc, i, col, partners, label, klass):
    """De-zero cell (i, col) with a sum-neutral compensating adjustment on
    the best available partner cell (largest first)."""
    v = doc.geti(i, col)
    if v is None or v <= 0 or v % 10 != 0:
        return
    h = _hash(doc.salt, label, col, v)
    cands = sorted(
        [(j, doc.geti(j, col)) for j in partners if j != i],
        key=lambda x: -(x[1] or 0))
    for j, pv in cands:
        if pv is None or pv <= 1:
            continue
        d = _pick_delta(h, [lambda d: _ok(v + d), lambda d: _ok(pv - d)])
        if d is not None:
            doc.set(i, col, v + d, label, klass)
            doc.set(j, col, pv - d, f"{label} (compensating)", klass)
            return
    doc.flag(f"could not de-zero {label} without a usable partner (left as shipped)")


def _dezero_cluster(doc: _Doc, cells, klass):
    """Move a whole locked cluster (total plus its absorbing legs) by one
    shared delta so every touched cell ends 1-9 and every sum stays exact.
    `cells` is a list of (row, col, label); the first is the anchor whose
    trailing zero triggered the move."""
    vals = [doc.geti(i, col) for i, col, _ in cells]
    if any(v is None for v in vals):
        return
    anchor = vals[0]
    if anchor <= 0 or anchor % 10 != 0:
        return
    h = _hash(doc.salt, cells[0][2], cells[0][1], anchor)
    d = _pick_delta(h, [lambda d, vv=v: _ok(vv + d) for v in vals])
    if d is None:
        doc.flag(f"could not de-zero {cells[0][2]} cluster (left as shipped)")
        return
    for (i, col, label), v in zip(cells, vals):
        doc.set(i, col, v + d, label, klass)


def _dezero_counts(doc: _Doc, watchers_locked, signups_locked, touch_locked,
                   demo_locked):
    ni, n = doc.hval("New Platform Signups")
    ai, a = doc.sval("Attributed Signups")
    di, d = doc.sval("Dormant to Reactive")
    tsi, _ = doc.sval("TOTAL SIGNUPS")
    ti, t = doc.hval("Total Show Watchers")
    pi_, p = doc.hval("Pre-Existing Series Viewers")
    ci, c = doc.hval("Clean Sample (New First Time Viewers)")

    # New Platform Signups anchors the signups chain and (on modern files)
    # the demographic bases, so its nudge propagates to every locked leg.
    if n is not None and n > 0 and n % 10 == 0:
        cells = [(ni, C_COUNT, "New Platform Signups")]
        if signups_locked and a is not None and d is not None:
            leg = (ai, C_COUNT, "Attributed Signups") if a >= d else \
                  (di, C_COUNT, "Dormant to Reactive")
            cells.append(leg)
            if tsi is not None:
                cells.append((tsi, C_COUNT, "TOTAL SIGNUPS"))
        for group, idxs in doc.demo.items():
            if demo_locked.get(group):
                bi, bv = max(((j, doc.geti(j, C_COUNT)) for j in idxs),
                             key=lambda x: x[1] or 0)
                cells.append((bi, C_COUNT, f"{group} {str(doc.cell(bi, C_CAT)).strip()}"))
        # When the touchpoint total prints the same value as New Platform
        # Signups they are aliases of one number: move them (and the largest
        # touchpoint component, to keep the component sum exact) together.
        if touch_locked and doc.touch and doc.touch_total is not None:
            tt = doc.geti(doc.touch_total, C_COUNT)
            if tt == n:
                li, lv = max(((i, doc.geti(i, C_COUNT)) for i, _ in doc.touch),
                             key=lambda x: x[1] or 0)
                cells.append((doc.touch_total, C_COUNT, "Total Platform Signups"))
                cells.append((li, C_COUNT, str(doc.cell(li, C_CAT)).strip()))
        _dezero_cluster(doc, cells, "trailing_zero")
        _, n = doc.hval("New Platform Signups")

    # Watchers total moves with Clean Sample so Pre + Clean stays exact.
    if t is not None and t > 0 and t % 10 == 0:
        if watchers_locked and c is not None:
            _dezero_cluster(doc, [
                (ti, C_COUNT, "Total Show Watchers"),
                (ci, C_COUNT, "Clean Sample (New First Time Viewers)"),
            ], "trailing_zero")
        else:
            _dezero_free(doc, ti, C_COUNT, "Total Show Watchers", "trailing_zero")
        _, t = doc.hval("Total Show Watchers")
        _, c = doc.hval("Clean Sample (New First Time Viewers)")

    # Pre-Existing and Clean Sample compensate each other under the total.
    if watchers_locked and pi_ is not None and ci is not None:
        _dezero_pair(doc, pi_, C_COUNT, [ci], "Pre-Existing Series Viewers",
                     "trailing_zero")
        _dezero_pair(doc, ci, C_COUNT, [pi_], "Clean Sample (New First Time Viewers)",
                     "trailing_zero")
    else:
        if pi_ is not None:
            _dezero_free(doc, pi_, C_COUNT, "Pre-Existing Series Viewers",
                         "trailing_zero")
        if ci is not None:
            _dezero_free(doc, ci, C_COUNT, "Clean Sample (New First Time Viewers)",
                         "trailing_zero")

    # Attributed / Dormant compensate each other under New Platform Signups.
    if signups_locked and ai is not None and di is not None:
        _dezero_pair(doc, ai, C_COUNT, [di], "Attributed Signups", "trailing_zero")
        _dezero_pair(doc, di, C_COUNT, [ai], "Dormant to Reactive", "trailing_zero")
    else:
        for j, lbl in ((ai, "Attributed Signups"), (di, "Dormant to Reactive")):
            if j is not None:
                _dezero_free(doc, j, C_COUNT, lbl, "trailing_zero")

    # Touchpoints: total moves with its largest component; components
    # compensate among themselves.
    if doc.touch and doc.touch_total is not None:
        tot = doc.geti(doc.touch_total, C_COUNT)
        if touch_locked and tot is not None and tot > 0 and tot % 10 == 0:
            li, lv = max(((i, doc.geti(i, C_COUNT)) for i, _ in doc.touch),
                         key=lambda x: x[1] or 0)
            _dezero_cluster(doc, [
                (doc.touch_total, C_COUNT, "Total Platform Signups"),
                (li, C_COUNT, str(doc.cell(li, C_CAT)).strip()),
            ], "trailing_zero")
        elif not touch_locked:
            _dezero_free(doc, doc.touch_total, C_COUNT, "Total Platform Signups",
                         "trailing_zero")
        comp_idxs = [i for i, _ in doc.touch]
        for i in comp_idxs:
            lbl = str(doc.cell(i, C_CAT)).strip()
            if touch_locked:
                _dezero_pair(doc, i, C_COUNT, comp_idxs, lbl, "trailing_zero")
            else:
                _dezero_free(doc, i, C_COUNT, lbl, "trailing_zero")

    # Monthly platform rows carry no printed total: free nudges.
    for i, kind, month in doc.monthly:
        _dezero_free(doc, i, C_COUNT, f"{month} {kind}", "monthly_round")
        if kind == "signups":
            sec_lbl = str(doc.cell(i, C_SLABEL)).strip()
            if sec_lbl == "watched show":
                _dezero_free(doc, i, C_SEC, f"{month} watched show", "trailing_zero")

    # Demographic buckets compensate inside their group.
    for group, idxs in doc.demo.items():
        for i in idxs:
            lbl = f"{group} {str(doc.cell(i, C_CAT)).strip()}"
            _dezero_pair(doc, i, C_COUNT, idxs, lbl, "trailing_zero")


def _fix_gp_chain(doc: _Doc):
    """Projection chain: New Platform Signups = TOTAL SIGNUPS =
    1st Touchpoint = Attributed + Dormant projections. Returns
    (chain_locked, touch1_aliased)."""
    ni = doc.headline.get("New Platform Signups")
    ai = doc.summary.get("Attributed Signups")
    di = doc.summary.get("Dormant to Reactive")
    tsi = doc.summary.get("TOTAL SIGNUPS")
    t1i = next((i for i, rank in doc.touch if rank == 1), None)
    a_gp, d_gp = doc.gp(ai), doc.gp(di)
    n_gp = doc.gp(ni)
    if a_gp is None or d_gp is None or n_gp is None:
        return False, False
    canon = a_gp + d_gp
    if abs(n_gp - canon) > TOL_GP_ABS:
        doc.flag(f"signups projection chain off by {n_gp - canon} (structural, left as shipped)")
        return False, False
    t1_aliased = (t1i is not None and doc.gp(t1i) == n_gp)

    # De-zero the canonical value itself through its larger leg first.
    if canon > 0 and canon % 10 == 0:
        li, lv = (ai, a_gp) if a_gp >= d_gp else (di, d_gp)
        h = _hash(doc.salt, "signups gp chain", C_GP, canon)
        d = _pick_delta(h, [lambda d: _ok(lv + d), lambda d: _ok(canon + d)])
        if d is not None:
            doc.set(li, C_GP, lv + d, str(doc.cell(li, C_CAT)).strip() + " projection",
                    "trailing_zero")
            canon += d
            a_gp, d_gp = doc.gp(ai), doc.gp(di)

    # Attributed / Dormant projections compensate each other.
    for i, j in ((ai, di), (di, ai)):
        v, pv = doc.gp(i), doc.gp(j)
        if v is not None and v > 0 and v % 10 == 0 and pv is not None and pv > 1:
            lbl = str(doc.cell(i, C_CAT)).strip() + " projection"
            h = _hash(doc.salt, lbl, C_GP, v)
            d = _pick_delta(h, [lambda d: _ok(v + d), lambda d: _ok(pv - d)])
            if d is not None:
                doc.set(i, C_GP, v + d, lbl, "trailing_zero")
                doc.set(j, C_GP, pv - d,
                        str(doc.cell(j, C_CAT)).strip() + " projection (compensating)",
                        "trailing_zero")
    canon = doc.gp(ai) + doc.gp(di)

    # Pin the chain to the canonical sum.
    for idx, lbl in ((ni, "New Platform Signups"), (tsi, "TOTAL SIGNUPS")):
        if idx is None:
            continue
        cur = doc.gp(idx)
        if cur is not None and cur != canon and abs(cur - canon) <= TOL_GP_ABS:
            doc.set(idx, C_GP, canon, f"{lbl} projection", "reconciliation")
        elif cur is not None and cur != canon:
            doc.flag(f"{lbl} projection off chain by {cur - canon} (left as shipped)")
    if t1_aliased and t1i is not None and doc.gp(t1i) != canon:
        doc.set(t1i, C_GP, canon, "1st Touchpoint projection", "reconciliation")
    return True, t1_aliased


def _fix_touch_gp(doc: _Doc, t1_aliased: bool):
    """Touchpoint total projection = sum of component projections. The 1st
    touchpoint projection is pinned to the signups chain and is never used
    as an absorber."""
    if not doc.touch or doc.touch_total is None:
        return
    comp = [(i, rank) for i, rank in doc.touch]
    gps = {i: doc.gp(i) for i, _ in comp}
    tot = doc.gp(doc.touch_total)
    if tot is None or any(v is None for v in gps.values()):
        return
    sum_gp = sum(gps.values())
    consistent = abs(tot - sum_gp) <= max(TOL_GP_ABS, int(0.01 * max(tot, 1)))
    non_first = [i for i, rank in comp if rank != 1]
    if not consistent:
        doc.flag(f"touchpoint projections off from total by {tot - sum_gp} "
                 f"(structural, left as shipped)")
        # Still de-zero cells individually; there is no sum to preserve.
        if not t1_aliased:
            t1i = next((i for i, rank in comp if rank == 1), None)
            if t1i is not None:
                _dezero_free(doc, t1i, C_GP, "1st Touchpoint projection", "trailing_zero")
        for i in non_first:
            _dezero_free(doc, i, C_GP,
                         str(doc.cell(i, C_CAT)).strip() + " projection", "trailing_zero")
        _dezero_free(doc, doc.touch_total, C_GP, "Total Platform Signups projection",
                     "trailing_zero")
        return

    # When the 1st touchpoint projection is not pinned to the signups
    # chain it is an ordinary component: de-zero it sum-neutrally too.
    dezero_targets = list(non_first)
    if not t1_aliased:
        t1i = next((i for i, rank in comp if rank == 1), None)
        if t1i is not None:
            dezero_targets = [t1i] + dezero_targets

    # De-zero component projections sum-neutrally (absorbers are always
    # non-first components so the chain pin is never disturbed).
    for i in dezero_targets:
        lbl = str(doc.cell(i, C_CAT)).strip() + " projection"
        v = doc.gp(i)
        if v is None or v <= 0 or v % 10 != 0:
            continue
        h = _hash(doc.salt, lbl, C_GP, v)
        partners = sorted([j for j in non_first if j != i],
                          key=lambda j: -(doc.gp(j) or 0))
        done = False
        for j in partners:
            pv = doc.gp(j)
            if pv is None or pv <= 1:
                continue
            d = _pick_delta(h, [lambda d: _ok(v + d), lambda d: _ok(pv - d)])
            if d is not None:
                doc.set(i, C_GP, v + d, lbl, "trailing_zero")
                doc.set(j, C_GP, pv - d,
                        str(doc.cell(j, C_CAT)).strip() + " projection (compensating)",
                        "trailing_zero")
                done = True
                break
        if not done:
            doc.flag(f"could not de-zero {lbl} (left as shipped)")

    # Total projection is the sum of the components, with a clean last digit.
    new_sum = sum(doc.gp(i) for i, _ in comp)
    if new_sum > 0 and new_sum % 10 == 0 and non_first:
        li = max(non_first, key=lambda j: doc.gp(j) or 0)
        lv = doc.gp(li)
        h = _hash(doc.salt, "touch total projection", C_GP, new_sum)
        d = _pick_delta(h, [lambda d: _ok(lv + d), lambda d: _ok(new_sum + d)])
        if d is not None:
            doc.set(li, C_GP, lv + d,
                    str(doc.cell(li, C_CAT)).strip() + " projection", "trailing_zero")
            new_sum += d
    if doc.gp(doc.touch_total) != new_sum:
        doc.set(doc.touch_total, C_GP, new_sum, "Total Platform Signups projection",
                "touchpoint_sum")


def _dezero_remaining_gp(doc: _Doc):
    """Headline, monthly and demographic projections carry no printed sum
    of their own: free deterministic nudges."""
    for lbl, i in doc.headline.items():
        if lbl == "New Platform Signups":
            continue  # chain-managed
        _dezero_free(doc, i, C_GP, f"{lbl} projection", "trailing_zero")
    for i, kind, month in doc.monthly:
        _dezero_free(doc, i, C_GP, f"{month} {kind} projection", "trailing_zero")
    for group, idxs in doc.demo.items():
        for i in idxs:
            lbl = f"{group} {str(doc.cell(i, C_CAT)).strip()} projection"
            _dezero_free(doc, i, C_GP, lbl, "trailing_zero")


def _fix_competitive_order(doc: _Doc):
    idxs = doc.competitive
    if len(idxs) < 2:
        return
    pcts = [_pct_val(doc.cell(i, C_PCT)) for i in idxs]
    if any(v is None for v in pcts):
        return
    if all(pcts[k] >= pcts[k + 1] for k in range(len(pcts) - 1)):
        return
    order = sorted(range(len(idxs)), key=lambda k: -pcts[k])
    originals = [list(doc.rows[i]) for i in idxs]
    for slot, k in zip(idxs, order):
        doc.rows[slot] = originals[k]
    doc.changes.append({
        "row": idxs[0], "col": C_PCT, "label": "COMPETITIVE PLATFORMS",
        "before": "unsorted", "after": "sorted desc", "klass": "rank_order",
    })


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_rows(rows, salt=None):
    """Run every hygiene pass over parsed CSV rows.

    Returns (new_rows, report) where report has:
      changes: list of per-cell change dicts (empty means already clean)
      flags:   structural notes for cells deliberately left as shipped
      is_tracker: False when the file is not a standard tracker CSV
    """
    doc = _Doc(rows, salt=salt)
    if not doc.is_tracker or "Total Show Watchers" not in doc.headline:
        return doc.rows, {"changes": [], "flags": ["not a standard tracker CSV"],
                          "is_tracker": False}

    watchers_locked = _fix_watchers_group(doc)
    signups_locked = _fix_signups_group(doc)
    touch_locked = _fix_touch_counts(doc)
    demo_locked = _fix_demo_groups(doc)
    _dezero_counts(doc, watchers_locked, signups_locked, touch_locked, demo_locked)
    chain_locked, t1_aliased = _fix_gp_chain(doc)
    if not chain_locked:
        # No usable chain: the signups projection is a free cell.
        ni = doc.headline.get("New Platform Signups")
        if ni is not None:
            _dezero_free(doc, ni, C_GP, "New Platform Signups projection",
                         "trailing_zero")
        for lbl in ("Attributed Signups", "Dormant to Reactive", "TOTAL SIGNUPS"):
            i = doc.summary.get(lbl)
            if i is not None:
                _dezero_free(doc, i, C_GP, f"{lbl} projection", "trailing_zero")
    _fix_touch_gp(doc, t1_aliased)
    _dezero_remaining_gp(doc)
    _fix_competitive_order(doc)

    return doc.rows, {"changes": doc.changes, "flags": doc.flags,
                      "is_tracker": True}


def apply_to_dataframe(df_out, salt=None):
    """Engine adapter: run the hygiene passes over a write_output df_out
    (Landman 10-column DataFrame) and write only the changed cells back.
    Returns (df_out, change_descriptions)."""
    import pandas as pd  # engine always has pandas; sweep does not need it

    def render(v):
        if v is None:
            return ""
        try:
            if pd.isna(v):
                return ""
        except (TypeError, ValueError):
            pass
        return str(v)

    cols = list(df_out.columns)
    rows = [[render(v) for v in rec] for rec in df_out.itertuples(index=False, name=None)]
    new_rows, report = process_rows(rows, salt=salt)
    descriptions = []
    for ch in report["changes"]:
        if ch["klass"] == "rank_order":
            continue  # handled below by full-section row rewrite
        i, col = ch["row"], ch["col"]
        new_val = ch["after"]
        if col in (C_COUNT, C_SEC) and "," not in new_val and "." not in new_val:
            df_out.iloc[i, col] = int(new_val)
        else:
            df_out.iloc[i, col] = new_val
        descriptions.append(
            f"{ch['label']}: {ch['before']} -> {ch['after']} [{ch['klass']}]")
    if any(ch["klass"] == "rank_order" for ch in report["changes"]):
        # Rewrite the competitive block rows wholesale in their new order.
        doc = _Doc(rows)
        for i in doc.competitive:
            for col in range(min(len(new_rows[i]), len(cols))):
                df_out.iloc[i, col] = new_rows[i][col]
        descriptions.append("COMPETITIVE PLATFORMS re-sorted descending [rank_order]")
    for fl in report["flags"]:
        descriptions.append(f"note: {fl}")
    return df_out, descriptions
