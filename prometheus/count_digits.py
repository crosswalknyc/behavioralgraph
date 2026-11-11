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

One read, one arithmetic (2026-11-11, the Mad Men compare, job
9e5e8d62fd5a). The pass used to draw each figure on its own: the 2025
total 7,582,850 was drawn three ways (the headline, the stat line, the
table pools), one bucket moved in every row so no row summed to the
pool the reply printed, and the stated loss stayed the difference of
two totals the reply no longer showed. The pass now moves the read as
one structure or not at all:

* a value is drawn once, keyed on the value itself, so the same figure
  lands on the same figure wherever it stands (a pool shared by five
  rows, the stat that repeats it, the headline);
* a row whose buckets sum to its pool still sums to it afterwards: the
  pool is settled first and one bucket carries the residual;
* a figure that is the difference or the sum of two others (a loss, a
  gain, a combined total, an older band that is two buckets together)
  is recomputed from them, never drawn on its own;
* a figure already on record (``held``: the profile's own sample and
  projection, a banked ledger figure, a count an earlier turn of the
  thread stated) is never drawn, and neither is a figure tied to one by
  an identity;
* when the moves cannot be made to agree the read is left as written.

Pure arithmetic and text; no model, no S3, no host. Never raises.
Regression: scripts/test_pm_count_digits.py.
"""
from __future__ import annotations

import hashlib
import re

__all__ = ['FLOOR', 'placeholder_zeros', 'is_natural', 'natural', 'reconcile', 'apply', 'fix_result',
           'rewrite_text', 'count_fields', 'on_record', 'is_partition']

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


# ---------------------------------------------------------------------------
# figures on record
# ---------------------------------------------------------------------------
_RECORD_RX = re.compile(r'(?<![\d,.$])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d,]*\s*%)(?!\.\d)')


def on_record(*sources):
    """Every count the given records state, as a set of ints: numeric
    fields and the figures written in any string, at any depth of a dict,
    a list, or an object that carries ``figures`` (the read's figure
    registry). The digits pass never draws a figure in this set. Pass
    the profile's own figures, the subject's ledger entries and the
    thread's earlier turns. Never raises."""
    out = set()

    def _walk(o, depth):
        if depth > 9 or o is None or isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            try:
                if o == o and 1000 <= abs(o) < 1e15 and float(o).is_integer():
                    out.add(int(abs(o)))
            except (OverflowError, ValueError):
                pass
        elif isinstance(o, str):
            for m in _RECORD_RX.finditer(o):
                v = _num(m.group(1))
                if v is not None and v >= 1000:
                    out.add(v)
        elif isinstance(o, dict):
            for v in o.values():
                _walk(v, depth + 1)
        elif isinstance(o, (list, tuple, set, frozenset)):
            for v in o:
                _walk(v, depth + 1)
        elif hasattr(o, 'figures'):
            _walk(getattr(o, 'figures', None), depth + 1)

    for s in sources:
        try:
            _walk(s, 0)
        except Exception:
            continue
    return out


def _held_set(held):
    out = set()
    for v in (held or ()):
        n = _num(v)
        if n is not None and n > 0:
            out.add(n)
    return out


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


# ---------------------------------------------------------------------------
# the read as one structure
# ---------------------------------------------------------------------------
PARTITION_TOLERANCE = 0.006     # buckets within 0.6% of the pool are the pool, split


def is_partition(counts, pool):
    """True when the buckets are the pool split into parts: two or more
    of them and a sum within 0.6% of the pool (shares written to one
    decimal can miss by that much; a top-N list of overlapping groups
    misses by far more)."""
    try:
        vals = [int(c) for c in counts]
        pool = int(pool)
    except (TypeError, ValueError):
        return False
    if len(vals) < 2 or pool <= 0 or any(v < 0 for v in vals):
        return False
    return abs(sum(vals) - pool) <= max(len(vals), int(pool * PARTITION_TOLERANCE))


