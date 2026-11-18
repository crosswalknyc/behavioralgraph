"""A read with no figures is repaired or run again, never thrown at the
reader (2026-11-18, Liz).

Liz asked for a table (nine series, the nine combined and everyone on
the service, by percent women and median age, plus two age
distributions). The pass came back with nothing the read contract could
use, the coherence pass raised "measurement read carried no usable
metrics", the job ended in an error, and the calm line was all she got.
The same morning two sibling asks carried her rule "Say plainly if a
line cannot be measured instead of estimating"; one answered a single
title of nine and wrote that the rest "carry no play count".

Three things, in the order a read meets them:

  ``ask_blocks(text)``   prompt blocks read off the question before the
                         pass runs: the credit the reader set
                         (methodology.credit_rule), what "cannot be
                         measured" means in this house when the question
                         states that rule, and the grid contract for a
                         demographic grid (prometheus.demo_grid).
  ``salvage(data)``      figures a reply carried in a shape the contract
                         does not read (metrics as a mapping, values as
                         text, a table under another key, rows with a
                         pool and no share), moved into the contract.
  ``coherent(...)``      the coherence pass with the repair around it: a
                         grid is shaped and completed first; a reply
                         with no usable figure is salvaged, then run
                         ONE more time with a corrective block, then
                         salvaged again; only then ``EmptyRead``, which
                         the read core turns into a held read (the calm
                         line, a fresh pass from the sweep, ops told),
                         never an unhandled error.

Pure except for the two callables handed in (``enforce``, ``again``).
Regression: scripts/test_pm_demo_grid.py.
"""
from __future__ import annotations

import re

__all__ = ['EmptyRead', 'ask_blocks', 'measured_guidance', 'salvage', 'coherent', 'corrective_block', 'states_measure_rule']


class EmptyRead(ValueError):
    """The read carried no usable figure after the repair and one more pass."""


_MEASURE_RULE_RX = re.compile(
    r"\bsay\s+(?:so\s+)?plainly\s+(?:if|when|where)\b[^.]{0,80}\bcannot\s+be\s+measured\b|"
    r"\b(?:if|when|where)\s+(?:a|any|the)\s+(?:line|figure|number|row|cell)\s+cannot\s+be\s+measured\b|"
    r"\binstead\s+of\s+estimating\b|\bdo\s+not\s+estimate\b|\bnever\s+estimate\b", re.I)

MEASURED_GUIDANCE = (
    "WHAT \"CANNOT BE MEASURED\" MEANS HERE (the question asks you to say plainly when a line cannot be measured):\n"
    "- A line cannot be measured only when the behavior has no digital trace: over-the-air or linear TV tune-in, "
    "in-store purchases, foot traffic, terrestrial radio. Say that plainly, in one sentence, for such a line.\n"
    "- Every other line the question lists is a digital behavior (streaming, app and site use, search, social, "
    "ecommerce) and IS measured at the person level. Produce its count. A count that the blocks above do not "
    "already carry is still measured: it is not an estimate and it is never left blank.\n"
    "- Never write that a title, a threshold, a window, a union, a platform total or a split is not on record, "
    "carries no count, is not cut, or stays unanswered. Never answer one item of a list and decline the rest.\n"
    "- Every metric carries a numeric value. A sentence is never a value.\n")


def states_measure_rule(text):
    return bool(_MEASURE_RULE_RX.search(str(text or '')))


def measured_guidance(text):
    return MEASURED_GUIDANCE if states_measure_rule(text) else ''


def ask_blocks(text):
    """The prompt blocks this question adds, in order. Never raises."""
    out = []
    try:
        from . import methodology as _meth
        cr = _meth.credit_rule(text)
        if cr:
            out.append('VIEWER CREDIT (set by the question):\n' + cr.rstrip('\n'))
    except Exception:
        pass
    try:
        mg = measured_guidance(text)
        if mg:
            out.append(mg.rstrip('\n'))
    except Exception:
        pass
    try:
        from . import demo_grid as _dg
        gp = _dg.plan(text)
        if gp:
            out.append(_dg.guidance(gp))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- salvage
_PCT_UNITS = ('pct', 'percent', 'percentage', '%')
_METRIC_LIST_KEYS = ('figures', 'measures', 'stats', 'numbers', 'results', 'values', 'lines')
_ROW_LIST_KEYS = ('rows', 'table', 'grid', 'groups', 'series', 'items')
_ROW_LABEL_KEYS = ('label', 'name', 'group', 'title', 'series', 'row', 'category')
_ROW_SHARE_KEYS = ('share_pct', 'share', 'pct', 'percent', 'percentage')
_ROW_POOL_KEYS = ('pool', 'count', 'people', 'viewers', 'users', 'value', 'total')
_NUM_RX = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _parse_value(v, unit=''):
    """(number, unit) from a metric value that may be text ('63.8%',
    '1,234,567 viewers', '2.4 million'); (None, unit) when it holds no
    number or more than one."""
    if isinstance(v, bool):
        return None, unit
    if isinstance(v, (int, float)):
        return (float(v), unit) if v == v else (None, unit)
    if isinstance(v, dict):
        for k in ('value', 'count', 'amount', 'number', 'pct', 'percent'):
            if k in v:
                return _parse_value(v.get(k), unit or str(v.get('unit') or ''))
        return None, unit
    s = str(v or '').strip()
    if not s:
        return None, unit
    nums = _NUM_RX.findall(s)
    if len(nums) != 1:
        return None, unit
    x = float(nums[0].replace(',', ''))
    low = s.lower()
    if '%' in s or 'percent' in low:
        return x, unit or 'pct'
    if re.search(r"\bbillion\b|\bbn\b", low):
        x *= 1e9
    elif re.search(r"\bmillion\b|\d\s*m\b", low):
        x *= 1e6
    elif re.search(r"\bthousand\b|\d\s*k\b", low):
        x *= 1e3
    return x, unit


