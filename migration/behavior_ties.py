"""How rows on a profile sit together: the one law the category reach
and the cross of behaviors both read (2026-11-11, Jenna).

A profile carries one share per row. Two reads need more than that:

- the category reach (migration/category_rollup.py): the share of an
  audience with AT LEAST ONE row of a group;
- the cross of behaviors (migration/behavior_crosstab.py): the share
  that does EVERY behavior named.

Before this they each made their own assumption, so for two rows A and B
"A or B" from one and "A and B" from the other did not add up to A + B.
Jenna: "One estimator family, one set of tie parameters, shared by both
modules." This module is that family and those parameters. Both reads
are events under the SAME description of how rows move together, so for
any rows on a profile

    either + both = A + B

and a whole category's reach equals what its own rows' crosses imply.

The law, per row
----------------
BRAND rows (every behavioral column the category reach rolls up) move
with one common driver at the category reach's fixed association RHO.
That is exactly what the category reach already assumed, so none of its
figures move for these columns.

EITHER/OR rows (columns in EITHER_OR_COLS: a person typically holds one
wireless carrier, one insurer) are not pulled together. Part of each
row's share (PICK) is the person's ONE pick in the column, the rest is
looking around, unrelated to the pick. Two rows of such a column
therefore sit a little BELOW the figure their shares multiplied would
give, and the column's reach sits a little above the unrelated figure.
A small, explicit list of columns; never a list of brands.

SOFT rows (interests and demographic buckets, which the category reach
does not roll up) tie to the same common driver by their index against
the US: nothing at or under the US average, rising with the index,
capped at A_MAX.

Every figure is integrated on the category reach's own fixed grid, so
the same inputs always give the same output, and the same law runs on
the audience and on the US side.

Pure arithmetic; no model, no S3, no host.
Regression: scripts/test_pm_crosstab_behaviors.py,
scripts/test_category_rollup.py.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

__all__ = ['RHO', 'A_MAX', 'IDX_FULL', 'IDX_FLOOR', 'EITHER_OR_COLS', 'PICK', 'PICK_MAX', 'CAP',
           'soft_loading', 'is_either_or', 'pick_parts', 'Row', 'none_share', 'reach_share']

# The category reach's association between two brand rows of a group.
RHO = 0.70
# A group's reach stops here unless one row in it is higher on its own.
CAP = 0.994
# Soft rows (interests, demographic buckets): the most one moves with the
# common driver, the index at which it is fully tied, and the index at or
# under which it is not tied at all.
A_MAX = 0.60
IDX_FULL = 300.0
IDX_FLOOR = 105.0
# Columns where a person typically holds one row at a time. Explicit and
# small on purpose; add a column here, never a brand.
EITHER_OR_COLS = frozenset(('TELECOM', 'INSURANCE'))
# The part of an either/or row's share that is the person's one pick, and
# the most of a column's people who can hold a pick at all.
PICK = 0.25
PICK_MAX = 0.90

_ND = NormalDist()
_GRID = np.arange(-8.0, 8.0 + 1e-9, 0.1)
_W = np.exp(-0.5 * _GRID * _GRID)
_W = _W / _W.sum()
_BRAND_LOADING = math.sqrt(RHO)


def _phi(x):
    """Standard normal CDF on an array (the category reach's own)."""
    z = np.abs(x) * 0.7071067811865476
    t = 1.0 / (1.0 + 0.5 * z)
    poly = (-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (
        0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (
            -1.13520398 + t * (1.48851587 + t * (
                -0.82215223 + t * 0.17087277)))))))))
    erfc = t * np.exp(poly)
    half = 0.5 * erfc
    return np.where(x >= 0, 1.0 - half, half)


def norm_col(c):
    import re
    return re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())


def is_either_or(column):
    return norm_col(column) in EITHER_OR_COLS


def soft_loading(index):
    """How much an interest or a demographic bucket moves with the common
    driver, 0 to A_MAX, from its index against the US (100 = average)."""
    try:
        ix = float(index)
    except (TypeError, ValueError):
        return 0.0
    if ix != ix or ix <= IDX_FLOOR:
        return 0.0
    x = math.log(ix / 100.0) / math.log(IDX_FULL / 100.0)
    return round(A_MAX * max(0.0, min(1.0, x)), 6)