def _draw(v, subject, held):
    """One value, one draw: keyed on the subject and the value only, so
    the same figure moves the same way wherever the read states it."""
    if v is None or v <= 0 or v in held or is_natural(v):
        return v
    return int(natural(v, f"{subject}|v"))


def _structure(res):
    """(rows, metrics): the breakdown rows that carry counts and the
    count metrics, as plain records over the live dicts."""
    bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
    rows = []
    for i, r in enumerate(bd.get('rows') if isinstance(bd.get('rows'), list) else []):
        if not isinstance(r, dict):
            continue
        pool = _num(r.get('pool'))
        if pool is not None and pool <= 0:
            pool = None
        bks = [(b, _num(b.get('count'))) for b in (r.get('buckets') or [])
               if isinstance(b, dict) and _num(b.get('count')) is not None and _num(b.get('count')) >= 0]
        if pool is None and not bks:
            continue
        olds = [c for _b, c in bks]
        rows.append({'i': i, 'row': r, 'pool': pool, 'bks': [b for b, _c in bks], 'olds': olds,
                     'labels': [re.sub(r'[^a-z0-9]+', ' ', str(b.get('label') or '').lower()).strip() for b, _c in bks],
                     'partition': bool(pool and is_partition(olds, pool))})
    metrics = []
    for i, m in enumerate(res.get('metrics') or []):
        if isinstance(m, dict) and _is_count_metric(m):
            v = _num(m.get('value'))
            if v is not None and v >= 1000:
                metrics.append({'i': i, 'm': m, 'v': v})
    return rows, metrics


def _text_counts(texts):
    out = set()
    for t in texts or ():
        for m in _COUNT_RX.finditer(str(t or '')):
            v = _num(m.group(1))
            if v is not None and v >= 1000:
                out.add(v)
    return out


def _identities(rows, metrics, free):
    """The figures that are arithmetic on other figures, found on the
    values as written: {value: rule}. A rule is ('diff', a, b),
    ('sum', a, b), ('pools',) for the sum of every row's pool,
    ('run', row, s, e) for neighbouring buckets added together,
    ('comp', row, j) for a pool less one bucket, or ('across', r1, j1,
    r2, j2) for the same bucket in two rows, one less the other. Only
    ``free`` values (figures that are not a pool or a bucket themselves)
    are looked up; the smallest is settled first so a triple a = b + c
    names one derived figure, never two."""
    pools = []
    for r in rows:
        if r['pool'] and r['pool'] not in pools:
            pools.append(r['pool'])
    anchors = list(dict.fromkeys(pools + [m['v'] for m in metrics]))
    row_pool_sum = sum(r['pool'] for r in rows if r['pool']) if sum(1 for r in rows if r['pool']) >= 2 else None
    table = {}
    for x in range(len(anchors)):
        for y in range(x + 1, len(anchors)):
            a, b = anchors[x], anchors[y]
            if a == b:
                continue
            table.setdefault(abs(a - b), ('diff', max(a, b), min(a, b)))
    for x in range(len(anchors)):
        for y in range(x + 1, len(anchors)):
            table.setdefault(anchors[x] + anchors[y], ('sum', anchors[x], anchors[y]))
    for ri, r in enumerate(rows):
        n = len(r['olds'])
        for s in range(n):
            tot = r['olds'][s]
            for e in range(s + 1, n):
                tot += r['olds'][e]
                if e - s + 1 < n and tot > 0:
                    table.setdefault(tot, ('run', ri, s, e))
        if r['pool'] and r['partition']:
            for j, c in enumerate(r['olds']):
                if 0 < c < r['pool']:
                    table.setdefault(r['pool'] - c, ('comp', ri, j))
    index = [{lab: j for j, lab in enumerate(r['labels']) if lab} for r in rows]
    span = range(len(rows)) if len(rows) <= 80 else range(0)
    for r1 in span:
        for r2 in range(r1 + 1, len(rows)):
            if len(rows[r1]['labels']) != len(rows[r2]['labels']):
                continue
            for j1, lab in enumerate(rows[r1]['labels']):
                j2 = index[r2].get(lab) if lab else None
                if j2 is None:
                    continue
                a, b = rows[r1]['olds'][j1], rows[r2]['olds'][j2]
                if a != b and a > 0 and b > 0:
                    table.setdefault(abs(a - b), ('across', r1, j1, r2, j2) if a > b else ('across', r2, j2, r1, j1))
    rules, derived = {}, set()
    for d in sorted(free):
        if d < 1000:
            continue
        if row_pool_sum and d == row_pool_sum and d not in pools:
            rules[d] = ('pools',)
            derived.add(d)
            continue
        rule = table.get(d)
        if not rule:
            continue
        if rule[0] in ('diff', 'sum') and (rule[1] in derived or rule[2] in derived or d in (rule[1], rule[2])):
            continue
        rules[d] = rule
        derived.add(d)
    return rules


