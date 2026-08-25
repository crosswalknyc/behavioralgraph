"""Parent-anchoring for NON-PINNED demo categories on derived cuts.

2026-08-24 (Furious audit D1): the Millennials age cut and the Los
Angeles geo cut both shipped male-leaning GENDER (~53/52% male) against
a 55.4%-female parent TU. Only the PINNED dimension of a cut (AGE for an
age cut, LOCATION for a geo cut, GENDER for a gender cut) may be
reshaped; every other demographic category must stay anchored to the
parent's shape. Row-by-row reasoning is free to tilt buckets a little -
a Millennials slice of a female-leaning audience can be a bit less
female - but it must never INVERT the parent's majority bucket, and no
bucket may drift more than a few points from the parent.

This is arithmetic correction of drift in the same spirit as
`migration/gender_split_coherence.py` (avid-and-cut-skin-rules rule 4b):
it slides levels, preserves the reasoning's tilt where it fits inside
the corridor, and renormalizes to 100. It is NOT a multiplier on the
reasoning and it never touches the pinned category, non-demo categories,
or sample-size math.

Usage (both derived-cut engines):

    from cut_demo_anchor import anchor_nonpinned_demos_to_parent
    df_cut, stats = anchor_nonpinned_demos_to_parent(
        df_cut, df_parent, subject,
        pin_category='AGE',            # the cut's own pinned dimension
        cut_salt='millennials_25_44',  # deterministic jitter salt
    )
"""

from __future__ import annotations

import hashlib
import re

# The nine demographic categories that must sum to 100 (matches
# DEPIN_DEMO_CATS in migration/post_generation_enforcers.py).
DEMO_CATS = {
    'GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION',
    'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
}

# Max absolute drift (percentage points) a non-pinned demo bucket may
# sit from the parent's bucket value, before jitter.
DEFAULT_MAX_DRIFT_PP = 4.0


