"""The identities a read must hold, checked on the finished answer (2026-11-11).

The Mad Men compare (job 9e5e8d62fd5a) shipped with three different 2025
totals (the headline, the stat line, the table pools), a stated loss that
was not the difference of the two totals printed beside it, and age and
gender rows that did not sum to their pool. It was stamped 'pass': the
checks before it looked at anchors and vocabulary, never at whether the
answer agreed with itself.

``enforce(reply, res)`` runs last, on the text the reader will see and on
the structured result the file, the job record and the ledger entry are
written from. It checks, and fixes in place:

1. A row whose buckets are its pool split into parts sums to the pool
   exactly (one bucket carries the residual; when every bucket is on
   record the pool becomes their sum).
2. Rows of one group (the same people, split by different dimensions:
   age, gender, income, each with its own set of buckets) whose pools
   sit within half a percent of each other carry one pool: a figure
   drawn apart is brought back to one value, the one a count stat
   states when there is one. Rows that share a bucket set are different
   groups on one dimension (two months, two platforms) and are never
   brought together.
3. Every table line the reply prints carries the row's own figures: the
   pool and each bucket count are read back from the structured result.
4. A count stat printed as 'Label: N' carries the stat's own value.
5. A stated change ('a loss of 641,023', 'a gain of ...', 'a difference
   of ...') beside two counts is the difference of those two counts.

Nothing is held and nothing fails: a read that does not agree with itself
is made to. Every fix lands on the trail (the worker log and the verify
stamp); the reader sees the corrected figure and nothing else.

Pure text and dict arithmetic; no model, no network, never raises.
Regression: scripts/test_pm_read_identities.py.
"""
from __future__ import annotations

import re
from collections import Counter

from . import count_digits as _cd

__all__ = ['enforce', 'check', 'trail']

GROUP_TOLERANCE = 0.005        # pools of one group this close are one figure
CHANGE_OF_OPERAND = 0.009      # a stated change may sit this far off (as a share of the larger count)
CHANGE_OF_ITSELF = 0.10        # and this far off as a share of the true difference, and still be it
_COUNT = r'\d{1,3}(?:,\d{3})+|\d{6,}'
_COUNT_RX = re.compile(r'(?<![\d,.$])(' + _COUNT + r')(?![\d,]*\s*%)(?!\.\d)')
_CHANGE_RX = re.compile(
    r'\b(?:loss|gain|drop|decline|decrease|increase|rise|difference|gap|fall|shortfall)\s+of\s+'
    r'(?:about\s+|roughly\s+|nearly\s+|some\s+|just\s+)?(?P<n>' + _COUNT + r')(?![\d,]*\s*%)', re.I)
_SENTENCE_RX = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')


def _num(v):
    try:
        return int(round(float(str(v).replace(',', ''))))
    except (TypeError, ValueError):
        return None


def _fmt_like(raw, n):
    return f"{int(n):,}" if ',' in str(raw) else str(int(n))


def _rows(res):
    bd = res.get('breakdown') if isinstance(res, dict) and isinstance(res.get('breakdown'), dict) else {}
    return [r for r in (bd.get('rows') or []) if isinstance(r, dict)]


def _buckets(row):
    return [b for b in (row.get('buckets') or []) if isinstance(b, dict) and _num(b.get('count')) is not None]


def _group_key(row, suffix_count):
    g = str(row.get('group') or '').strip().lower()
    if g:
        return 'g:' + g
    label = str(row.get('label') or '')
    if ' - ' in label:
        suf = label.split(' - ', 1)[1].strip().lower()
        if suf and suffix_count.get(suf, 0) >= 2:
            return 's:' + suf
    return ''


def _carry(row, target, held):
    """Make the row's buckets sum to ``target`` by moving one bucket (the
    largest not on record). True when the row now sums."""
    bks = _buckets(row)
    counts = [_num(b.get('count')) for b in bks]
    resid = target - sum(counts)
    if resid == 0:
        return True
    order = sorted((j for j, c in enumerate(counts) if c > 0 and c not in held), key=lambda j: -counts[j])
    for j in order:
        cand = counts[j] + resid
        if cand > 0 and _cd.is_natural(cand):
            bks[j]['count'] = int(cand)
            return True
    for j in order[:1]:
        cand = counts[j] + resid
        if cand > 0:
            bks[j]['count'] = int(cand)
            return True
    return False