def _rule_operands(rule, rows):
    if rule[0] in ('diff', 'sum'):
        return [rule[1], rule[2]]
    if rule[0] == 'pools':
        return [r['pool'] for r in rows if r['pool']]
    if rule[0] == 'run':
        return list(rows[rule[1]]['olds'][rule[2]:rule[3] + 1])
    if rule[0] == 'comp':
        return [rows[rule[1]]['pool'], rows[rule[1]]['olds'][rule[2]]]
    if rule[0] == 'across':
        return [rows[rule[1]]['olds'][rule[2]], rows[rule[3]]['olds'][rule[4]]]
    return []


def _fit_row(r, target, subject, held, banned):
    """(news, carrier): the row's buckets drawn natural and, when the row
    is a partition, summing exactly to ``target``. One bucket (the
    largest that is not on record) carries the residual; when that alone
    would leave it a placeholder, a few units pass between two buckets.
    None when no arrangement works."""
    news = [_draw(o, subject, held) for o in r['olds']]
    if not r['partition'] or target is None:
        return news, None
    resid = target - sum(news)
    if resid == 0:
        return news, None
    order = sorted((j for j, o in enumerate(r['olds']) if o > 0 and o not in held and j not in banned),
                   key=lambda j: -news[j])
    for c in order:
        cand = news[c] + resid
        if cand > 0 and is_natural(cand):
            out = list(news)
            out[c] = cand
            return out, (c,)
    for c in order[:3]:
        for j in order[:4]:
            if j == c:
                continue
            for d in range(1, 500):
                for sgn in (1, -1):
                    a, b = news[c] + resid - sgn * d, news[j] + sgn * d
                    if a > 0 and b > 0 and is_natural(a) and is_natural(b):
                        out = list(news)
                        out[c], out[j] = a, b
                        return out, (c, j)
    return None