def _as_metric_list(mets):
    if isinstance(mets, dict):
        out = []
        for k, v in mets.items():
            if isinstance(v, dict):
                out.append(dict(v, name=str(v.get('name') or k), label=str(v.get('label') or k)))
            else:
                out.append({'name': str(k), 'label': str(k).replace('_', ' '), 'value': v})
        return out
    return [m for m in (mets or []) if isinstance(m, dict)] if isinstance(mets, list) else []


def salvage(data):
    """The reply with its figures moved into the contract's shape, or
    the same object when nothing could be moved. Never raises."""
    try:
        if isinstance(data, list):
            dicts = [d for d in data if isinstance(d, dict)]
            if not dicts:
                return data
            merged = {}
            for d in dicts:                      # several objects: the first names win, lists add up
                for k, v in d.items():
                    if k not in merged or merged[k] in (None, '', [], {}):
                        merged[k] = v
                    elif isinstance(v, list) and isinstance(merged[k], list) and k in ('metrics', 'reads'):
                        merged[k] = merged[k] + v
            data = merged
        if not isinstance(data, dict):
            return data
        out, changed = dict(data), False
        for wrap in ('answer', 'result', 'read', 'response', 'data'):
            inner = out.get(wrap)
            if isinstance(inner, dict) and (inner.get('metrics') or inner.get('breakdown') or inner.get('headline')):
                for k, v in inner.items():
                    if out.get(k) in (None, '', [], {}):
                        out[k] = v
                        changed = True
        mets = _as_metric_list(out.get('metrics'))
        if not isinstance(out.get('metrics'), list):
            changed = changed or bool(mets)
        for key in _METRIC_LIST_KEYS:
            extra = _as_metric_list(out.get(key))
            if extra and not mets:
                mets, changed = extra, True
        fixed = []
        for i, m in enumerate(mets):
            m = dict(m)
            if not str(m.get('name') or '').strip():
                m['name'] = re.sub(r"[^a-z0-9]+", '_', str(m.get('label') or f"metric_{i + 1}").lower()).strip('_')[:48] or f"metric_{i + 1}"
                changed = True
            v = m.get('value')
            if v is None:
                for alt in ('count', 'amount', 'number', 'pct', 'percent'):
                    if m.get(alt) is not None:
                        v = m.get(alt)
                        break
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                num, unit = _parse_value(v, str(m.get('unit') or ''))
                if num is None:
                    continue
                m['value'] = num
                if unit:
                    m['unit'] = unit
                changed = True
            fixed.append(m)
        if changed or len(fixed) != len(mets):
            out['metrics'] = fixed
            changed = True
        bd = out.get('breakdown')
        rows = None
        if isinstance(bd, list):
            rows, bd = bd, {}
        elif isinstance(bd, dict):
            rows = bd.get('rows') if isinstance(bd.get('rows'), list) else None
        else:
            bd = {}
        if not rows:
            for key in _ROW_LIST_KEYS:
                src = out.get(key)
                if isinstance(src, dict) and isinstance(src.get('rows'), list):
                    bd = dict(src, **bd)
                    src = src.get('rows')
                if isinstance(src, list) and any(isinstance(r, dict) for r in src):
                    rows = src
                    break
        if rows:
            new_rows, moved = [], False
            for r in rows:
                if not isinstance(r, dict):
                    continue
                r = dict(r)
                if not str(r.get('label') or '').strip():
                    for k in _ROW_LABEL_KEYS:
                        if isinstance(r.get(k), str) and r[k].strip():
                            r['label'] = r[k].strip()
                            moved = True
                            break
                share, _u = _parse_value(r.get('share_pct'))
                if share is None or share <= 0:
                    for k in _ROW_SHARE_KEYS[1:]:
                        s2, _u = _parse_value(r.get(k))
                        if s2 is not None and 0 < s2 <= 100:
                            r['share_pct'] = s2
                            moved = True
                            break
                elif not isinstance(r.get('share_pct'), (int, float)):
                    r['share_pct'] = share
                    moved = True
                if r.get('pool') is None:
                    for k in _ROW_POOL_KEYS[1:]:
                        pv, _u = _parse_value(r.get(k))
                        if pv is not None and pv >= 1000:
                            r['pool'] = int(round(pv))
                            moved = True
                            break
                new_rows.append(r)
            # rows that carry a count and no share take their share of the counts
            need = [r for r in new_rows if not isinstance(r.get('share_pct'), (int, float)) or r['share_pct'] <= 0]
            if need and all(isinstance(r.get('pool'), (int, float)) and r['pool'] > 0 for r in new_rows):
                tot = float(sum(r['pool'] for r in new_rows))
                for r in new_rows:
                    r['share_pct'] = round(100.0 * r['pool'] / tot, 4)
                moved = True
            if moved or not isinstance(out.get('breakdown'), dict) or out['breakdown'].get('rows') is not rows:
                nbd = dict(bd)
                nbd['rows'] = new_rows
                nbd.setdefault('dimension', str(bd.get('dimension') or 'Group'))
                nbd.setdefault('share_basis', str(bd.get('share_basis') or 'share of the total'))
                out['breakdown'] = nbd
                changed = True
        return out if changed else data
    except Exception:
        return data


