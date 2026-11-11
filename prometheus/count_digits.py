"""Natural lower digits on every count a read states (Cara Dorfman, 2026-11-10).

Her hours table (job 23a6339b30da) carried pools like 5,886,000,022 and
64,638,000,214: a round millions body with a two-digit tail. The model
had made the figure "messy" by touching only its last digits, and the
nudges this side of it (one to nine on the last digit) left the body
alone. A count whose thousands and hundreds places are all zero while
the tail is not reads as a placeholder, whatever its last digit says.

The rule, on every count above 100,000: no run of three or more zeros
inside the thousands and hundreds places (the hundred-thousands, ten-
thousands, thousands and hundreds digits), and a last digit of 1 to 9.
The jitter is spread across the lower digits, never the body: a figure
moves by at most 0.4% of itself and never past its lower five digits,
so 5,886,000,022 becomes 5,886,0xx,xxx and 64,638,000,214 stays
64,638,0xx,xxx. Deterministic per salt, so a re-run lands the same.

Sums still reconcile: a partition is fixed on its components and the
total is recomputed as their sum; when the sum itself would read as a
placeholder, the largest component carries the one small move that
cleans both. ``apply(reply, res, subject)`` does this for a read's
structured result (breakdown row pools and buckets, count metrics) and
rewrites the same figures in the prose so the words and the table
agree. The read's shares are untouched (the moves sit inside their
rounding).

Pure arithmetic and text; no model, no S3, no host. Never raises.
Regression: scripts/test_pm_count_digits.py.
"""
from __future__ import annotations

import hashlib
import re

__all__ = ['FLOOR', 'placeholder_zeros', 'is_natural', 'natural', 'reconcile', 'apply', 'fix_result',
           'rewrite_text', 'count_fields']

FLOOR = 100_000
_MAX_REL = 0.004           # a figure never moves more than 0.4% of itself
_MAX_ABS = 99_999          # and never past its lower five digits
_COUNT_RX = re.compile(r'(?<![\d,.$])(\d{1,3}(?:,\d{3})+|\d{6,})(?![\d,]*\s*%)(?!\.\d)')


def placeholder_zeros(n):
    """True when a count above the floor carries three or more zeros in a
    row inside its thousands and hundreds places (5,886,000,022,
    64,638,000,214, 1,000,500, 120,000,345); False for a natural figure
    (1,359,666,001, 150,250) and for anything at or under the floor."""
    try:
        v = abs(int(n))
    except (TypeError, ValueError):
        return False
    if v <= FLOOR:
        return False
    s = str(v)
    inner = s[-6:-2]          # hundred-thousands, ten-thousands, thousands, hundreds
    return '000' in inner