def _norm(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _fbp(v):
    try:
        f = float(str(v).replace('%', '').replace(',', '').strip())
        return f
    except Exception:
        return None


def _jit(seed: str, span: float) -> float:
    """Deterministic jitter in [-span/2, +span/2] from a seed hash."""
    h = int(hashlib.md5(seed.encode('utf-8')).hexdigest()[:8], 16)
    return (h % 10_000) / 10_000.0 * span - span / 2.0


def _detect_bp_col(df):
    for c in df.columns:
        if str(c).lower().strip() == 'brand penetration (row)':
            return c
    for c in df.columns:
        if 'brand penetration' in str(c).lower():
            return c
    return None


def _detect_numeric_cols(df):
    cs = raw = proj = None
    for c in df.columns:
        cl = str(c).lower().strip()
        if cl == 'category share':
            cs = c
        elif cl.startswith('original raw'):
            raw = c
        elif 'projection' in cl:
            proj = c
    return cs, raw, proj


def anchor_nonpinned_demos_to_parent(
    df_cut,
    df_parent,
    subject: str,
    *,
    pin_category: str = '',
    cut_salt: str = '',
    max_drift_pp: float = DEFAULT_MAX_DRIFT_PP,
    verbose: bool = True,
):
    """Clamp every NON-PINNED demo category of a derived cut to the
    parent's shape.

    Per category (skipping ``pin_category``):
      1. Each bucket with a parent counterpart is clamped into
         [parent - max_drift_pp, parent + max_drift_pp] with a small
         subject-salted jitter on the corridor edges (never a shared
         flat bound).
      2. The category is renormalized to sum exactly 100 (scale, then
         residual on the largest bucket).
      3. If the parent's majority bucket is no longer the cut's
         majority bucket, mass is slid (zero-sum) from the usurper to
         the parent's majority until it leads by a small jittered
         margin. This is the never-invert guarantee.
      4. Values landing on an exact 2dp boundary get a +-0.0007-ish
         nudge, compensated on the largest bucket, so no depin pass has
         to touch demo rows later.

    Raw / Projection cells are recomputed from the cut's SAMPLE SIZE
    anchors when available. Returns ``(df_cut, stats)``.
    """
    stats = {'cats_checked': 0, 'buckets_clamped': 0,
             'majority_flips_fixed': 0, 'cats_adjusted': 0}
    if df_cut is None or df_parent is None or len(df_cut) == 0 \
            or len(df_parent) == 0:
        return df_cut, stats
    if 'Column' not in df_cut.columns or 'Value' not in df_cut.columns:
        return df_cut, stats
    if 'Column' not in df_parent.columns or 'Value' not in df_parent.columns:
        return df_cut, stats

    bp_c = _detect_bp_col(df_cut)
    bp_p = _detect_bp_col(df_parent)
    if not bp_c or not bp_p:
        return df_cut, stats
    cs_c, raw_c, proj_c = _detect_numeric_cols(df_cut)

    col_u_cut = df_cut['Column'].astype(str).str.strip().str.upper()
    col_u_par = df_parent['Column'].astype(str).str.strip().str.upper()
    # pin_category accepts a single category name OR an iterable of
    # them (compound multi-pin cuts, 2026-08-25). Every pinned
    # dimension is skipped; everything else anchors to the parent.
    if isinstance(pin_category, (list, tuple, set, frozenset)):
        pin_set_u = {str(p or '').strip().upper()
                     for p in pin_category if str(p or '').strip()}
    else:
        pin_set_u = ({str(pin_category or '').strip().upper()}
                     if str(pin_category or '').strip() else set())

    # Sample anchors on the CUT for Raw/Proj recompute.
    sample = universe = None
    for anchor in ('SAMPLE SIZE', 'BRAND INPUT'):
        m = col_u_cut == anchor
        if not m.any():
            continue
        row = df_cut.loc[m].iloc[0]
        if sample is None and raw_c:
            v = _fbp(row.get(raw_c))
            sample = v if v and v > 0 else None
        if universe is None and proj_c:
            v = _fbp(row.get(proj_c))
            universe = v if v and v > 0 else None

    # Parent demo index: {cat: {bucket_norm: parent_bp}}
    parent_idx: dict = {}
    for cat in DEMO_CATS:
        m = col_u_par == cat
        if not m.any():
            continue
        d = {}
        for _, r in df_parent.loc[m].iterrows():
            bn = _norm(r.get('Value'))
            bv = _fbp(r.get(bp_p))
            if bn and bv is not None:
                d[bn] = bv
        if len(d) >= 2:
            parent_idx[cat] = d

    # dtype safety for writes
    for c in (bp_c, cs_c, raw_c, proj_c):
        if c and c in df_cut.columns and \
                df_cut[c].dtype.name not in ('object', 'O'):
            df_cut[c] = df_cut[c].astype(object)

    for cat in sorted(DEMO_CATS):
        if cat in pin_set_u or cat not in parent_idx:
            continue
        m = col_u_cut == cat
        idxs = list(df_cut.index[m])
        if len(idxs) < 2:
            continue
        stats['cats_checked'] += 1
        pdist = parent_idx[cat]

        entries = []  # (idx, label, bucket_norm, cut_bp, parent_bp|None, had_pct)
        for idx in idxs:
            label = str(df_cut.at[idx, 'Value'] or '').strip()
            bn = _norm(label)
            cell = str(df_cut.at[idx, bp_c])
            bv = _fbp(cell)
            if bv is None:
                bv = 0.0
            entries.append(
                (idx, label, bn, bv, pdist.get(bn),
                 cell.strip().endswith('%')))

        # 1. corridor bounds per bucket (jittered edges, never a shared
        #    flat bound). Buckets with no parent counterpart are free.
        bounds = {}
        new_vals = {}
        clamped_here = 0
        for idx, label, bn, bv, pbv, _pct in entries:
            if pbv is None:
                bounds[idx] = (0.0, 100.0)
                new_vals[idx] = max(0.0, bv)
                continue
            lo = max(0.0, pbv - max_drift_pp
                     + _jit(f'{subject}|{cat}|{label}|{cut_salt}|lo', 0.6))
            hi = min(100.0, pbv + max_drift_pp
                     + _jit(f'{subject}|{cat}|{label}|{cut_salt}|hi', 0.6))
            if hi <= lo:
                hi = lo + 0.05
            bounds[idx] = (lo, hi)
            nv = min(max(bv, lo), hi)
            if abs(nv - bv) > 1e-9:
                clamped_here += 1
            new_vals[idx] = nv

        # 2. renormalize to 100 WITHOUT leaving the corridor: distribute
        #    the residual proportionally to each bucket's remaining slack
        #    toward its own bound (not a multiplicative scale, which can
        #    push the largest bucket way back out of the corridor - the
        #    Furious LA ETHNICITY case drifted 9pp that way). The parent
        #    distribution itself sums to 100 and sits inside every
        #    corridor, so a feasible point always exists; a handful of
        #    slack-weighted passes converges exactly.
        total = sum(new_vals.values())
        if total <= 0:
            continue
        for _pass in range(12):
            residual = 100.0 - sum(new_vals.values())
            if abs(residual) < 1e-9:
                break
            if residual > 0:
                slack = {i: bounds[i][1] - new_vals[i] for i in new_vals}
            else:
                slack = {i: new_vals[i] - bounds[i][0] for i in new_vals}
            s_tot = sum(v for v in slack.values() if v > 1e-12)
            if s_tot <= 1e-12:
                # corridor exhausted (shouldn't happen: parent is
                # feasible) - fall back to plain scale for safety
                t = sum(new_vals.values())
                for i in new_vals:
                    new_vals[i] = new_vals[i] / t * 100.0
                break
            for i in new_vals:
                sl = max(0.0, slack[i])
                if sl <= 0:
                    continue
                new_vals[i] += residual * (sl / s_tot)
                lo_i, hi_i = bounds[i]
                new_vals[i] = min(max(new_vals[i], lo_i), hi_i)

        # 3. never-invert-the-majority guarantee (only among buckets
        #    that exist on both sides).
        both = [(idx, bn, pbv) for idx, _l, bn, _b, pbv, _p in entries
                if pbv is not None]
        flipped = False
        if len(both) >= 2:
            maj_idx = max(both, key=lambda t: t[2])[0]
            cur_idx = max(new_vals, key=lambda i: new_vals[i])
            if cur_idx != maj_idx:
                margin = 0.55 + abs(
                    _jit(f'{subject}|{cat}|{cut_salt}|maj', 0.9))
                need = new_vals[cur_idx] - new_vals[maj_idx] + margin
                move = min(need, max(0.0, new_vals[cur_idx] - 0.05))
                new_vals[cur_idx] -= move
                new_vals[maj_idx] += move
                flipped = True
                stats['majority_flips_fixed'] += 1

        # 4. 4dp round + residual-to-largest + de-boundary nudge
        for idx in new_vals:
            new_vals[idx] = round(new_vals[idx], 4)
        largest = max(new_vals, key=lambda i: new_vals[i])
        residual = round(100.0 - sum(new_vals.values()), 4)
        new_vals[largest] = round(new_vals[largest] + residual, 4)
        second = None
        if len(new_vals) >= 2:
            second = sorted(new_vals, key=lambda i: new_vals[i])[-2]
        for idx in list(new_vals):
            v = new_vals[idx]
            if v > 0 and abs(v * 100 - round(v * 100)) < 1e-9:
                sign = 1.0 if _jit(
                    f'{subject}|{cat}|{idx}|{cut_salt}|nudge', 1.0) >= 0 \
                    else -1.0
                delta = sign * (0.0003 + abs(_jit(
                    f'{subject}|{cat}|{idx}|{cut_salt}|nudge2', 0.0008)))
                comp = second if idx == largest else largest
                if comp is not None and comp != idx:
                    new_vals[idx] = round(v + delta, 4)
                    new_vals[comp] = round(new_vals[comp] - delta, 4)

        # write back only if the category actually moved
        moved = any(
            abs(new_vals[idx] - bv) > 0.0001
            for idx, _l, _n, bv, _pv, _p in entries
        )
        if not moved and not flipped:
            continue
        stats['cats_adjusted'] += 1
        stats['buckets_clamped'] += clamped_here
        for idx, _label, _bn, _bv, _pbv, had_pct in entries:
            nv = new_vals[idx]
            df_cut.at[idx, bp_c] = (
                f'{nv:.4f}%' if had_pct else f'{nv:.4f}')
            if raw_c and sample:
                df_cut.at[idx, raw_c] = int(round(nv / 100.0 * sample))
            if proj_c and raw_c and sample:
                raw_v = int(round(nv / 100.0 * sample))
                df_cut.at[idx, proj_c] = int(
                    round(raw_v / 10_000_000.0 * 329_900_000))
            if cs_c:
                df_cut.at[idx, cs_c] = f'{nv:.4f}'
        if verbose:
            note = ' (majority restored)' if flipped else ''
            print(f'   ⚖️  demo anchor: {cat} re-anchored to parent '
                  f'({clamped_here} bucket(s) clamped){note}')

    return df_cut, stats