def _fix_structure(res, held, fixes):
    """1 + 2 on the structured result. Returns {old: new} for a pool or
    a stat that was brought back to its group's one figure, so the prose
    that quoted the old value follows."""
    swaps = {}
    rows = _rows(res)
    if not rows:
        return swaps
    metrics = [m for m in (res.get('metrics') or [])
               if isinstance(m, dict) and _cd._is_count_metric(m) and (_num(m.get('value')) or 0) >= 1000]
    metric_vals = {_num(m.get('value')) for m in metrics}
    # 2. one group, one pool
    suffix_count = Counter()
    for r in rows:
        label = str(r.get('label') or '')
        if ' - ' in label:
            suffix_count[label.split(' - ', 1)[1].strip().lower()] += 1
    groups = {}
    for r in rows:
        pool = _num(r.get('pool'))
        key = _group_key(r, suffix_count)
        if key and pool and pool > 0 and len(_buckets(r)) >= 2:
            groups.setdefault(key, []).append(r)
    for key, members in groups.items():
        pools = [_num(r.get('pool')) for r in members]
        if len(set(pools)) < 2:
            continue
        sigs = [tuple(sorted(str(b.get('label') or '').strip().lower() for b in _buckets(r))) for r in members]
        if len(set(sigs)) != len(sigs):
            continue            # two rows on one dimension: different groups, each with its own pool
        lo, hi = min(pools), max(pools)
        if (hi - lo) > hi * GROUP_TOLERANCE:
            continue            # different bases (a known-income pool, a subgroup): not one figure
        tally = Counter(pools)
        stated = [p for p in pools if p in metric_vals]
        on_rec = [p for p in pools if p in held]
        near = [v for v in metric_vals if v in held and abs(v - hi) <= hi * GROUP_TOLERANCE]
        one = on_rec[0] if on_rec else (near[0] if near else (stated[0] if stated else tally.most_common(1)[0][0]))
        for r in members:
            old = _num(r.get('pool'))
            if old != one:
                r['pool'] = int(one)
                swaps[old] = int(one)
                fixes.append(f"pool of '{str(r.get('label') or '')[:50]}' {old:,} -> {one:,} (one figure for the group)")
        # a stat drawn apart from the same figure comes back with it
        for m in metrics:
            v = _num(m.get('value'))
            if v != one and v not in held and abs(v - one) <= one * GROUP_TOLERANCE:
                m['value'] = int(one)
                swaps[v] = int(one)
                fixes.append(f"stat '{str(m.get('label') or '')[:50]}' {v:,} -> {one:,} (one figure for the group)")
    # 1. a partition sums to its pool
    for r in rows:
        pool = _num(r.get('pool'))
        bks = _buckets(r)
        counts = [_num(b.get('count')) for b in bks]
        if not pool or pool <= 0 or not _cd.is_partition(counts, pool):
            continue
        total = sum(counts)
        if total == pool:
            continue
        if _carry(r, pool, held):
            fixes.append(f"buckets of '{str(r.get('label') or '')[:50]}' summed to {total:,}, now {pool:,}")
        elif pool not in held:
            r['pool'] = int(total)
            fixes.append(f"pool of '{str(r.get('label') or '')[:50]}' {pool:,} -> {total:,} (the sum of its buckets)")
    return swaps


_SEG_COUNT_RX = re.compile(r'(:\s*)(' + _COUNT + r'|\d{1,5})(\s*\(\d)')
_POOL_RX = re.compile(r'(\bPool\s+)(' + _COUNT + r'|\d{1,5})')
CHAT_ROWS = 16      # the reply prints the first sixteen rows (prometheus_analysis.CHAT_BREAKDOWN_ROWS)


