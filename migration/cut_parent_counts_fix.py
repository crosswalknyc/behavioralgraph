"""Correct a cut that carries its parent's people counts (2026-11-12).

The defect is measured in `migration/cut_parent_counts.py`. This is the
correction, and it is not arithmetic: every carried row is reasoned for
the cut's own audience from the parent's row by the house row-by-row
reasoning, the same call a new Avid Fan build makes
(`avid_fan_row_by_row.reason_category_rows`: the whole category shown,
every row, in chunks, no multiplier, no flat lift, no top-N). A row the
reasoner does not return sits at the parent's value with the salted
jitter that module already uses for an unreasoned row.

What is kept: the key, the sample, the demographics (unless they carry
the parent's counts too), every pin the parent holds at 100, BRAND
INPUT, BRAND CATEGORY, any row the caller names (`keep`), and every row
that is not the parent's row carried over.

After the rows are set the frame goes through the chain a new cut goes
through, one step at a time, each step kept only if the frame still
holds its invariants (the sample, the pins, the kept rows, demographic
sums, every reach between 0 and 100): no-collision against the parent,
subset coherence, the ratio-collapse guard, the post-generation
enforcers, the total-universe coherence check, the write safety net,
the terminal re-cap and the cut write gate with the final ship gate
(report only), then the Gen Pop baseline columns. A step that breaks
an invariant is reverted, alone, and named in the report.

    correct(cut_df, parent_df, subject=..., cut_key=..., ...) -> (df, report)

Pure with respect to storage: nothing here reads or writes the bucket.
`scripts/fix_cut_parent_counts.py` is the in-place tool; the cut write
gate calls `correct_if_carried` on any cut that reaches it with the
signature, so one is corrected before it publishes, never held.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from migration import cut_parent_counts as CPC
except ImportError:                                     # pragma: no cover
    import cut_parent_counts as CPC  # type: ignore

__all__ = ["correct", "correct_if_carried", "audience_of", "UsageMeter", "frame_invariants"]

BP = "Brand Penetration (Row)"
RAW = "Original Raw Numbers"
_PLACEHOLDERS = (12.3456, 0.4271, 47.8312)
US_PER_PANEL = 32.99
_AVID_RX = re.compile(r"\bavid\b", re.I)


# ------------------------------------------------------------------ helpers
def _cols(df):
    cols = list(df.columns)
    bp = next((c for c in cols if "Brand Penetration" in c), cols[2])
    cs = next((c for c in cols if "Category Share" in c), cols[3])
    raw = next((c for c in cols if "Original Raw" in c), cols[4])
    proj = next((c for c in cols if "Gen Pop Projection" in c), cols[5])
    return bp, cs, raw, proj


def _meta(df, col, field="Value"):
    r = df[df["Column"].astype(str).str.upper().str.strip() == col]
    return str(r.iloc[0][field]) if len(r) else ""


def _sample(df):
    _bp, _cs, raw, proj = _cols(df)
    s = CPC.num(_meta(df, "SAMPLE SIZE", raw)) or CPC.num(_meta(df, "BRAND INPUT", raw))
    u = CPC.num(_meta(df, "SAMPLE SIZE", proj)) or CPC.num(_meta(df, "BRAND INPUT", proj))
    return (int(round(s)) if s else 0), (int(round(u)) if u else 0)


def audience_of(cut_df, parent_df, label, subject) -> dict:
    """What the reasoner is told about the cohort: its share of the
    parent and its own demographics, read from the cut file."""
    cs, _ = _sample(cut_df)
    ps, _ = _sample(parent_df)
    bp = _cols(cut_df)[0]
    targets = {}
    for d in sorted(CPC.DEMOS):
        rows = cut_df[cut_df["Column"].astype(str).str.upper().str.strip() == d]
        b = {str(r["Value"]): CPC.num(r[bp]) for _, r in rows.iterrows() if CPC.num(r[bp]) is not None}
        if b:
            targets[d] = b
    what = ("the Avid Fan tier: the most engaged, devoted people in this audience" if _AVID_RX.search(str(label or ""))
            else f"the {label} cut of this audience")
    return {"cohort_fraction": (cs / ps) if cs and ps else 0.0,
            "reasoning": f"{what} ({subject}); its demographics are the ones on its own file",
            "audience_demo_targets": targets}


class UsageMeter:
    """The model spend of the calls made while it is open, read from the
    house usage ledger (migration/usage_tracker: every call the client
    makes lands there, priced at the house rates)."""

    def __init__(self):
        self._report = {}

    @staticmethod
    def _ledger():
        try:
            from migration import usage_tracker as ut
        except ImportError:                             # pragma: no cover
            import usage_tracker as ut  # type: ignore
        return ut

    def __enter__(self):
        try:
            self._ledger().get_process_report(pop=True)          # start from zero
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            self._report = self._ledger().get_process_report(pop=True) or {}
        except Exception:
            self._report = {}

    def report(self) -> dict:
        r = self._report or {}
        return {"calls": int(r.get("calls") or 0), "input_tokens": int(r.get("input_tokens") or 0),
                "output_tokens": int(r.get("output_tokens") or 0),
                "cache_read_input_tokens": int(r.get("cache_read_input_tokens") or 0),
                "cache_creation_input_tokens": int(r.get("cache_creation_input_tokens") or 0),
                "usd": round(float(r.get("total_usd") or 0.0), 2), "models": sorted((r.get("by_model") or {}).keys()),
                "pricing_estimated": bool(r.get("pricing_estimated"))}


def _default_reason_fn(label):
    """The house reasoning for the cut's kind: the Avid Fan row-by-row
    call for an Avid Fan file, the add-on cut reasoner otherwise."""
    if _AVID_RX.search(str(label or "")):
        try:
            from migration import avid_fan_row_by_row as A
        except ImportError:                             # pragma: no cover
            import avid_fan_row_by_row as A  # type: ignore

        def avid(subject, audience, category, rows, cut_def, parent_df):
            return A.reason_category_rows(subject, audience, category, rows, df_source=parent_df)
        return avid
    try:
        from migration import cut_family as CF
    except ImportError:                                 # pragma: no cover
        import cut_family as CF  # type: ignore
    return CF.house_reasoner


def frame_invariants(df, *, sample, keep_values=None, pins=None, n_rows_min=0) -> list:
    """What must hold on the frame after any step; the list of what does not."""
    bad = []
    try:
        bp, _cs, raw, _proj = _cols(df)
        s, _u = _sample(df)
        if s != sample:
            bad.append(f"the sample moved ({sample} to {s})")
        if len(df) < n_rows_min:
            bad.append(f"rows fell from at least {n_rows_min} to {len(df)}")
        cu = df["Column"].astype(str).str.upper().str.strip()
        vals = df[bp].map(CPC.num)
        body = ~cu.isin(list(CPC.META))
        if (vals[body].dropna() > 100.0001).any() or (vals[body].dropna() < 0).any():
            bad.append("a reach outside 0 to 100")
        for d in CPC.DEMOS:
            m = cu == d
            if m.any():
                tot = float(vals[m].dropna().sum())
                if abs(tot - 100.0) > 0.25:
                    bad.append(f"{d} sums to {tot:.2f}")
        for (c, v), want in (keep_values or {}).items():
            hit = df[(cu == c) & (df["Value"].astype(str).str.strip().str.upper() == v)]
            if len(hit) != 1:
                bad.append(f"kept row {c} / {v} is gone")
        for (c, v) in (pins or ()):
            hit = df[(cu == c) & (df["Value"].astype(str).str.strip().str.upper() == v)]
            if len(hit) == 1 and (CPC.num(hit.iloc[0][bp]) or 0) < 99.0:
                bad.append(f"pin {c} / {v} fell to {hit.iloc[0][bp]}")
    except Exception as e:
        bad.append(f"the frame could not be read ({type(e).__name__}: {e})")
    return bad


def _set_row(df, idx, value, sample, uspop, cols):
    bp, _cs, raw, proj = cols
    had_pct = str(df.at[idx, bp]).strip().endswith("%")
    df.at[idx, bp] = f"{value:.4f}%" if had_pct else f"{value:.4f}"
    people = int(round(sample * value / 100.0))
    df.at[idx, raw] = str(people)
    df.at[idx, proj] = str(int(round(people * US_PER_PANEL)) if not uspop else int(round(uspop * value / 100.0)))


# ------------------------------------------------------------------ the demographics
def reason_demographics(cut_df, parent_df, *, subject, audience_fn=None, log=print):
    """Set the cut's demographic groups from the house audience
    reasoning (the Avid Fan build's first step: how each distribution
    sharpens for the avid slice), for a cut whose demographics are the
    parent's copy. Each group is renormalized to 100 with the salted
    jitter the build uses, so nothing sits on a two-decimal boundary.
    The sample is not touched. Returns (df, stats)."""
    try:
        from migration import avid_fan_row_by_row as A
    except ImportError:                                 # pragma: no cover
        import avid_fan_row_by_row as A  # type: ignore
    df = cut_df.copy()
    cols = _cols(df)
    bp = cols[0]
    for c in cols:
        if df[c].dtype.name not in ("object", "O"):
            df[c] = df[c].astype(object)
    sample, uspop = _sample(df)
    stats = {"reasoned": False, "rows": 0, "groups": 0}
    try:
        if audience_fn is None:
            try:
                from migration.super_fan_synthesis import build_source_snapshot
            except ImportError:                         # pragma: no cover
                from super_fan_synthesis import build_source_snapshot  # type: ignore
            snap = build_source_snapshot(parent_df)
            snap["subject"] = subject or snap.get("subject")
            audience = A.reason_avid_audience(snap)
        else:
            audience = audience_fn(parent_df, subject)
    except Exception as e:
        log(f"[parent-counts] the audience reasoning raised ({type(e).__name__}: {e}); demographics left as they are")
        return cut_df, stats
    targets = {str(k).strip().upper(): v for k, v in (audience.get("audience_demo_targets") or {}).items() if isinstance(v, dict)}
    if not targets or audience.get("claude_used") is False:
        log("[parent-counts] the audience reasoning returned no demographic targets; demographics left as they are")
        return cut_df, stats
    p_bp = _cols(parent_df)[0]
    pcu = parent_df["Column"].astype(str).str.upper().str.strip()
    cu = df["Column"].astype(str).str.upper().str.strip()
    for d in sorted(CPC.DEMOS):
        idxs = list(df.index[cu == d])
        buckets = {re.sub(r"\s+", " ", str(k).strip().upper()): float(v) for k, v in (targets.get(d) or {}).items()
                   if CPC.num(v) is not None}
        if not idxs:
            continue                                    # (a group the reasoning did not name takes the build's fallback below)
        parent_vals = {re.sub(r"\s+", " ", str(v).strip().upper()): CPC.num(b)
                       for v, b in zip(parent_df.loc[pcu == d, "Value"], parent_df.loc[pcu == d, p_bp])}
        vals = []
        for idx in idxs:
            lab = re.sub(r"\s+", " ", str(df.at[idx, "Value"]).strip().upper())
            old = CPC.num(df.at[idx, bp]) or 0.0
            new = buckets.get(lab)
            if new is None:
                # the build's own fallback for a bucket the reasoning did not name
                new = old + A._seed_jitter(f"{subject}|{d}|{lab}|avid-demo-fallback", span=2.0)
            new += A._seed_jitter(f"{subject}|{d}|{lab}|avid-demo-renorm", span=0.10)
            vals.append((idx, lab, max(0.01, new)))
        total = sum(v for _, _, v in vals)
        if total <= 0:
            continue
        normed = [(idx, lab, round(v * 100.0 / total, 4)) for idx, lab, v in vals]
        # the rounding remainder goes to the largest bucket, so the group sums to 100.0000
        rem = round(100.0 - sum(v for _, _, v in normed), 4)
        if abs(rem) >= 0.00005:
            j = max(range(len(normed)), key=lambda i: normed[i][2])
            normed[j] = (normed[j][0], normed[j][1], round(normed[j][2] + rem, 4))
        for idx, lab, v in normed:
            pv = parent_vals.get(lab)
            if pv is not None and f"{v:.4f}" == f"{pv:.4f}":
                continue                                # resolved below, zero-sum
            _set_row(df, idx, v, sample, uspop, cols)
            df.at[idx, cols[1]] = f"{v:.4f}"
            stats["rows"] += 1
        # never a copy of the parent's share: a matching pair of buckets trades a few ten-thousandths
        same = [(idx, lab) for idx, lab, v in normed if parent_vals.get(lab) is not None and f"{v:.4f}" == f"{parent_vals[lab]:.4f}"]
        if same:
            other = max((x for x in normed if (x[0], x[1]) not in same), key=lambda x: x[2], default=None)
            for k, (idx, lab) in enumerate(same):
                v = next(x[2] for x in normed if x[0] == idx)
                step = 0.0003 + 0.0001 * k
                _set_row(df, idx, round(v + step, 4), sample, uspop, cols)
                df.at[idx, cols[1]] = f"{v + step:.4f}"
                if other is not None:
                    ov = CPC.num(df.at[other[0], bp])
                    _set_row(df, other[0], round(ov - step, 4), sample, uspop, cols)
                    df.at[other[0], cols[1]] = f"{ov - step:.4f}"
                stats["rows"] += 1
        stats["groups"] += 1
    stats["reasoned"] = stats["groups"] > 0
    stats["reasoning"] = str(audience.get("reasoning") or "")[:400]
    return df, stats


# ------------------------------------------------------------------ the rows
def reason_rows(cut_df, parent_df, *, subject, label="Avid Fan", keep=(), reason_fn=None, workers=4, multiple=None,
                log=print):
    """Set every carried row of ``cut_df`` from the house reasoning.
    Returns (df, changes, stats). ``changes`` is [{'column','value',
    'before','after','parent','how'}]."""
    try:
        from migration import avid_fan_row_by_row as A
    except ImportError:                                 # pragma: no cover
        import avid_fan_row_by_row as A  # type: ignore
    from concurrent.futures import ThreadPoolExecutor

    df = cut_df.copy()
    cols = _cols(df)
    bp = cols[0]
    for c in cols:
        if df[c].dtype.name not in ("object", "O"):
            df[c] = df[c].astype(object)
    sample, uspop = _sample(df)
    keep_set = {(str(c).strip().upper(), re.sub(r"\s+", " ", str(v).strip().upper())) for c, v in (keep or ())}
    carried = [r for r in CPC.carried_rows(parent_df, df, multiple=multiple) if (r[1], r[2]) not in keep_set]
    by_cat = {}
    for idx, cat, val, cbp, pbp, how in carried:
        by_cat.setdefault(cat, []).append((idx, val, cbp, pbp, how))
    if not carried:
        return df, [], {"carried": 0, "categories": 0, "reasoned": 0, "unreasoned": 0, "misfires": 0}

    # the whole category as the parent holds it: the reasoner sees every row, as on a new build
    p_bp = _cols(parent_df)[0]
    pcu = parent_df["Column"].astype(str).str.upper().str.strip()
    cat_rows = {}
    for cat in by_cat:
        rows, seen = [], set()
        for v, b in zip(parent_df.loc[pcu == cat, "Value"], parent_df.loc[pcu == cat, p_bp]):
            n = CPC.num(b)
            lab = re.sub(r"\s+", " ", str(v).strip())
            if n is None or n >= CPC.NEAR_TOTAL or lab.upper() in seen:
                continue
            seen.add(lab.upper())
            rows.append((lab, n))
        cat_rows[cat] = rows
    audience = audience_of(cut_df, parent_df, label, subject)
    reason_fn = reason_fn or _default_reason_fn(label)
    cut_def = {"label": label, "kind": "behavioral", "cohort_description": audience["reasoning"]}
    order = sorted(by_cat, key=lambda c: -len(cat_rows[c]))
    decisions = {}

    def _one(cat):
        try:
            return cat, (reason_fn(subject, audience, cat, cat_rows[cat], cut_def, parent_df) or {})
        except Exception as e:
            log(f"[parent-counts] {cat}: the reasoner raised ({type(e).__name__}: {e}); the unreasoned-row convention applies")
            return cat, {}
    with ThreadPoolExecutor(max(1, min(workers, len(order)))) as ex:
        for cat, dec in ex.map(_one, order):
            decisions[cat] = {re.sub(r"\s+", " ", str(k).strip().upper()): float(v) for k, v in dec.items()
                              if CPC.num(v) is not None}
            log(f"[parent-counts] {cat}: {len(decisions[cat])} of {len(cat_rows[cat])} rows reasoned "
                f"({len(by_cat[cat])} carried)")
    # a category that came back empty (a dropped call) is asked again, one at a time
    for cat in [c for c in order if not decisions.get(c)]:
        _c, dec = _one(cat)
        decisions[cat] = {re.sub(r"\s+", " ", str(k).strip().upper()): float(v) for k, v in dec.items()
                          if CPC.num(v) is not None}
        log(f"[parent-counts] {cat}: asked again, {len(decisions[cat])} of {len(cat_rows[cat])} rows reasoned")
    try:
        try:
            from migration.fractional_ladders import deladder_decision_map
        except ImportError:                             # pragma: no cover
            from fractional_ladders import deladder_decision_map  # type: ignore
        decisions, _n = deladder_decision_map(decisions, subject)
    except Exception as e:
        log(f"[parent-counts] ladder guard skipped ({e})")

    changes, stats = [], {"carried": len(carried), "categories": len(by_cat), "reasoned": 0, "unreasoned": 0, "misfires": 0}
    for cat, rows in by_cat.items():
        dec = decisions.get(cat, {})
        for idx, val, cbp, pbp, how in rows:
            claude = dec.get(val)
            echoed = claude is not None and any(abs(float(claude) - p) < 0.0005 for p in _PLACEHOLDERS)
            if claude is not None and not echoed:
                new = float(claude)
                if (new >= 75.0 and pbp < 5.0) or (new >= 60.0 and pbp < 1.0):
                    # the house plausibility guard: near-universal reach on a row the parent barely holds is a misfire
                    new = pbp + A._seed_jitter(f"{subject}|{cat}|{val}|avid-misfire-jitter", span=0.10)
                    stats["misfires"] += 1
                    why = "parent value, salted (the reasoned value was a misfire)"
                else:
                    stats["reasoned"] += 1
                    why = "reasoned for the cut"
            else:
                new = pbp + A._seed_jitter(f"{subject}|{cat}|{val}|avid-no-claude-jitter", span=0.10)
                stats["unreasoned"] += 1
                why = "parent value, salted (not returned by the reasoner)"
            new = max(0.0001, min(99.49, round(new, 4)))
            _set_row(df, idx, new, sample, uspop, cols)
            changes.append({"column": cat, "value": str(df.at[idx, "Value"]), "before": round(cbp, 4), "after": new,
                            "parent": round(pbp, 4), "carried": how, "how": why})
    return df, changes, stats


# ------------------------------------------------------------------ rows the reasoner did not return
_WATCH = threading.local()
WHY = ("reasoned", "no answer for its chunk", "the answer for its chunk could not be read", "left out of the answer",
       "answered out of range", "answered with a placeholder", "answered with a misfire",
       "answered with one value for four or more rows (the house ladder guard reads that as no decision)")


def _watch_reasoner(A):
    """Record, per call, what the house reasoner got back: an answer or
    none, whether it could be read, and the labels and values in it.
    The reasoner itself is not changed; it is only observed."""
    if getattr(A, "_cpc_watched", False):
        return
    real_call, real_parse = A._claude_messages_with_retry, A._extract_json_block

    def call(*a, **k):
        r = real_call(*a, **k)
        cap = getattr(_WATCH, "cap", None)
        if cap is not None:
            cap["calls"] = cap.get("calls", 0) + 1
            cap["answered"] = bool(r)
        return r

    def parse(resp):
        o = real_parse(resp)
        cap = getattr(_WATCH, "cap", None)
        if cap is not None:
            cap["read"] = isinstance(o, dict)
            items = (o.get("items") or []) if isinstance(o, dict) else []
            cap["items"] = {re.sub(r"\s+", " ", str(it.get("label", "")).strip().upper()): it.get("new_bp")
                            for it in items if isinstance(it, dict)}
        return o
    A._claude_messages_with_retry, A._extract_json_block = call, parse
    A._cpc_watched = True


def house_chunk_fn(parent_df):
    """One call of the house row reasoner on one chunk of rows, watched:
    (subject, audience, category, [(LABEL, parent reach)]) ->
    ({LABEL: value}, {'answered', 'read', 'items', 'calls'})."""
    try:
        from migration import avid_fan_row_by_row as A
    except ImportError:                                 # pragma: no cover
        import avid_fan_row_by_row as A  # type: ignore
    _watch_reasoner(A)

    def chunk_fn(subject_, audience_, cat, part):
        _WATCH.cap = cap = {}
        try:
            got = A.reason_category_rows(subject_, audience_, cat, part, chunk_size=max(1, len(part)), df_source=parent_df) or {}
        except Exception as e:
            got, cap["raised"] = {}, f"{type(e).__name__}: {e}"[:120]
        finally:
            _WATCH.cap = None
        return {re.sub(r"\s+", " ", str(k).strip().upper()): v for k, v in got.items()}, cap
    return chunk_fn


def reason_rows_again(cut_df, parent_df, *, subject, label="Avid Fan", keep=(), select=None, chunk=60, rounds=3,
                      guard_rounds=2, workers=4, chunk_fn=None, log=print):
    """Send the rows that sit at the parent's value to the house
    reasoner again and say, row by row, what came back.

    The first pass asks for a whole category at a time, 200 rows to a
    call, and a row the answer does not carry falls back to the parent's
    value with the salted jitter. Here only those rows are sent, in
    chunks of ``chunk``, and a row still missing is sent again on its
    own in a smaller chunk, up to ``rounds`` times. The house ladder
    guard then runs as on a build, and a row it drops (the model gave
    four or more rows of the category one identical value) is asked
    again up to ``guard_rounds`` times before it is left at the
    fallback. Returns (df, changes, stats); every change carries
    ``how`` (one of WHY) and stats['why'] counts them.

    ``chunk_fn(subject, audience, category, rows)`` -> ({LABEL: value},
    capture) replaces the house call in a test."""
    from concurrent.futures import ThreadPoolExecutor

    df = cut_df.copy()
    cols = _cols(df)
    for c in cols:
        if df[c].dtype.name not in ("object", "O"):
            df[c] = df[c].astype(object)
    sample, uspop = _sample(df)
    keep_set = {(str(c).strip().upper(), re.sub(r"\s+", " ", str(v).strip().upper())) for c, v in (keep or ())}
    rows = [r for r in (select or CPC.at_parent_rows)(parent_df, df) if (r[1], r[2]) not in keep_set]
    stats = {"sent": len(rows), "categories": len({r[1] for r in rows}), "reasoned": 0, "unreasoned": 0, "misfires": 0,
             "calls": 0, "rounds": 0, "why": {}}
    if not rows:
        return df, [], stats
    audience = audience_of(cut_df, parent_df, label, subject)
    if chunk_fn is None:
        chunk_fn = house_chunk_fn(parent_df)

    values, why = {}, {}                 # (cat, val) -> value / reason it fell back
    pending = {}
    for idx, cat, val, cbp, pbp, _how in rows:
        pending.setdefault(cat, []).append((val, pbp))
    size = max(1, int(chunk))
    for rnd in range(1, max(1, int(rounds)) + 1):
        jobs = []
        for cat, part in pending.items():
            part = sorted(part, key=lambda kv: -kv[1])
            jobs += [(cat, part[i:i + size]) for i in range(0, len(part), size)]
        if not jobs:
            break
        stats["rounds"] = rnd

        def _one(job):
            cat, part = job
            got, cap = chunk_fn(subject, audience, cat, part)
            return cat, part, got, cap
        nxt = {}
        with ThreadPoolExecutor(max(1, min(workers, len(jobs)))) as ex:
            for cat, part, got, cap in ex.map(_one, jobs):
                stats["calls"] += int(cap.get("calls", 1) or 1)
                answered = cap.get("answered", bool(got))
                read = cap.get("read", bool(got))
                items = cap.get("items") or {}
                for val, pbp in part:
                    if val in got:
                        values[(cat, val)] = float(got[val])
                        why.pop((cat, val), None)
                        continue
                    if not answered:
                        why[(cat, val)] = WHY[1]
                    elif not read:
                        why[(cat, val)] = WHY[2]
                    elif val in items:
                        why[(cat, val)] = WHY[4]
                    else:
                        why[(cat, val)] = WHY[3]
                    nxt.setdefault(cat, []).append((val, pbp))
        log(f"[parent-counts] round {rnd}: {len(jobs)} calls of up to {size} rows; "
            f"{sum(len(v) for v in pending.values()) - sum(len(v) for v in nxt.values())} rows answered, "
            f"{sum(len(v) for v in nxt.values())} not")
        pending = nxt
        size = max(8, size // 3)
    # The house ladder guard, as on a build: four or more rows of one category answered with one
    # identical value is a templated answer, not a per-row decision, and those rows (all but one) take
    # the fallback. A row the guard drops is asked again, in a small chunk, before it is left there.
    def _guard(vals):
        try:
            try:
                from migration.fractional_ladders import deladder_decision_map
            except ImportError:                         # pragma: no cover
                from fractional_ladders import deladder_decision_map  # type: ignore
        except Exception as e:                          # pragma: no cover
            log(f"[parent-counts] ladder guard skipped ({e})")
            return vals, set()
        by_cat = {}
        for (cat, val), v in vals.items():
            by_cat.setdefault(cat, {})[val] = v
        with contextlib.redirect_stdout(io.StringIO()):
            by_cat, _n = deladder_decision_map(by_cat, subject)
        kept = {(cat, val): float(v) for cat, d in by_cat.items() for val, v in d.items()}
        return kept, set(vals) - set(kept)

    parent_of = {(cat, val): pbp for _i, cat, val, _c, pbp, _h in rows}
    dropped = set()
    for g in range(max(0, int(guard_rounds)) + 1):
        kept, dropped = _guard(values)
        if not dropped or g == int(guard_rounds):
            values = kept
            break
        jobs, by = [], {}
        for cat, val in sorted(dropped):
            by.setdefault(cat, []).append((val, parent_of[(cat, val)]))
        for cat, part in by.items():
            part = sorted(part, key=lambda kv: -kv[1])
            jobs += [(cat, part[i:i + 20]) for i in range(0, len(part), 20)]
        again = 0
        with ThreadPoolExecutor(max(1, min(workers, len(jobs)))) as ex:
            for cat, part, got, cap in ex.map(lambda job: (job[0], job[1]) + tuple(chunk_fn(subject, audience, job[0], job[1])), jobs):
                stats["calls"] += int(cap.get("calls", 1) or 1)
                for val, _p in part:
                    if val in got:
                        values[(cat, val)] = float(got[val])
                        again += 1
        stats["guard_rounds"] = g + 1
        log(f"[parent-counts] ladder guard: {len(dropped)} rows answered with a value shared by four or more rows; "
            f"asked again in {len(jobs)} calls, {again} answered")
    for k in dropped:
        why[k] = WHY[7]

    changes = []
    for idx, cat, val, cbp, pbp, how in rows:
        got = values.get((cat, val))
        reason = why.get((cat, val), WHY[0] if got is not None else WHY[3])
        if got is not None and any(abs(got - p_) < 0.0005 for p_ in _PLACEHOLDERS):
            got, reason = None, WHY[5]
        if got is not None and ((got >= 75.0 and pbp < 5.0) or (got >= 60.0 and pbp < 1.0)):
            got, reason = None, WHY[6]
            stats["misfires"] += 1
        stats["why"][reason] = stats["why"].get(reason, 0) + 1
        if got is None:
            stats["unreasoned"] += 1          # the row stays where it is: the parent's value with its salted jitter
            changes.append({"column": cat, "value": str(df.at[idx, "Value"]), "before": round(cbp, 4), "after": round(cbp, 4),
                            "parent": round(pbp, 4), "carried": how, "how": reason})
            continue
        new = max(0.0001, min(99.49, round(got, 4)))
        stats["reasoned"] += 1
        _set_row(df, idx, new, sample, uspop, cols)
        changes.append({"column": cat, "value": str(df.at[idx, "Value"]), "before": round(cbp, 4), "after": new,
                        "parent": round(pbp, 4), "carried": how, "how": reason})
    return df, changes, stats


# ------------------------------------------------------------------ the chain
def _chain_steps(subject, parent_df, cut_key, brand_category):
    """The steps a new cut goes through after its rows are set, in order."""
    steps = []
    try:
        from migration import avid_fan_row_by_row as A
    except ImportError:                                 # pragma: no cover
        import avid_fan_row_by_row as A  # type: ignore
    steps.append(("no value equal to the parent's", lambda d: A.enforce_no_collisions(d, parent_df, subject)[0]))
    steps.append(("subset coherence with the parent", lambda d: A.enforce_avid_subset_coherence(d, parent_df, subject, verbose=False)[0]))
    steps.append(("ratio-collapse guard", lambda d: A.enforce_avid_ratio_collapse_guard(d, parent_df, subject, verbose=False)[0]))

    def _enforcers(d):
        try:
            from migration.post_generation_enforcers import run_all_enforcers
        except ImportError:                             # pragma: no cover
            from post_generation_enforcers import run_all_enforcers  # type: ignore
        return run_all_enforcers(d, subject, brand_category=brand_category or None, verbose=False)[0]
    steps.append(("post-generation enforcers", _enforcers))

    def _tu(d):
        try:
            from migration.tu_avid_coherence import enforce_tu_avid_coherence
        except ImportError:                             # pragma: no cover
            from tu_avid_coherence import enforce_tu_avid_coherence  # type: ignore
        return enforce_tu_avid_coherence(parent_df, d, subject, verbose=False)[0]
    steps.append(("total-universe coherence", _tu))

    def _net(d):
        try:
            from migration.post_generation_enforcers import run_write_safety_net
        except ImportError:                             # pragma: no cover
            from post_generation_enforcers import run_write_safety_net  # type: ignore
        return run_write_safety_net(d, subject, verbose=False)[0]
    steps.append(("write safety net", _net))
    steps.append(("terminal subset re-cap", lambda d: A.enforce_avid_subset_coherence(d, parent_df, subject, verbose=False)[0]))
    return steps


def hold_mirrors(df, parent_df, log=None) -> int:
    """One value where the house holds one value (in place; returns the
    rows moved):

      * a person's role row is the person's TALENT row, to the digit;
      * a team's companion rows (league, conference, division, and the
        SPORTS TEAM row where the parent holds it with them) carry one
        value. The groups are read off the parent: rows of one team the
        parent holds within five percent of each other are one group on
        the cut, anchored on SPORTS TEAM, else the league row.

    Purchase rows are not handled here: the enforcer chain's own exact
    mirror to MOST PURCHASED BRANDS covers them. Pins (100) are left."""
    try:
        from migration import post_generation_enforcers as E
    except ImportError:                                 # pragma: no cover
        import post_generation_enforcers as E  # type: ignore
    cols = _cols(df)
    bp = cols[0]
    sample, uspop = _sample(df)
    cu = df["Column"].astype(str).str.upper().str.strip()
    vu = df["Value"].astype(str).map(lambda v: re.sub(r"[^A-Z0-9]", "", str(v).upper()))
    moved = 0
    # people
    fam = set(getattr(E, "_TALENT_FAMILY", ())) - {"TALENT", "SOCCER", "PODCAST"}
    fam |= {c for c in set(cu) if c.endswith(" ATHLETE")}
    talent = {}
    for idx in df.index[cu == "TALENT"]:
        n = CPC.num(df.at[idx, bp])
        if n is not None and n < CPC.PIN:
            talent.setdefault(vu[idx], n)
    for idx in df.index[cu.isin(list(fam))]:
        want, cur = talent.get(vu[idx]), CPC.num(df.at[idx, bp])
        if want is None or cur is None or cur >= CPC.PIN or abs(cur - want) <= 0.00005:
            continue
        _set_row(df, idx, want, sample, uspop, cols)
        moved += 1
    # teams
    house_comp = getattr(E, "_is_companion_pin_cat", lambda c: False)
    team_names = set(vu[cu == "SPORTS TEAM"])
    not_sport = fam | {"TALENT", "MOST PURCHASED BRANDS", "MEDIA", "INTEREST", "SPORTS ORGANIZATIONS", "COLLEGE/UNIVERSITY"} \
        | set(CPC.META) | set(CPC.DEMOS) | set(CPC.GEO)
    mostly_teams = set()
    for c in set(cu) - not_sport - {"SPORTS TEAM"}:
        names = set(vu[cu == c])
        if names and len(names & team_names) >= 0.7 * len(names):
            mostly_teams.add(c)                      # a league column the house list does not name (PREMIER LEAGUE, NWSL)

    def is_comp(c):
        return c not in not_sport and (house_comp(c) or c in mostly_teams)
    pcu = parent_df["Column"].astype(str).str.upper().str.strip()
    pvu = parent_df["Value"].astype(str).map(lambda v: re.sub(r"[^A-Z0-9]", "", str(v).upper()))
    p_bp = _cols(parent_df)[0]
    parent_val = {}
    for c, v, b in zip(pcu, pvu, parent_df[p_bp]):
        n = CPC.num(b)
        if n is not None and (c == "SPORTS TEAM" or is_comp(c)):
            parent_val.setdefault((v, c), n)
    teams = {}
    for idx in df.index[(cu == "SPORTS TEAM") | cu.map(is_comp)]:
        n = CPC.num(df.at[idx, bp])
        if n is not None and n < CPC.PIN:
            teams.setdefault(vu[idx], []).append((idx, cu[idx], n))

    _div = re.compile(r"\b(DIVISION|EAST|WEST|NORTH|SOUTH|CENTRAL|PACIFIC|ATLANTIC|METROPOLITAN)\b")

    def _rank(c):
        if c == "SPORTS TEAM":
            return 0
        if "CONFERENCE" in c or c in ("AFC", "NFC", "AL", "NL"):
            return 2
        return 3 if _div.search(c) else 1            # a league row anchors its conference and division rows
    for team, rows in teams.items():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda r: (_rank(r[1]), r[1]))
        groups = []
        for idx, c, n in rows:
            pv = parent_val.get((team, c))
            for g in groups:
                gp = g["parent"]
                if pv is not None and gp is not None and gp > 0 and abs(pv - gp) <= 0.05 * gp:
                    g["rows"].append((idx, c, n))
                    break
            else:
                groups.append({"parent": pv, "anchor": n, "rows": [(idx, c, n)]})
        for g in groups:
            for idx, c, n in g["rows"][1:]:
                if abs(n - g["anchor"]) > 0.00005:
                    _set_row(df, idx, g["anchor"], sample, uspop, cols)
                    moved += 1
    if moved and callable(log):
        log(f"[parent-counts] mirrors held: {moved} row(s) set to the value of their TALENT or team row")
    return moved


def _quiet(fn, d):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(d)
    return out, buf.getvalue()


def keep_demographics(df, original, log=None) -> int:
    """The cut's demographic groups as they were on the file (rows and
    values): the correction is about rows that carry the parent's
    counts, and a demographic group that does not is right as it is.
    A group that itself carries the parent's counts is left to the
    chain. Returns the rows restored or dropped."""
    cols, ocols = _cols(df), _cols(original)
    sample, uspop = _sample(df)
    cu = df["Column"].astype(str).str.upper().str.strip()
    ocu = original["Column"].astype(str).str.upper().str.strip()
    n, drop = 0, []
    for d in sorted(CPC.DEMOS):
        want = {re.sub(r"\s+", " ", str(v).strip().upper()): CPC.num(b)
                for v, b in zip(original.loc[ocu == d, "Value"], original.loc[ocu == d, ocols[0]])}
        want = {k: v for k, v in want.items() if v is not None}
        if not want:
            continue
        for idx in df.index[cu == d]:
            k = re.sub(r"\s+", " ", str(df.at[idx, "Value"]).strip().upper())
            if k not in want:
                drop.append(idx)                    # a bucket the chain added: not on the file as it was
                n += 1
            elif abs((CPC.num(df.at[idx, cols[0]]) or 0) - want[k]) > 0.00005:
                _set_row(df, idx, want[k], sample, uspop, cols)
                df.at[idx, cols[1]] = f"{want[k]:.4f}"
                n += 1
    if drop:
        df.drop(index=drop, inplace=True)
        df.reset_index(drop=True, inplace=True)
    if n and callable(log):
        log(f"[parent-counts] demographics kept as they were: {n} row(s) restored or dropped")
    return n


def correct(cut_df, parent_df, *, subject, cut_key="", label="Avid Fan", keep=(), reason_fn=None, chain=True,
            gate=True, workers=4, keep_demos=True, audience_fn=None, multiple=None, trace=(), rows_fn=None, log=print):
    """Correct the carried rows of ``cut_df`` and run the frame through
    the cut chain. Returns (df, report). The frame returned is the last
    one that held every invariant; ``report['publish']`` says whether it
    is fit to write (the signature is gone and nothing was left broken)."""
    before = CPC.measure(parent_df, cut_df)
    sample, _u = _sample(cut_df)
    bp = _cols(cut_df)[0]
    cu = cut_df["Column"].astype(str).str.upper().str.strip()
    report = {"cut": cut_key, "before": before, "steps": [], "reverted": [], "changes": [], "publish": False}
    keep_set = {(str(c).strip().upper(), re.sub(r"\s+", " ", str(v).strip().upper())) for c, v in (keep or ())}
    keep_values = {}
    for (c, v) in keep_set:
        hit = cut_df[(cu == c) & (cut_df["Value"].astype(str).str.strip().str.upper() == v)]
        if len(hit) == 1:
            keep_values[(c, v)] = CPC.num(hit.iloc[0][bp])
    p_bp = _cols(parent_df)[0]
    pins = {(str(c).strip().upper(), re.sub(r"\s+", " ", str(v).strip().upper()))
            for c, v, b in zip(parent_df["Column"], parent_df["Value"], parent_df[p_bp])
            if (CPC.num(b) or 0) >= CPC.NEAR_TOTAL and str(c).strip().upper() not in CPC.META and str(c).strip().upper() not in CPC.DEMOS}
    pins = {k for k in pins
            if len(cut_df[(cu == k[0]) & (cut_df["Value"].astype(str).str.strip().str.upper() == k[1])]) == 1
            and (CPC.num(cut_df[(cu == k[0]) & (cut_df["Value"].astype(str).str.strip().str.upper() == k[1])].iloc[0][bp]) or 0) >= 99.0}
    n_min = int(len(cut_df) * 0.97)

    # A cut whose demographics are the parent's copy has them set for the cut first (the build's first
    # step), so the rows are then reasoned for the cut's own audience and not for the parent's.
    demo_base = cut_df
    if before.get("demos_copied") and _AVID_RX.search(str(label or "")):
        demo_df, dstats = reason_demographics(cut_df, parent_df, subject=subject, audience_fn=audience_fn, log=log)
        report["demographics"] = dstats
        if dstats.get("reasoned") and not frame_invariants(demo_df, sample=sample, n_rows_min=len(cut_df)):
            demo_base = demo_df
            log(f"[parent-counts] demographics set for the cut: {dstats['rows']} rows in {dstats['groups']} groups")
    if rows_fn is not None:
        # another way of choosing and reasoning the rows (the rows the reasoner did not return last time)
        df, changes, stats = rows_fn(demo_base, parent_df)
    else:
        df, changes, stats = reason_rows(demo_base, parent_df, subject=subject, label=label, keep=keep, reason_fn=reason_fn,
                                         workers=workers, multiple=multiple, log=log)
    report["changes"], report["rows"] = changes, stats
    report["trace"] = []

    def _trace(d, step):
        if not trace:
            return
        tcu = d["Column"].astype(str).str.upper().str.strip()
        for c, v in trace:
            hit = d[(tcu == str(c).upper()) & (d["Value"].astype(str).str.strip().str.upper() == str(v).upper())]
            report["trace"].append([step, c, v, str(hit.iloc[0][_cols(d)[0]]) if len(hit) else None])
    _trace(df, "rows set from the reasoning")
    bad = frame_invariants(df, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min)
    if bad:
        report["reverted"].append({"step": "rows set from the reasoning", "why": bad})
        report["after"] = before
        return cut_df, report
    if not changes and demo_base is cut_df:
        report["after"] = before
        report["nothing_to_do"] = True
        return cut_df, report

    # a pin keeps its value through the chain: exactly 100 where the parent holds exactly 100 (its own
    # property, inherited untouched), else the near-total the cut already carried
    pin_values = {}
    for c, v, b in zip(parent_df["Column"], parent_df["Value"], parent_df[p_bp]):
        k = (str(c).strip().upper(), re.sub(r"\s+", " ", str(v).strip().upper()))
        if k in pins:
            hit = cut_df[(cu == k[0]) & (cut_df["Value"].astype(str).str.strip().str.upper() == k[1])]
            pin_values[k] = 100.0 if (CPC.num(b) or 0) >= CPC.PIN else CPC.num(hit.iloc[0][bp])

    def _restore_kept(d):
        """A kept row and a pin keep their value through the chain."""
        cols = _cols(d)
        dcu = d["Column"].astype(str).str.upper().str.strip()
        s, u = _sample(d)
        n = 0
        for (c, v), want in list(pin_values.items()):
            hit = d[(dcu == c) & (d["Value"].astype(str).str.strip().str.upper() == v)]
            if len(hit) == 1 and want is not None and abs((CPC.num(hit.iloc[0][cols[0]]) or 0) - want) > 0.00005:
                _set_row(d, hit.index[0], want, s, u, cols)
                n += 1
        for (c, v), want in list(keep_values.items()):
            hit = d[(dcu == c) & (d["Value"].astype(str).str.strip().str.upper() == v)]
            if len(hit) == 1 and want is not None and abs((CPC.num(hit.iloc[0][cols[0]]) or 0) - want) > 0.00005:
                _set_row(d, hit.index[0], want, s, u, cols)
                n += 1
        return n

    if chain:
        bc = _meta(parent_df, "BRAND CATEGORY")
        for name, fn in _chain_steps(subject, parent_df, cut_key, bc):
            try:
                out, _noise = _quiet(fn, df.copy())
                _restore_kept(out)
                bad = frame_invariants(out, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min)
            except Exception as e:
                bad, out = [f"raised {type(e).__name__}: {e}"], None
            if bad:
                report["reverted"].append({"step": name, "why": bad})
                log(f"[parent-counts] step reverted: {name} ({'; '.join(bad)[:200]})")
                continue
            df = out
            report["steps"].append(name)
            _trace(df, name)

    def _mirror_step(d, name):
        try:
            out = d.copy()
            n = hold_mirrors(out, parent_df, log=log)
            if not n:
                return d
            try:
                from migration.post_generation_enforcers import apply_recompute_category_share
            except ImportError:                         # pragma: no cover
                from post_generation_enforcers import apply_recompute_category_share  # type: ignore
            out, _noise = _quiet(lambda x: apply_recompute_category_share(x, subject, verbose=False)[0], out)
            try:
                from migration.profile_writer import _sort_within_category
            except ImportError:                         # pragma: no cover
                from profile_writer import _sort_within_category  # type: ignore
            out = _sort_within_category(out)
            bad = frame_invariants(out, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min)
            if bad:
                report["reverted"].append({"step": name, "why": bad})
                return d
            report["steps"].append(f"{name} ({n} rows)")
            return out
        except Exception as e:
            report["reverted"].append({"step": name, "why": [f"raised {type(e).__name__}: {e}"]})
            return d

    if chain:
        df = _mirror_step(df, "mirrors held")
    if gate:
        try:
            try:
                from migration.cut_write_gate import finalize_cut_for_upload
            except ImportError:                         # pragma: no cover
                from cut_write_gate import finalize_cut_for_upload  # type: ignore
            (out, gate_report), _noise = _quiet(
                lambda d: finalize_cut_for_upload(d, subject, parent_df=parent_df, out_key=cut_key, verbose=False,
                                                  ship_gate=False, _skip_parent_counts=True), df.copy())
            _restore_kept(out)
            bad = frame_invariants(out, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min)
            if bad:
                report["reverted"].append({"step": "cut write gate", "why": bad})
                log(f"[parent-counts] step reverted: cut write gate ({'; '.join(bad)[:200]})")
            else:
                df = out
                report["steps"].append("cut write gate")
                report["gate_report"] = {k: gate_report.get(k) for k in ("fan_rows_stripped", "post_polish_collisions_broken",
                                                                         "terminal_subset_recap", "numeric_artifacts")
                                         if isinstance(gate_report, dict)}
        except Exception as e:
            report["reverted"].append({"step": "cut write gate", "why": [f"raised {type(e).__name__}: {e}"]})
        df = _mirror_step(df, "mirrors held after the gate")
        if keep_demos:
            try:
                out = df.copy()
                n = keep_demographics(out, demo_base, log=log)
                if n:
                    try:
                        from migration.profile_writer import _sort_within_category
                    except ImportError:                 # pragma: no cover
                        from profile_writer import _sort_within_category  # type: ignore
                    out = _sort_within_category(out)
                    bad = frame_invariants(out, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min)
                    if bad:
                        report["reverted"].append({"step": "demographics kept as they were", "why": bad})
                    else:
                        df = out
                        report["steps"].append(f"demographics kept as they were ({n} rows)")
            except Exception as e:
                report["reverted"].append({"step": "demographics kept as they were", "why": [f"{type(e).__name__}: {e}"]})
        try:
            try:
                from migration.final_ship_gate import check_final_ship_invariants
            except ImportError:                         # pragma: no cover
                from final_ship_gate import check_final_ship_invariants  # type: ignore
            (viol, _m), _noise = _quiet(lambda d: check_final_ship_invariants(
                d.to_csv(index=False).encode("utf-8"), cut_key, subject, verbose=False), df)
            report["ship_gate"] = {"ok": not viol, "violations": [
                {k: str(v.get(k))[:200] for k in ("code", "where", "value", "plain")} if isinstance(v, dict) else str(v)[:300]
                for v in (viol or [])][:40]}
        except Exception as e:
            report["ship_gate"] = {"ok": None, "error": f"{type(e).__name__}: {e}"}
        try:
            try:
                from migration.genpop_baseline import append_genpop_columns
            except ImportError:                         # pragma: no cover
                from genpop_baseline import append_genpop_columns  # type: ignore
            out, _noise = _quiet(append_genpop_columns, df.copy())
            if not frame_invariants(out, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min):
                df = out
                report["steps"].append("Gen Pop baseline columns")
        except Exception as e:
            report["reverted"].append({"step": "Gen Pop baseline columns", "why": [f"{type(e).__name__}: {e}"]})
    after = CPC.measure(parent_df, df)
    report["after"] = after
    gate_ok = (report.get("ship_gate") or {}).get("ok")
    report["publish"] = bool(not after.get("flag") and gate_ok is not False and not frame_invariants(
        df, sample=sample, keep_values=keep_values, pins=pins, n_rows_min=n_min))
    return df, report


def correct_if_carried(df, parent_df, subject, *, out_key="", reason_fn=None, log=print):
    """For the cut write gate: a cut that reaches it carrying its
    parent's counts has those rows reasoned before it publishes. Returns
    (df, report or None). The rows only; the gate's own chain follows.
    Never raises, never holds: on any failure the frame goes on as it
    came."""
    try:
        if parent_df is None or str(os.environ.get("CUT_PARENT_COUNTS_GATE", "1")).strip() == "0":
            return df, None
        m = CPC.measure(parent_df, df)
        if not m.get("flag"):
            return df, None
        name = str(out_key).rsplit("/", 1)[-1]
        name = name[:-4] if name.lower().endswith(".csv") else name
        label = re.split(r"\)?\s*-\s+", name, maxsplit=1)[1].strip() if re.search(r"\)?\s*-\s+", name) else "Avid Fan"
        log(f"[cut-write-gate] {out_key or 'cut'}: {m['carried']} of {m['shared']} shared rows carry the parent's count; "
            f"reasoning them for the cut before it publishes")
        sample, _u = _sample(df)
        out, changes, stats = reason_rows(df, parent_df, subject=subject, label=label, reason_fn=reason_fn, log=log)
        if frame_invariants(out, sample=sample, n_rows_min=len(df)):
            return df, {"corrected": False, "before": m}
        return out, {"corrected": True, "before": m, "after": CPC.measure(parent_df, out), "rows": stats}
    except Exception as e:
        try:
            log(f"[cut-write-gate] parent-counts check skipped (non-fatal): {e}")
        except Exception:
            pass
        return df, None