def corrective_block(reason=''):
    """The block the one extra pass runs with."""
    return (
        "YOUR LAST REPLY COULD NOT BE USED: it carried no figure in the JSON shape the system prompt sets "
        f"({str(reason or 'no usable metrics')[:160]}). Answer again, and this time:\n"
        "- \"metrics\" is a LIST of objects; every one has a numeric \"value\" (a number, never text, never null) and a unit.\n"
        "- A table goes in \"breakdown\": every row has a \"label\" and a numeric \"share_pct\"; a row that is a group "
        "of people also carries \"pool\" (its people) and, for a distribution, \"buckets\".\n"
        "- Every line the question lists gets its figure. A digital behavior is measured; a count the blocks above do "
        "not already carry is still measured and is never left out, left null, or replaced by a sentence.\n"
        "- JSON only.")


def _model_data(result):
    """The parsed reply out of a model call result, or None."""
    if isinstance(result, dict) and 'success' in result:
        if not result.get('success'):
            return None
        result = result.get('data')
    if isinstance(result, list):
        result = next((d for d in result if isinstance(d, dict)), None)
    if not isinstance(result, dict):
        return None
    if str(result.get('action') or '').strip().lower() == 'decline':
        return None
    return result


def coherent(data, *, enforce, again=None, question='', subject='', note=None, log=None, facts=None):
    """(res, data): ``enforce(data)`` with the repair around it.

    A demographic grid (prometheus.demo_grid.plan on the question) is
    shaped first and its missing groups run once more through
    ``again(prompt_block)``. A ValueError from ``enforce`` (no usable
    metrics, a payload that is not an object, an incomplete grid) is
    answered by ``salvage``; then by ONE more pass (``again`` with the
    corrective block), shaped and salvaged the same way. ``EmptyRead``
    when none of it produced a figure. Any other exception is the
    caller's, unchanged. ``note(text)`` tells ops what was filled;
    ``log(text)`` gets one line per repair step. ``facts(plan)`` hands a
    grid the figures on file for its comparison row (prometheus.
    grid_facts), read once and kept for every pass of the read."""
    def _say(msg):
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    def _ask(block):
        if not callable(again):
            return None
        try:
            return _model_data(again(block))
        except Exception:
            return None

    gp = None
    try:
        from . import demo_grid as _dg
        gp = _dg.plan(question)
    except Exception:
        _dg, gp = None, None

    if gp and callable(facts):
        try:
            _dg.set_facts(gp, facts(gp))
        except Exception:
            pass

    def _grid(d, piece=True):
        if not gp:
            return d
        shaped = _dg.shape(d if isinstance(d, dict) else (salvage(d) if isinstance(d, list) else {}), gp)
        miss = _dg.missing_groups(shaped)
        if miss and piece:
            _say(f"grid: {len(miss)} of {len(gp['groups'])} groups missing after the pass; running them once more")
        return _dg.complete(shaped, gp, ask=(_ask if piece else None), subject=subject, note=note)

    first_err = None
    for attempt in ('reply', 'second pass'):
        if attempt == 'second pass':
            d2 = _ask(corrective_block(str(first_err)) + (('\n\n' + _dg.guidance(gp)) if gp else ''))
            if d2 is None:
                break
            data = d2
            _say('second pass answered')
        try:
            # the missing groups of a grid get one piece pass; the second pass is the whole grid again and is
            # completed from what it carries, so a read costs three passes at most
            d = _grid(data, piece=(attempt == 'reply'))
            return enforce(d), d
        except ValueError as e:
            first_err = first_err or e
            _say(f"{attempt}: {e}")
        if not gp:
            s = salvage(data)
            if s is not data:
                try:
                    res = enforce(s)
                    _say(f"{attempt}: figures moved into the contract shape")
                    return res, s
                except ValueError as e:
                    _say(f"{attempt} after salvage: {e}")
    raise EmptyRead(str(first_err or 'measurement read carried no usable metrics'))