def pick_parts(share, column_sum):
    """(pick, look) for an either/or row: the part of its share that is
    the person's one pick in the column and the chance of looking at it
    otherwise. ``column_sum`` is the sum of every row's share in the
    column on that side (fractions), so the picks never add past
    PICK_MAX."""
    p = max(0.0, min(1.0, float(share)))
    tot = max(p, float(column_sum or 0.0))
    k = min(1.0, PICK_MAX / (PICK * tot)) if tot > 0 else 1.0
    h = PICK * p * k
    return h, ((p - h) / (1.0 - h) if h < 1.0 else 0.0)


class Row(tuple):
    """One row for the law: (share, kind, loading, column, column_sum).
    kind 'brand' (common driver at RHO), 'soft' (common driver at its own
    loading), 'pick' (an either/or column)."""
    __slots__ = ()

    def __new__(cls, share, kind='brand', loading=0.0, column='', column_sum=0.0):
        return tuple.__new__(cls, (max(0.0, min(1.0, float(share))), str(kind), float(loading), str(column),
                                   float(column_sum or 0.0)))

    share = property(lambda s: s[0])
    kind = property(lambda s: s[1])
    loading = property(lambda s: s[2])
    column = property(lambda s: s[3])
    column_sum = property(lambda s: s[4])


def none_share(rows):
    """The share of people with NONE of the rows (a fraction), under the
    one law. Rows are ``Row``s; order never matters."""
    rows = [r for r in rows if r.share > 0.0]
    if not rows:
        return 1.0
    out = 1.0
    gauss = [(r.share, _BRAND_LOADING if r.kind == 'brand' else max(0.0, min(0.999, r.loading)))
             for r in rows if r.kind in ('brand', 'soft')]
    if gauss:
        if any(p >= 1.0 for p, _ in gauss):
            return 0.0
        tied = [(p, a) for p, a in gauss if a > 0.0]
        loose = [p for p, a in gauss if a <= 0.0]
        part = 1.0
        if tied:
            ps = np.clip(np.asarray([p for p, _ in tied], dtype=float), 1e-12, 1.0 - 1e-12)
            a = np.asarray([x for _, x in tied], dtype=float)
            thr = np.fromiter((_ND.inv_cdf(float(p)) for p in ps), dtype=float, count=len(ps))
            with np.errstate(divide='ignore', over='ignore', invalid='ignore'):
                log_none = np.zeros_like(_GRID)
                for i in range(0, len(thr), 2048):
                    q = _phi((thr[i:i + 2048, None] - a[i:i + 2048, None] * _GRID[None, :])
                             / np.sqrt(1.0 - a[i:i + 2048, None] ** 2))
                    log_none += np.log1p(-np.minimum(q, 1.0)).sum(axis=0)
                part = float((np.exp(log_none) * _W).sum())
        for p in loose:
            part *= (1.0 - p)
        out *= part
    cols = {}
    for r in rows:
        if r.kind == 'pick':
            cols.setdefault(r.column, []).append(r)
    for col in sorted(cols):
        held, look = 0.0, 1.0
        for r in cols[col]:
            h, b = pick_parts(r.share, r.column_sum)
            held += h
            look *= (1.0 - b)
        out *= max(0.0, 1.0 - held) * look
    return max(0.0, min(1.0, out))


def reach_share(rows):
    """The share of people with at least one of the rows (a fraction):
    never below the largest row, never above the sum or 1, and stopped at
    CAP unless one row is higher on its own. For plain brand rows this is
    the category reach's own figure."""
    rows = [r for r in rows if r.share > 0.0]
    if not rows:
        return 0.0
    top = max(r.share for r in rows)
    if len(rows) == 1:
        return top
    est = 1.0 - none_share(rows)
    est = min(est, 1.0, sum(r.share for r in rows))
    est = min(est, CAP)
    return max(est, top)