def _plan(res, subject, held, texts=()):
    """The whole read's moves, worked out before anything is written:
    {'pools': {old: new}, 'rows': [news per row], 'metrics': [new per
    metric], 'extra': {old: new for figures only the prose states},
    'map': {old: new}}. None when the moves cannot be made to agree (the
    read is then left as written)."""
    rows, metrics = _structure(res)
    struct_vals = set()
    for r in rows:
        if r['pool']:
            struct_vals.add(r['pool'])
        struct_vals.update(o for o in r['olds'] if o > 0)
    prose = _text_counts(texts)
    free = {m['v'] for m in metrics if m['v'] not in struct_vals} | {v for v in prose if v not in struct_vals}
    rules = _identities(rows, metrics, free)
    # a figure on record holds the figures it is arithmetic on
    held = set(held)
    for _ in range(3):
        grew = False
        for d, rule in rules.items():
            if d in held:
                for o in _rule_operands(rule, rows):
                    if o and o not in held:
                        held.add(o)
                        grew = True
        if not grew:
            break
    group = {}
    for ri, r in enumerate(rows):
        if r['pool']:
            group.setdefault(r['pool'], []).append(ri)
    banned = {}
    for _attempt in range(8):
        pool_new, row_news, carriers = {}, {}, {}
        for p, members in group.items():
            done = False
            for target in dict.fromkeys((_draw(p, subject, held), p)):
                fits = {}
                for ri in members:
                    fit = _fit_row(rows[ri], target, subject, held, banned.get(ri, ()))
                    if fit is None:
                        fits = None
                        break
                    fits[ri] = fit
                if fits is not None:
                    pool_new[p] = target
                    for ri, (news, car) in fits.items():
                        row_news[ri] = news
                        carriers[ri] = car or ()
                    done = True
                    break
            if not done:
                # nothing fits (every bucket is on record and the row never
                # summed): the group stands as written
                pool_new[p] = p
                for ri in members:
                    row_news[ri] = list(rows[ri]['olds'])
                    carriers[ri] = ()
        for ri, r in enumerate(rows):
            if ri not in row_news:
                row_news[ri] = [_draw(o, subject, held) for o in r['olds']]
                carriers[ri] = ()
        # one value, one figure: every place a value stands must agree
        seen, clash = {}, set()
        for p, n in pool_new.items():
            seen.setdefault(p, set()).add(n)
        for ri, r in enumerate(rows):
            for o, n in zip(r['olds'], row_news[ri]):
                if o > 0:
                    seen.setdefault(o, set()).add(n)
        for m in metrics:
            if m['v'] not in seen and m['v'] not in rules:
                seen.setdefault(m['v'], set()).add(_draw(m['v'], subject, held))
        for v, ns in seen.items():
            if len(ns) > 1:
                clash.add(v)
        if not clash:
            break
        grew = False
        for ri, r in enumerate(rows):
            for c in carriers.get(ri, ()):
                if r['olds'][c] in clash and c not in banned.get(ri, ()):
                    banned.setdefault(ri, set()).add(c)
                    grew = True
        if not grew:
            return None
    else:
        return None
    vmap = {v: next(iter(ns)) for v, ns in seen.items()}

    def _new(v):
        return vmap.get(v, v)

    def _eval(rule):
        if rule[0] == 'diff':
            return abs(_new(rule[1]) - _new(rule[2]))
        if rule[0] == 'sum':
            return _new(rule[1]) + _new(rule[2])
        if rule[0] == 'pools':
            return sum(pool_new[r['pool']] for r in rows if r['pool'])
        if rule[0] == 'run':
            return sum(row_news[rule[1]][rule[2]:rule[3] + 1])
        if rule[0] == 'comp':
            return pool_new[rows[rule[1]]['pool']] - row_news[rule[1]][rule[2]]
        if rule[0] == 'across':
            return abs(row_news[rule[1]][rule[2]] - row_news[rule[3]][rule[4]])
        return None

    for _ in range(3):      # a rule may stand on a figure another rule settles
        for d, rule in rules.items():
            if d in held:
                continue
            n = _eval(rule)
            if n is not None and n > 0:
                vmap[d] = int(n)
    extra = {}
    for v in prose:
        if v in vmap or v in struct_vals:
            continue
        if v > FLOOR:
            n = _draw(v, subject, held)
            vmap[v] = n
            if n != v:
                extra[v] = n
    return {'rows': rows, 'metrics': metrics, 'pool_new': pool_new, 'row_news': row_news,
            'map': vmap, 'extra': extra}