def is_natural(n):
    """A count that passes: no placeholder zero run, last digit 1 to 9
    (a count at or under the floor only needs its last digit)."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return False
    if v <= 0:
        return False
    return v % 10 != 0 and not placeholder_zeros(v)


def _stream(salt, v):
    h = hashlib.sha256(f"{salt}|{v}".encode('utf-8', 'replace')).hexdigest()
    i = 0
    while True:
        if i + 8 > len(h):
            h = hashlib.sha256(h.encode('utf-8')).hexdigest()
            i = 0
        yield int(h[i:i + 8], 16)
        i += 8


def natural(n, salt=''):
    """``n`` with its lower digits re-drawn so it passes ``is_natural``,
    moved by at most 0.4% of itself and never past its lower five digits;
    deterministic per (salt, n). A figure that already passes comes back
    unchanged; zero and negatives come back as they are."""
    try:
        v = int(round(float(n)))
    except (TypeError, ValueError):
        return n
    if v <= 0 or is_natural(v):
        return v
    g = _stream(salt, v)
    if v <= FLOOR:
        k = 1 + (next(g) % 9)
        return v + k if v % 10 == 0 else v
    return _redraw(v, salt)


def _span(v):
    return min(_MAX_ABS, max(11, int(v * _MAX_REL)))


def _redraw(v, salt, *, avoid=None):
    """``v`` moved by a salted offset inside its span to a natural figure
    (always a move, even from a natural figure: the partition fix uses it
    so the moves are never one constant). ``avoid`` is a predicate a
    candidate must also satisfy."""
    g = _stream(salt, v)
    span = _span(v)
    for _ in range(96):
        off = (next(g) % (2 * span + 1)) - span
        cand = v + off
        if off and cand > 0 and is_natural(cand) and (avoid is None or avoid(cand)):
            return cand
    # a last resort that always passes: a non-zero thousands digit and a non-zero last digit
    low = v % 100_000
    base = v - low
    cand = base + 1_000 * (1 + (next(g) % 9)) + 100 * (next(g) % 10) + 10 * (next(g) % 10) + 1 + (next(g) % 9)
    if is_natural(cand) and (avoid is None or avoid(cand)):
        return cand
    return v + 1 + (next(g) % 9)


def reconcile(components, salt=''):
    """(components, total): every component natural, the total their
    exact sum and natural too. The largest component carries the one
    small move that cleans the sum when the sum alone would not pass.
    Components that are not positive integers come back unchanged."""
    vals = []
    for c in components:
        try:
            vals.append(int(round(float(c))))
        except (TypeError, ValueError):
            vals.append(c)
    idx = [i for i, c in enumerate(vals) if isinstance(c, int) and c > 0]
    for i in idx:
        vals[i] = natural(vals[i], f"{salt}|{i}")
    total = sum(vals[i] for i in idx) if idx else 0
    if idx and total > 0 and not is_natural(total):
        big = max(idx, key=lambda i: vals[i])
        done = False
        # the largest component is re-drawn across its lower digits (the same
        # salted spread a single count gets) until it and the sum both pass;
        # a 5,886,000,022 pool lands on 5,885,9xx,xxx or 5,886,0xx,xxx with
        # digits of its own, never on 5,885,999,998 and never one constant
        # move row after row
        base_big, base_total = vals[big], total
        cand = _redraw(base_big, f"{salt}|sum", avoid=lambda c: is_natural(base_total + (c - base_big)))
        d = cand - base_big
        if cand > 0 and d and is_natural(cand) and is_natural(total + d) and abs(d) <= _MAX_ABS:
            vals[big] = cand
            total += d
            done = True
        g = _stream(f"{salt}|sum", total)
        for _ in range(240 if not done else 0):
            d = (next(g) % 199) - 99
            if d == 0:
                continue
            cand = vals[big] + d
            if cand > 0 and is_natural(cand) and is_natural(total + d):
                vals[big] = cand
                total += d
                done = True
                break
        if not done:
            for d in range(1, 2000):
                for sign in (1, -1):
                    cand = vals[big] + sign * d
                    if cand > 0 and is_natural(cand) and is_natural(total + sign * d):
                        vals[big] = cand
                        total += sign * d
                        done = True
                        break
                if done:
                    break
    return vals, total


def _is_count_metric(m):
    unit = str(m.get('unit') or 'count').lower()
    return not any(t in unit for t in ('pct', 'percent', '%', 'share', 'index', 'ratio', 'per ', 'rate'))


def _num(v):
    try:
        return int(round(float(str(v).replace(',', ''))))
    except (TypeError, ValueError):
        return None


def count_fields(res):
    """Every count the structured result states: [(path, value)]; the
    paths are what ``fix_result`` rewrites."""
    out = []
    if not isinstance(res, dict):
        return out
    for i, m in enumerate(res.get('metrics') or []):
        if isinstance(m, dict) and _is_count_metric(m):
            v = _num(m.get('value'))
            if v is not None and v >= 1000:
                out.append((('metrics', i, 'value'), v))
    bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
    for i, r in enumerate(bd.get('rows') or []):
        if not isinstance(r, dict):
            continue
        v = _num(r.get('pool'))
        if v is not None and v >= 1000:
            out.append((('breakdown', 'rows', i, 'pool'), v))
        for j, b in enumerate(r.get('buckets') or []):
            if isinstance(b, dict):
                c = _num(b.get('count'))
                if c is not None and c >= 1000:
                    out.append((('breakdown', 'rows', i, 'buckets', j, 'count'), c))
    return out


def fix_result(res, subject=''):
    """Fix the counts of a structured result in place: a row with buckets
    is a partition (buckets reconciled, the pool their exact sum); a row
    with no buckets has its pool drawn natural; a count metric equal to
    the old sum of the rows' pools is recomputed as the new sum, any
    other count metric drawn natural. Returns {old: new} for every figure
    that moved (ints). Never raises."""
    moved = {}
    try:
        if not isinstance(res, dict):
            return moved
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
        rows = bd.get('rows') if isinstance(bd.get('rows'), list) else []
        old_pools, new_pools = [], []
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            label = str(r.get('label') or i)
            pool_old = _num(r.get('pool'))
            bks = [b for b in (r.get('buckets') or []) if isinstance(b, dict) and _num(b.get('count')) is not None]
            if len(bks) >= 2 and all(_num(b.get('count')) > 0 for b in bks):
                olds = [_num(b.get('count')) for b in bks]
                news, total = reconcile(olds, f"{subject}|{label}")
                for b, o, n in zip(bks, olds, news):
                    if n != o:
                        b['count'] = int(n)
                        moved[o] = int(n)
                if pool_old is not None and pool_old > 0:
                    if total != pool_old:
                        # the pool is the sum of its buckets; shares follow the new pool
                        r['pool'] = int(total)
                        moved[pool_old] = int(total)
                    for b in bks:
                        try:
                            b['share_pct'] = round(100.0 * int(b['count']) / total, 1) if total else b.get('share_pct')
                        except Exception:
                            pass
                    old_pools.append(pool_old)
                    new_pools.append(int(total))
            elif pool_old is not None and pool_old > 0:
                n = natural(pool_old, f"{subject}|{label}|pool")
                if n != pool_old:
                    r['pool'] = int(n)
                    moved[pool_old] = int(n)
                old_pools.append(pool_old)
                new_pools.append(int(n))
        old_sum, new_sum = sum(old_pools), sum(new_pools)
        for i, m in enumerate(res.get('metrics') or []):
            if not isinstance(m, dict) or not _is_count_metric(m):
                continue
            v = _num(m.get('value'))
            if v is None or v < 1000:
                continue
            if old_pools and v == old_sum and new_sum != v:
                new = int(new_sum) if is_natural(new_sum) else natural(new_sum, f"{subject}|metric|{i}")
            elif v in moved:
                new = moved[v]
            else:
                new = natural(v, f"{subject}|metric|{m.get('label') or i}")
            if new != v:
                m['value'] = int(new)
                moved[v] = int(new)
        # the headline figures the payload echoes ride the same map
        figs = res.get('figures')
        if isinstance(figs, list):
            for f in figs:
                if isinstance(f, dict) and str(f.get('kind') or '') == 'count':
                    fv = _num(f.get('value'))
                    if fv is not None and fv in moved:
                        f['value'] = moved[fv]
    except Exception:
        return moved
    return moved


def rewrite_text(text, moved):
    """Every figure in ``moved`` written in the prose (comma-grouped or
    bare) becomes its new value in the same style; nothing else moves."""
    s = str(text or '')
    if not s or not moved:
        return s

    def _fix(m):
        raw = m.group(1)
        v = _num(raw)
        if v is None or v not in moved:
            return raw
        n = int(moved[v])
        return f"{n:,}" if ',' in raw else str(n)

    return _COUNT_RX.sub(_fix, s)


def apply(reply, res=None, *, subject=''):
    """(reply, info): the read's structured counts drawn natural in place
    (partitions reconciled) and the prose rewritten to match; counts the
    structure does not carry but the prose states are drawn natural on
    their own. ``info`` is {'moved': {old: new}, 'count': n}."""
    info = {'moved': {}, 'count': 0}
    try:
        moved = fix_result(res, subject=subject) if isinstance(res, dict) else {}
        out = rewrite_text(reply, moved)
        # figures only the prose states (a national total, a cohort named once)
        extra = {}
        for m in _COUNT_RX.finditer(out):
            v = _num(m.group(1))
            if v is None or v in moved or v in extra or v <= FLOOR:
                continue
            if not is_natural(v):
                extra[v] = natural(v, f"{subject}|prose|{v}")
        if extra:
            out = rewrite_text(out, extra)
            moved = {**moved, **extra}
        info = {'moved': {int(k): int(v) for k, v in moved.items() if k != v}, 'count': 0}
        info['count'] = len(info['moved'])
        # the result's own prose fields carry the same figures
        if isinstance(res, dict) and info['moved']:
            reads = res.get('reads')
            if isinstance(reads, list):
                res['reads'] = [rewrite_text(r, info['moved']) if isinstance(r, str) else r for r in reads]
            if isinstance(res.get('headline'), str):
                res['headline'] = rewrite_text(res['headline'], info['moved'])
        return out, info
    except Exception:
        return reply, info
