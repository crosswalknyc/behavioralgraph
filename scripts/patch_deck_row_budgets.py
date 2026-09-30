#!/usr/bin/env python3
"""Row budget guards: measured rows that would run past the slide
floor trim to their per-row budget instead of spilling over the
footer (second defect class the geometric overlap test caught)."""
import ast
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / 'deck_builder.py'
src = DB.read_text(encoding='utf-8')


def splice(s, old, new, desc):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f'[{desc}] anchor count {n}')
    return s.replace(old, new)


# ---- tiles_facts: budget the fact rows ----------------------------
old = '''    y = top + 1.79
    for f in _items(sl, "facts", 6):
        row_h = max(_block_h(f.get("label") or "", 11.5, 3.40),
                    _block_h(f.get("note") or "", 11.5, 5.99),
                    0.235) + 0.085
        txt(s, M, Inches(y), Inches(3.40), Inches(row_h - 0.04),
            f.get("label") or "", size=11.5)
        txt(s, M + Inches(3.50), Inches(y), Inches(1.80), Inches(0.28),
            f.get("fig") or "", size=11.5, bold=True, color=OLIVE)
        txt(s, M + Inches(5.50), Inches(y), Inches(5.99),
            Inches(row_h - 0.04), f.get("note") or "", size=11.5,
            color=BODY_LT)
        y += row_h
    if sl.get("read"):
        ry = max(6.30, y + 0.10)
        txt(s, M, Inches(ry), BAND, Inches(0.52), sl["read"],
            size=11.5, color=BODY_LT)'''
new = '''    y = top + 1.79
    facts = _items(sl, "facts", 6)
    if facts:
        floor_y = 6.20 if sl.get("read") else 6.88
        budget = max((floor_y - y) / len(facts), 0.32)
        for f in facts:
            label_t, _ = _fit_body(f.get("label") or "", (11.5,),
                                   3.40, budget - 0.085)
            note_t, _ = _fit_body(f.get("note") or "", (11.5,),
                                  5.99, budget - 0.085)
            row_h = max(_block_h(label_t, 11.5, 3.40),
                        _block_h(note_t, 11.5, 5.99),
                        0.235) + 0.085
            txt(s, M, Inches(y), Inches(3.40), Inches(row_h - 0.04),
                label_t, size=11.5)
            txt(s, M + Inches(3.50), Inches(y), Inches(1.80),
                Inches(0.28), f.get("fig") or "", size=11.5,
                bold=True, color=OLIVE)
            txt(s, M + Inches(5.50), Inches(y), Inches(5.99),
                Inches(row_h - 0.04), note_t, size=11.5,
                color=BODY_LT)
            y += row_h
    if sl.get("read"):
        ry = max(6.30, y + 0.10)
        txt(s, M, Inches(ry), BAND, Inches(0.52), sl["read"],
            size=11.5, color=BODY_LT)'''
src = splice(src, old, new, 'tiles_facts budget')

# ---- bars: budget the row labels ----------------------------------
old = '''    avail = (6.02 if sl.get("read") else 6.60) - y
    step = min(0.52, max(0.30, avail / n))
    for r in rows:
        accent = bool(r.get("accent"))
        row_step = max(step, _line_count(
            r.get("label") or "", 11.5, lab_w,
            bold=accent) * 0.235 + 0.065)
        txt(s, M, Inches(y), Inches(lab_w), Inches(row_step - 0.04),
            r.get("label") or "", size=11.5, bold=accent, color=ink)'''
new = '''    floor_y = 6.02 if sl.get("read") else 6.60
    avail = floor_y - y
    step = min(0.52, max(0.30, avail / n))
    labels, steps = [], []
    for r in rows:
        lab = r.get("label") or ""
        steps.append(max(step, _line_count(
            lab, 11.5, lab_w,
            bold=bool(r.get("accent"))) * 0.235 + 0.065))
        labels.append(lab)
    if y + sum(steps) > floor_y + 0.05:
        labels = [_fit_body(lab, (11.5,), lab_w, 0.30)[0]
                  for lab in labels]
        steps = [step] * len(rows)
    for ri, r in enumerate(rows):
        accent = bool(r.get("accent"))
        row_step = steps[ri]
        txt(s, M, Inches(y), Inches(lab_w), Inches(row_step - 0.04),
            labels[ri], size=11.5, bold=accent, color=ink)'''
