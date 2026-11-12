"""Stored figures bind the next pass (2026-11-04, Liz's gender by door).

Two passes on the same subject printed two different gender splits for
the same viewer door: pass one had Film music first at 57.3% female,
pass three had it at 48.2%, pass four at 40.3%. Each pass had been
generated against a different base and persisted under a different
subject label, so the verification pass never saw the earlier figure and
the model drew a fresh one. Liz's agent withheld gender by door because
the passes contradicted each other.

The rule here is mechanical and runs before verification on every
generated read, on the dashboard and on the API door alike: a figure
already stored for the same subject, the same definition (dimension and
row, the same bucket labels) and the same window is the figure. The new
pass keeps it and the reply says so in one plain line. A different
window, or an ask that says to recompute without reusing earlier figures,
leaves the new pass alone.

Pure functions over the result dict and the ledger entries; no S3, no
model. Never raises into a read.
"""
from __future__ import annotations

import re

__all__ = ['bind_to_stored', 'already_reported_line', 'row_definition', 'stamp_definitions']

# the reader asked for a fresh, independent draw: nothing binds
_FRESH_RX = re.compile(
    r"\b(?:independent(?:ly)?\s+(?:verif|recomput|re-?run|re-?read)|recompute[^.]{0,60}without\s+reusing|"
    r"without\s+reusing\s+any\s+figure|do\s+not\s+reuse|don'?t\s+reuse|from\s+scratch|fresh\s+draw|"
    r"start\s+over|ignore\s+(?:the\s+)?(?:earlier|previous|prior)\s+(?:figures?|numbers?|reads?))\b", re.I)