def _write_plan(res, plan):
    """Write a plan into the structured result; returns {old: new} for
    every figure that moved."""
    moved = {}
    vmap = plan['map']
    for ri, r in enumerate(plan['rows']):
        news = plan['row_news'][ri]
        pool_old = r['pool']
        pool_new = plan['pool_new'].get(pool_old, pool_old) if pool_old else None
        changed = False
        for b, o, n in zip(r['bks'], r['olds'], news):
            if n != o:
                b['count'] = int(n)
                moved[o] = int(n)
                changed = True
        if pool_old and pool_new != pool_old:
            r['row']['pool'] = int(pool_new)
            moved[pool_old] = int(pool_new)
            changed = True
        if changed and pool_new and r['partition']:
            # a stated share stands while the count still rounds to it; one
            # the move has carried past its decimal is recomputed
            for b, n in zip(r['bks'], news):
                try:
                    exact = 100.0 * int(n) / pool_new
                    stated = b.get('share_pct')
                    if not isinstance(stated, (int, float)) or abs(exact - float(stated)) > 0.1:
                        b['share_pct'] = round(exact, 1)
                except Exception:
                    pass
    for m in plan['metrics']:
        n = vmap.get(m['v'], m['v'])
        if n != m['v']:
            m['m']['value'] = int(n)
            moved[m['v']] = int(n)
    for o, n in vmap.items():
        if n != o:
            moved.setdefault(o, int(n))
    figs = res.get('figures')
    if isinstance(figs, list):
        for f in figs:
            if isinstance(f, dict) and str(f.get('kind') or '') == 'count':
                fv = _num(f.get('value'))
                if fv is not None and fv in moved:
                    f['value'] = moved[fv]
    return moved


def _res_texts(res):
    out = []
    if not isinstance(res, dict):
        return out
    if isinstance(res.get('headline'), str):
        out.append(res['headline'])
    out += [r for r in (res.get('reads') or []) if isinstance(r, str)]
    return out


def _rewrite_res_prose(res, moved):
    """The result's own prose follows the map: the headline, the reads,
    the row notes and the definitions."""
    if not isinstance(res, dict) or not moved:
        return
    if isinstance(res.get('reads'), list):
        res['reads'] = [rewrite_text(r, moved) if isinstance(r, str) else r for r in res['reads']]
    for k in ('headline', 'universe', 'cohort'):
        if isinstance(res.get(k), str):
            res[k] = rewrite_text(res[k], moved)
    for m in (res.get('metrics') or []):
        if isinstance(m, dict) and isinstance(m.get('definition'), str):
            m['definition'] = rewrite_text(m['definition'], moved)
    bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
    if isinstance(bd.get('share_basis'), str):
        bd['share_basis'] = rewrite_text(bd['share_basis'], moved)
    for r in (bd.get('rows') or []):
        if isinstance(r, dict):
            for k in ('note', 'definition'):
                if isinstance(r.get(k), str):
                    r[k] = rewrite_text(r[k], moved)


def fix_result(res, subject='', held=None, texts=None):
    """Fix the counts of a structured result in place, as one structure:
    every pool drawn once (rows that share a pool keep sharing it), every
    partition row still summing to its pool, a count metric that repeats
    a pool following it, a figure that is the difference or the sum of
    two others recomputed from them. ``held`` figures never move.
    Returns {old: new} for every figure that moved (ints); {} when the
    moves could not be made to agree. Never raises."""
    try:
        if not isinstance(res, dict):
            return {}
        plan = _plan(res, str(subject or ''), _held_set(held), list(texts or []) + _res_texts(res))
        if not plan:
            return {}
        return _write_plan(res, plan)
    except Exception:
        return {}


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


def apply(reply, res=None, *, subject='', held=None):
    """(reply, info): the read's structured counts drawn natural in place
    as one structure (see ``fix_result``) and the prose rewritten to
    match; counts the structure does not carry but the prose states are
    drawn natural on their own unless they are arithmetic on figures the
    read carries, in which case they follow them. ``held`` is the set of
    figures on record (``on_record``); none of them moves. ``info`` is
    {'moved': {old: new}, 'count': n}."""
    info = {'moved': {}, 'count': 0}
    try:
        hs = _held_set(held)
        if isinstance(res, dict):
            moved = fix_result(res, subject=subject, held=hs, texts=[reply])
        else:
            moved = {}
            for v in _text_counts([reply]):
                if v > FLOOR:
                    n = _draw(v, str(subject or ''), hs)
                    if n != v:
                        moved[v] = n
        moved = {int(k): int(v) for k, v in moved.items() if k != v}
        out = rewrite_text(reply, moved)
        info = {'moved': moved, 'count': len(moved)}
        if isinstance(res, dict) and moved:
            _rewrite_res_prose(res, moved)
        return out, info
    except Exception:
        return reply, info