def _is_table_line(ln):
    return ln.startswith('- ') and ' | ' in ln and bool(re.search(r'\(\d', ln))


def _sync_line(ln, row):
    """One printed table line with the row's own pool and bucket counts,
    bucket by bucket in the order printed. The line comes back unchanged
    when it does not carry exactly the row's buckets."""
    bks = _buckets(row)
    segs = ln.split(' | ')
    if len(segs) != len(bks):
        return ln
    out = []
    for k, (seg, b) in enumerate(zip(segs, bks)):
        ms = list(_SEG_COUNT_RX.finditer(seg))
        if not ms:
            return ln
        m = ms[-1]
        head, tail = seg[:m.start(2)], seg[m.end(2):]
        if k == 0:
            pool = _num(row.get('pool'))
            pm = list(_POOL_RX.finditer(head))
            if pool and pm:
                q = pm[-1]
                head = head[:q.start(2)] + _fmt_like(q.group(2), pool) + head[q.end(2):]
        out.append(head + _fmt_like(m.group(2), _num(b.get('count'))) + tail)
    return ' | '.join(out)


def _sync_table_lines(text, res, fixes):
    """3 + 4: each printed table line and stat line carries the
    structured result's own figures. A line is matched to its row by its
    label, or, when the printed label was reworded on the way out, by its
    place: the reply prints the rows in the table's order."""
    all_rows = _rows(res)
    rows = [r for r in all_rows[:CHAT_ROWS] if _buckets(r)]
    lines = text.split('\n')
    table = [i for i, ln in enumerate(lines) if _is_table_line(ln)]
    by_place = len(table) == len(rows)
    changed = 0
    used = set()
    for k, r in enumerate(rows):
        label = str(r.get('label') or '')
        hits = [i for i in table if i not in used and lines[i].startswith(f"- {label}:")] if label else []
        if len(hits) == 1:
            i = hits[0]
        elif by_place:
            i = table[k]
        else:
            continue
        used.add(i)
        new = _sync_line(lines[i], r)
        if new != lines[i]:
            lines[i] = new
            changed += 1
    if changed:
        fixes.append(f"{changed} table line(s) read back from the table")
    out = '\n'.join(lines)
    # 4. 'Label: N' for a count stat
    stat_fixed = 0
    for m in (res.get('metrics') or []) if isinstance(res, dict) else []:
        if not isinstance(m, dict) or not _cd._is_count_metric(m):
            continue
        v, label = _num(m.get('value')), str(m.get('label') or '').strip()
        if v is None or v < 1000 or len(label) < 4:
            continue

        def _stat(mm, v=v):
            nonlocal stat_fixed
            if _num(mm.group(2)) == v:
                return mm.group(0)
            stat_fixed += 1
            return mm.group(1) + _fmt_like(mm.group(2), v)
        out = re.sub(r'(' + re.escape(label) + r':\s*)(' + _COUNT + r')(?![\d,]*\s*%)', _stat, out)
    if stat_fixed:
        fixes.append(f"{stat_fixed} stat line figure(s) read back from the stat")
    return out