src = splice(src, old, new, 'bars budget')

# ---- split_stats_bars: budget the row labels ----------------------
old = '''    step = min(0.50, max(0.36, (5.80 - y) / n))
    for r in rows:
        accent = bool(r.get("accent"))
        row_step = max(step, _line_count(
            r.get("label") or "", 11.5, 2.05,
            bold=accent) * 0.235 + 0.065)
        txt(s, rx, Inches(y), Inches(2.05), Inches(row_step - 0.04),
            r.get("label") or "", size=11.5, bold=accent)'''
new = '''    floor_y = 5.80
    step = min(0.50, max(0.36, (floor_y - y) / n))
    labels, steps = [], []
    for r in rows:
        lab = r.get("label") or ""
        steps.append(max(step, _line_count(
            lab, 11.5, 2.05,
            bold=bool(r.get("accent"))) * 0.235 + 0.065))
        labels.append(lab)
    if y + sum(steps) > floor_y + 0.05:
        labels = [_fit_body(lab, (11.5,), 2.05, 0.30)[0]
                  for lab in labels]
        steps = [step] * len(rows)
    for ri, r in enumerate(rows):
        accent = bool(r.get("accent"))
        row_step = steps[ri]
        txt(s, rx, Inches(y), Inches(2.05), Inches(row_step - 0.04),
            labels[ri], size=11.5, bold=accent)'''
src = splice(src, old, new, 'split budget')

# ---- table: budget the cell rows ----------------------------------
old = '''    y = top + 0.39
    for ri, row in enumerate(rows):
        row_acc = isinstance(acc_row, int) and ri == acc_row
        row_h = 0.52
        for ci in range(n):
            cell = str(row[ci]) if ci < len(row) else ""
            row_h = max(row_h, _block_h(
                cell, 14, first_w if ci == 0 else rest_w) + 0.10)
        for ci in range(n):
            cell = str(row[ci]) if ci < len(row) else ""
            cell_acc = row_acc and isinstance(acc_col, int) \\
                and ci == acc_col
            txt(s, Inches(xs[ci]), Inches(y),
                Inches(first_w if ci == 0 else rest_w),
                Inches(row_h - 0.06), cell, size=14,
                bold=(ci == 0 and row_acc) or cell_acc,
                color=OLIVE if cell_acc else GRAPHITE,
                align=PP_ALIGN.LEFT if ci == 0 else PP_ALIGN.RIGHT)
        y += row_h'''
new = '''    y = top + 0.39
    has_read = bool(sl.get("read") or sl.get("read2"))
    floor_y = (6.98 - 1.10 - 0.12) if has_read else 6.88
    budget = max((floor_y - y) / max(len(rows), 1), 0.42)
    for ri, row in enumerate(rows):
        row_acc = isinstance(acc_row, int) and ri == acc_row
        cells = []
        row_h = 0.42
        for ci in range(n):
            cell = str(row[ci]) if ci < len(row) else ""
            cw_in = first_w if ci == 0 else rest_w
            cell, c_size = _fit_body(cell, (14, 12.5),
                                     cw_in, budget - 0.10)
            cells.append((cell, c_size))
            row_h = max(row_h,
                        _block_h(cell, c_size, cw_in) + 0.10)
        for ci in range(n):
            cell, c_size = cells[ci]
            cell_acc = row_acc and isinstance(acc_col, int) \\
                and ci == acc_col
            txt(s, Inches(xs[ci]), Inches(y),
                Inches(first_w if ci == 0 else rest_w),
                Inches(row_h - 0.06), cell, size=c_size,
                bold=(ci == 0 and row_acc) or cell_acc,
                color=OLIVE if cell_acc else GRAPHITE,
                align=PP_ALIGN.LEFT if ci == 0 else PP_ALIGN.RIGHT)
        y += row_h'''
src = splice(src, old, new, 'table budget')

ast.parse(src)
DB.write_text(src, encoding='utf-8')
print('row budgets applied, ast clean')