# the reader pinned the window to the earlier read
_SAME_WINDOW_RX = re.compile(r"\bsame\s+(?:base\s+(?:and|,)\s+)?window\b|\bsame\s+base\b", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()


def _window_of(d):
    ws = str((d or {}).get('ws') or (d or {}).get('window_start') or '')[:10]
    we = str((d or {}).get('we') or (d or {}).get('window_end') or '')[:10]
    return ws, we


def _same_window(new_res, entry, ask_text=''):
    nws, nwe = _window_of(new_res)
    ows, owe = _window_of(entry)
    if not (nws and nwe and ows and owe):
        return True                      # an undated side is a standing figure
    if (nws, nwe) == (ows, owe):
        return True
    return bool(_SAME_WINDOW_RX.search(str(ask_text or '')))


def row_definition(row, dimension='', share_basis='', window=''):
    """One plain sentence naming what a row measures, kept on the row at
    persist so the next pass reads the same definition ('Gender for Film
    music first, share within the cut, Nov 4 2025 to Nov 4 2026')."""
    r = row if isinstance(row, dict) else {}
    dim = str(r.get('dimension') or dimension or '').strip()
    label = str(r.get('label') or '').strip()
    bits = [f"{dim} for {label}" if dim and _norm(dim) not in _norm(label) else label]
    basis = str(share_basis or '').strip()
    if basis:
        bits.append(basis)
    if window:
        bits.append(str(window))
    return ', '.join(b for b in bits if b)[:160]


def stamp_definitions(res):
    """Every breakdown row carries its definition and the read's window
    before the entry persists. In place; returns res."""
    try:
        bd = (res or {}).get('breakdown')
        if not isinstance(bd, dict):
            return res
        win = str(res.get('window_label') or '').strip()
        if not win and res.get('window_start') and res.get('window_end'):
            win = f"{res['window_start']} to {res['window_end']}"
        for r in (bd.get('rows') or []):
            if isinstance(r, dict) and not r.get('definition'):
                r['definition'] = row_definition(r, bd.get('dimension'), bd.get('share_basis'), win)
    except Exception:
        pass
    return res


def _row_key(row, dimension=''):
    return (_norm(row.get('dimension') or dimension), _norm(row.get('label')))


def _bucket_map(row):
    out = {}
    for b in (row.get('buckets') or []):
        if isinstance(b, dict) and str(b.get('label') or '').strip():
            out[_norm(b.get('label'))] = b
    return out


def _scope_of_result(res, ask_text=''):
    """The platforms the new pass is scoped to: what the read says about
    itself (its universe, cohort and table), and the question."""
    try:
        from . import platform_scope as _ps
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
        own = _ps.scope_of(res.get('universe'), res.get('cohort'), bd.get('dimension'), bd.get('share_basis'))
        return own or _ps.scope_of(ask_text)
    except Exception:
        return frozenset()


def _scope_of_entry(entry):
    try:
        from . import platform_scope as _ps
        bd = entry.get('breakdown') if isinstance(entry.get('breakdown'), dict) else {}
        own = _ps.scope_of(entry.get('universe'), entry.get('cohort'), bd.get('dimension'), bd.get('share_basis'))
        return own or _ps.scope_of(entry.get('subject'), entry.get('q'))
    except Exception:
        return frozenset()


def _same_scope(new_res, entry, ask_text=''):
    """A figure on record binds a table on the same platform scope only
    (2026-11-11, Armine: the all-platform category shares were carried
    into a YouTube-only table)."""
    return _scope_of_result(new_res, ask_text) == _scope_of_entry(entry)


def _stored_rows(prior_entries, new_res, ask_text):
    """{(dimension, label): (row, entry)} from the newest compatible
    entry that carries that row; bucket rows and plain rows alike."""
    out = {}
    ents = [e for e in (prior_entries or []) if isinstance(e, dict) and not e.get('retired')]
    ents.sort(key=lambda e: str(e.get('ts') or ''), reverse=True)
    for e in ents:
        bd = e.get('breakdown') if isinstance(e.get('breakdown'), dict) else None
        if not bd or not _same_window(new_res, e, ask_text) or not _same_scope(new_res, e, ask_text):
            continue
        for r in (bd.get('rows') or []):
            if not isinstance(r, dict) or not str(r.get('label') or '').strip():
                continue
            k = _row_key(r, bd.get('dimension'))
            if k not in out:
                out[k] = (r, e, bd)
            # a label-only key lets a row whose dimension was unlabeled
            # in one pass still find itself in the other
            lk = ('', k[1])
            if lk not in out:
                out[lk] = (r, e, bd)
    return out


def _stored_metrics(prior_entries, new_res, ask_text):
    out = {}
    ents = [e for e in (prior_entries or []) if isinstance(e, dict) and not e.get('retired')]
    ents.sort(key=lambda e: str(e.get('ts') or ''), reverse=True)
    for e in ents:
        if not _same_window(new_res, e, ask_text) or not _same_scope(new_res, e, ask_text):
            continue
        coh = _norm(e.get('cohort'))
        for m in (e.get('metrics') or []):
            if isinstance(m, dict) and isinstance(m.get('value'), (int, float)):
                k = (coh, _norm(m.get('name')))
                if k[1] and k not in out:
                    out[k] = (m, e)
    return out


def bind_to_stored(res, prior_entries, ask_text=''):
    """Hold the new pass to the figures already stored for the same
    definition and window. Returns (res, bound) where bound is a list of
    {'name', 'ts'} for the reply line. In place on res; never raises."""
    bound = []
    try:
        if not isinstance(res, dict) or not prior_entries:
            return res, bound
        if _FRESH_RX.search(str(ask_text or '')):
            return res, bound
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else None
        stored = _stored_rows(prior_entries, res, ask_text) if bd else {}
        new_coh = _norm(res.get('cohort'))
        for r in ((bd or {}).get('rows') or []):
            if not isinstance(r, dict):
                continue
            hit = stored.get(_row_key(r, bd.get('dimension'))) or stored.get(('', _norm(r.get('label'))))
            if not hit:
                continue
            srow, sentry, sbd = hit
            nb, sb = _bucket_map(r), _bucket_map(srow)
            if nb and sb:
                shared = [k for k in nb if k in sb]
                if len(shared) < max(2, int(round(0.6 * len(nb)))):
                    continue           # a different bucket set is a different definition
                changed = False
                for k in shared:
                    tgt, src = nb[k], sb[k]
                    if (tgt.get('count'), tgt.get('share_pct')) != (src.get('count'), src.get('share_pct')):
                        changed = True
                    tgt['count'] = src.get('count')
                    tgt['share_pct'] = src.get('share_pct')
                    tgt.pop('mode', None)
                if len(shared) == len(nb) and isinstance(srow.get('pool'), (int, float)):
                    r['pool'] = int(srow['pool'])
                elif r.get('buckets'):
                    r['pool'] = int(sum(int(b.get('count') or 0) for b in r['buckets']))
                if r.get('buckets'):
                    top = max(range(len(r['buckets'])), key=lambda i: int(r['buckets'][i].get('count') or 0))
                    r['buckets'][top]['mode'] = True
                if isinstance(srow.get('share_pct'), (int, float)) and len(shared) == len(nb):
                    r['share_pct'] = float(srow['share_pct'])
                r['bound_from'] = str(sentry.get('ts') or '')[:10]
                if changed:
                    bound.append({'name': _row_name(r, bd.get('dimension')), 'ts': str(sentry.get('ts') or '')[:10]})
            elif not nb and not sb:
                # a plain share row: the same cohort's same row is the figure
                if new_coh != _norm(sentry.get('cohort')):
                    continue
                before = (r.get('share_pct'), r.get('penetration_pct'))
                r['share_pct'] = float(srow.get('share_pct') or 0) if isinstance(srow.get('share_pct'), (int, float)) else r.get('share_pct')
                if isinstance(srow.get('penetration_pct'), (int, float)):
                    r['penetration_pct'] = float(srow['penetration_pct'])
                r['bound_from'] = str(sentry.get('ts') or '')[:10]
                if before != (r.get('share_pct'), r.get('penetration_pct')):
                    bound.append({'name': _row_name(r, bd.get('dimension')), 'ts': str(sentry.get('ts') or '')[:10]})
        if bd and any(r.get('bound_from') for r in (bd.get('rows') or [])):
            _renormalize(bd)
        smet = _stored_metrics(prior_entries, res, ask_text)
        for m in (res.get('metrics') or []):
            if not isinstance(m, dict) or not isinstance(m.get('value'), (int, float)):
                continue
            hit = smet.get((new_coh, _norm(m.get('name'))))
            if not hit:
                continue
            sm, sentry = hit
            if float(sm['value']) != float(m['value']):
                m['value'] = sm['value']
                bound.append({'name': str(m.get('label') or m.get('name') or 'figure'), 'ts': str(sentry.get('ts') or '')[:10]})
        return res, bound
    except Exception:
        return res, bound


def _renormalize(bd):
    """Row shares sum to 100 after any row was replaced; the largest row
    absorbs the residual (the house convention)."""
    rows = [r for r in (bd.get('rows') or []) if isinstance(r, dict) and isinstance(r.get('share_pct'), (int, float))]
    total = sum(float(r['share_pct']) for r in rows)
    if not rows or total <= 0:
        return
    for r in rows:
        r['share_pct'] = round(float(r['share_pct']) * 100.0 / total, 4)
    resid = round(100.0 - sum(float(r['share_pct']) for r in rows), 4)
    rows[0]['share_pct'] = round(float(rows[0]['share_pct']) + resid, 4)


def _row_name(row, dimension=''):
    """The name a reader knows the row by: its label, with the row's own
    dimension ahead of it when the label does not already say it. The
    table-level dimension ('Path cut by Gender') is never prefixed."""
    dim = str(row.get('dimension') or '').strip()
    label = str(row.get('label') or '').strip()
    if dim and _norm(dim) not in _norm(label):
        return f"{dim}, {label}"
    return label


def already_reported_line(bound):
    """'Already reported in this thread and kept as first reported: Gender,
    Film music first; Gender, Series music first (Nov 4).' One line, at
    most six names."""
    items = [b for b in (bound or []) if isinstance(b, dict) and b.get('name')]
    if not items:
        return ''
    seen, names = set(), []
    for b in items:
        if b['name'] not in seen:
            seen.add(b['name'])
            names.append(b['name'])
    shown = '; '.join(names[:6])
    tail = f" and {len(names) - 6} more" if len(names) > 6 else ''
    when = _when(items[0].get('ts'))
    return (f"Already reported in this thread and kept as first reported: {shown}{tail}"
            + (f" ({when})" if when else '') + '.')


def _when(ts):
    s = str(ts or '')[:10]
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return ''
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    try:
        return f"{months[int(m.group(2)) - 1]} {int(m.group(3))}"
    except Exception:
        return ''