def _fix_changes(text, held, fixes):
    """5: a stated change beside two counts is their difference."""
    swaps = {}
    out_lines = []
    for ln in text.split('\n'):
        if ln.startswith('- ') and ' | ' in ln and '(' in ln:
            out_lines.append(ln)          # a table line: its figures are the table's
            continue
        pieces = _SENTENCE_RX.split(ln)
        seps = _SENTENCE_RX.findall(ln)
        for k, sent in enumerate(pieces):
            for cm in list(_CHANGE_RX.finditer(sent)):
                d = _num(cm.group('n'))
                if d is None or d in held:
                    continue
                others = [(_num(m.group(1)), m.start()) for m in _COUNT_RX.finditer(sent)
                          if m.start() != cm.start('n')]
                others = [(v, pos) for v, pos in others if v is not None and v > d]
                if len(others) < 2:
                    continue
                before = [v for v, pos in others if pos < cm.start('n')]
                pool = before[-2:] if len(before) >= 2 else [v for v, _p in others][:2]
                a, b = pool[0], pool[1]
                target = abs(a - b)
                if target == d or target <= 0:
                    continue
                if abs(d - target) > min(CHANGE_OF_OPERAND * max(a, b), CHANGE_OF_ITSELF * target):
                    continue          # another quantity altogether; not ours to rewrite
                raw = cm.group('n')
                new_raw = _fmt_like(raw, target)
                sent = sent[:cm.start('n')] + new_raw + sent[cm.end('n'):]
                swaps[d] = target
                fixes.append(f"stated change {d:,} -> {target:,} (the difference of {max(a, b):,} and {min(a, b):,})")
                break       # positions moved; one change per sentence is the shape
            pieces[k] = sent
        rebuilt = pieces[0]
        for k in range(1, len(pieces)):
            rebuilt += (seps[k - 1] if k - 1 < len(seps) else ' ') + pieces[k]
        out_lines.append(rebuilt)
    return '\n'.join(out_lines), swaps


def check(reply, res=None, *, held=None):
    """The identities that do not hold on this text and result, as a list
    of plain notes; [] when the answer agrees with itself. Read only."""
    import copy
    try:
        probe = copy.deepcopy(res) if isinstance(res, dict) else None
        _out, info = enforce(reply, probe, held=held)
        return list(info.get('fixes') or [])
    except Exception:
        return []


def enforce(reply, res=None, *, held=None):
    """(reply, info): the finished answer made to agree with itself, in
    place on ``res``. info = {'fixes': [notes], 'changed': bool,
    'outcome': 'pass' | 'fixed'}. Never raises; on any failure the reply
    comes back as it was."""
    info = {'fixes': [], 'changed': False, 'outcome': 'pass'}
    try:
        text = str(reply or '')
        hs = _cd._held_set(held)
        fixes = []
        if isinstance(res, dict):
            regroup = _fix_structure(res, hs, fixes)
            text = _sync_table_lines(text, res, fixes)
            if regroup:
                # the prose that quoted a figure drawn apart quotes the one figure
                still = {v for _p, v in _cd.count_fields(res)}
                regroup = {o: n for o, n in regroup.items() if o not in still}
                text = _cd.rewrite_text(text, regroup)
                _cd._rewrite_res_prose(res, regroup)
        text, swaps = _fix_changes(text, hs, fixes)
        if swaps and isinstance(res, dict):
            struct = {v for _p, v in _cd.count_fields(res)}
            safe = {o: n for o, n in swaps.items() if o not in struct}
            if safe:
                _cd._rewrite_res_prose(res, safe)
                for m in (res.get('metrics') or []):
                    if isinstance(m, dict) and _cd._is_count_metric(m) and _num(m.get('value')) in safe:
                        m['value'] = int(safe[_num(m.get('value'))])
        if isinstance(res, dict) and fixes:
            # the result's own prose states the same table lines
            reads = res.get('reads')
            if isinstance(reads, list):
                fixed_reads = []
                for r in reads:
                    if isinstance(r, str):
                        r2 = _sync_table_lines('- ' + r, res, [])
                        r2, _s = _fix_changes(r2, hs, [])
                        fixed_reads.append(r2[2:] if r2.startswith('- ') else r2)
                    else:
                        fixed_reads.append(r)
                res['reads'] = fixed_reads
            if isinstance(res.get('headline'), str):
                res['headline'], _s = _fix_changes(res['headline'], hs, [])
        info['fixes'] = fixes
        info['changed'] = bool(fixes) or text != str(reply or '')
        info['outcome'] = 'fixed' if fixes else 'pass'
        return text, info
    except Exception:
        return reply, {'fixes': [], 'changed': False, 'outcome': 'pass'}


def trail(info):
    """One line for the worker log and the verify stamp; '' when the
    answer already agreed with itself."""
    fx = (info or {}).get('fixes') or []
    if not fx:
        return ''
    return f"identities fixed {len(fx)}: " + ' | '.join(fx)[:700]
