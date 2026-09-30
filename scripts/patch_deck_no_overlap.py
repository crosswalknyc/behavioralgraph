#!/usr/bin/env python3
"""Deck text overlap fix (Jenna 2026-09-30: "the deck prometheus made
has overlapping text: this is a common issue.")

Root cause: every y position in deck_builder.py was hardcoded, so any
string that wrapped past its assumed line count rendered on top of
the element below it (the PAW Patrol cover title wrapped to three
lines at 34pt and landed on the intro).

Fix, systemic: a measurement layer (real Inter metrics through
reportlab when present, a deliberately roomy estimate otherwise) and
measure-and-flow positioning on every slide type. Oversize headlines
step down a size until they fit their band; body copy steps down then
trims at a word boundary rather than spilling out of its card. The
plan prompt also gains the plain-english header ceiling so titles
stop arriving 19 words long.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'deck_builder.py'
PA = ROOT / 'prometheus_analysis.py'


def splice(s, old, new, desc):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f'[{desc}] anchor count {n}')
    return s.replace(old, new)


src = DB.read_text(encoding='utf-8')

# ---- A. measurement layer after _spc ------------------------------
old = '''def _spc(run, hundredths):
    run._r.get_or_add_rPr().set("spc", str(int(hundredths)))
'''
new = '''def _spc(run, hundredths):
    run._r.get_or_add_rPr().set("spc", str(int(hundredths)))


# ---- text measurement (2026-09-30: no overlapping text) --------------------
# Jenna: "the deck prometheus made has overlapping text: this is a
# common issue." Every stacked element now measures the text above it
# and flows below it, and oversize headlines step down a size until
# they fit their band. Widths come from the real Inter metrics when
# reportlab is present (Render and the build server both carry it);
# the fallback deliberately overestimates so layout errs roomy.

_FONT_REG = {"done": False, "ok": False}


def _font_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    env = os.environ.get("PROMETHEUS_PDF_FONT_DIR")
    if env:
        cands.append(env)
    cands.append(os.path.join(here, "static", "fonts"))
    cands.append(os.path.join(
        os.path.dirname(here), ".cursor", "skills",
        "crosswalk-brand-standards", "assets", "fonts"))
    for c in cands:
        if c and os.path.isdir(c) and os.path.exists(
                os.path.join(c, "Inter_18pt-Regular.ttf")):
            return c
    return None


def _ensure_fonts():
    if _FONT_REG["done"]:
        return _FONT_REG["ok"]
    _FONT_REG["done"] = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        d = _font_dir()
        if not d:
            return False
        pdfmetrics.registerFont(TTFont(
            "CWDeckR", os.path.join(d, "Inter_18pt-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(
            "CWDeckB", os.path.join(d, "Inter_18pt-Bold.ttf")))
        _FONT_REG["ok"] = True
    except Exception:
        _FONT_REG["ok"] = False
    return _FONT_REG["ok"]


def _str_w_in(text, size, bold=False):
    """Rendered width of one line, in inches."""
    if _ensure_fonts():
        from reportlab.pdfbase import pdfmetrics
        return pdfmetrics.stringWidth(
            str(text), "CWDeckB" if bold else "CWDeckR", size) / 72.0
    k = 0.60 if bold else 0.57
    return len(str(text)) * size * k / 72.0


def _line_count(text, size, width_in, bold=False):
    """Greedy-wrap line count for text in a box width_in wide."""
    total = 0
    for seg in str(text or "").split("\\n"):
        words = seg.split()
        if not words:
            total += 1
            continue
        lines, cur = 1, words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if _str_w_in(trial, size, bold) <= width_in:
                cur = trial
            else:
                lines += 1
                cur = w
        total += lines
    return max(total, 1)


def _block_h(text, size, width_in, bold=False, leading=1.22):
    """Height in inches the wrapped text occupies."""
    return _line_count(text, size, width_in, bold) * size \\
        * leading / 72.0


def _fit_size(text, sizes, width_in, max_lines, bold=True):
    """Largest size from sizes wrapping within max_lines. The caller
    flows below the measured height either way, so even the smallest
    size never overlaps; it just takes more vertical room."""
    for cand in sizes:
        if _line_count(text, cand, width_in, bold) <= max_lines:
            return cand
    return sizes[-1]


def _fit_body(text, size_opts, width_in, max_h):
    """Body copy: step the size down, then trim whole words, so the
    block never runs past max_h. Returns (text, size)."""
    for cand in size_opts:
        if _block_h(text, cand, width_in) <= max_h:
            return str(text or ""), cand
    size = size_opts[-1]
    words = str(text or "").split()
    while len(words) > 1 and _block_h(
            " ".join(words), size, width_in) > max_h:
        words.pop()
    return " ".join(words), size
'''
src = splice(src, old, new, 'measurement layer')

# ---- B. per-slide content_top state --------------------------------
old = '''        self.logo_white = logo_white
        self.logo_black = logo_black
        self.page = 0'''
new = '''        self.logo_white = logo_white
        self.logo_black = logo_black
        self.page = 0
        self.content_top = 2.330'''
src = splice(src, old, new, 'deck state')

old = '''    def new_slide(self, dark=False, fill=None):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        rect(s, 0, 0, SW, SH, fill or (GRAPHITE if dark else OFFWHITE))
        self.page += 1
        return s'''
new = '''    def new_slide(self, dark=False, fill=None):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        rect(s, 0, 0, SW, SH, fill or (GRAPHITE if dark else OFFWHITE))
        self.page += 1
        self.content_top = 2.330
        return s'''
src = splice(src, old, new, 'new_slide reset')

# ---- C. chrome: measured title, flowed sub, computed content top ---
old = '''        txt(s, M, Inches(1.050), BAND, Inches(0.72), title,
            size=34, bold=True, color=ink)
        if sub:
            txt(s, M, Inches(1.846), BAND, Inches(0.40), sub,
                size=16, color=body)'''
new = '''        band_in = float(BAND) / 914400.0
        t_size = _fit_size(str(title or ""), (34, 31, 28, 26),
                           band_in, 2)
        t_h = max(0.50, _block_h(title or "", t_size, band_in,
                                 bold=True))
        txt(s, M, Inches(1.050), BAND, Inches(t_h), title,
            size=t_size, bold=True, color=ink)
        content_top = max(2.330, 1.050 + t_h + 0.30)
        if sub:
            sub_y = max(1.846, 1.050 + t_h + 0.14)
            s_h = max(0.30, _block_h(sub, 16, band_in))
            txt(s, M, Inches(sub_y), BAND, Inches(s_h), sub,
                size=16, color=body)
            content_top = max(2.330, sub_y + s_h + 0.22)
            if _line_count(sub, 16, band_in) >= 2:
                content_top = max(content_top, 2.750)
        self.content_top = content_top'''
src = splice(src, old, new, 'chrome flow')

# ---- D. cover: measured title, flowed intro ------------------------
old = '''    txt(s, M, Inches(1.050), BAND, Inches(1.10),
        sl.get("title") or "", size=34, bold=True, color=WHITE)
    if sl.get("intro"):
        txt(s, M, Inches(2.28), Inches(10.8), Inches(0.70),
            sl["intro"], size=16, color=BODY_DK)'''
new = '''    band_in = float(BAND) / 914400.0
    t_size = _fit_size(str(sl.get("title") or ""), (34, 31, 28),
                       band_in, 3)
    t_h = max(0.55, _block_h(sl.get("title") or "", t_size, band_in,
                             bold=True))
    txt(s, M, Inches(1.050), BAND, Inches(t_h),
        sl.get("title") or "", size=t_size, bold=True, color=WHITE)
    if sl.get("intro"):
        intro_y = max(2.28, 1.050 + t_h + 0.24)
        txt(s, M, Inches(intro_y), Inches(10.8),
            Inches(max(0.40, _block_h(sl["intro"], 16, 10.8))),
            sl["intro"], size=16, color=BODY_DK)'''
src = splice(src, old, new, 'cover flow')

# ---- E. 2x2 cards: flowed head/body, fitted body -------------------
old = '''def _cards_2x2(d, s, cards, dark):
    cw, ch = Inches(5.607), Inches(1.82)
    for i, c in enumerate(cards[:4]):
        x = M + (i % 2) * (cw + GUT)
        y = Inches(2.330) + (i // 2) * (ch + Inches(0.22))
        rrect(s, x, y, cw, ch, SLATE if dark else CARD)
        txt(s, x + Inches(0.26), y + Inches(0.22), Inches(0.7), Inches(0.42),
            f"{i + 1:02d}", size=30, color=SIGNAL if dark else OLIVE,
            light=True)
        txt(s, x + Inches(1.05), y + Inches(0.28), Inches(4.25), Inches(0.36),
            c.get("head") or "", size=14, bold=True,
            color=WHITE if dark else GRAPHITE)
        txt(s, x + Inches(1.05), y + Inches(0.68), Inches(4.25), Inches(0.96),
            c.get("body") or "", size=11.5,
            color=BODY_DK if dark else BODY_LT)'''
new = '''def _cards_2x2(d, s, cards, dark):
    top = d.content_top
    cw, ch_in = Inches(5.607), 1.82
    for i, c in enumerate(cards[:4]):
        x = M + (i % 2) * (cw + GUT)
        y = top + (i // 2) * (ch_in + 0.22)
        rrect(s, x, Inches(y), cw, Inches(ch_in),
              SLATE if dark else CARD)
        txt(s, x + Inches(0.26), Inches(y + 0.22), Inches(0.7),
            Inches(0.42), f"{i + 1:02d}", size=30,
            color=SIGNAL if dark else OLIVE, light=True)
        head_h = max(0.36, _block_h(c.get("head") or "", 14, 4.25,
                                    bold=True))
        txt(s, x + Inches(1.05), Inches(y + 0.28), Inches(4.25),
            Inches(head_h), c.get("head") or "", size=14, bold=True,
            color=WHITE if dark else GRAPHITE)
        body_top = max(0.68, 0.28 + head_h + 0.08)
        body_txt, body_sz = _fit_body(c.get("body") or "",
                                      (11.5, 10.5), 4.25,
                                      ch_in - body_top - 0.12)
        txt(s, x + Inches(1.05), Inches(y + body_top), Inches(4.25),
            Inches(max(0.28, ch_in - body_top - 0.12)), body_txt,
            size=body_sz, color=BODY_DK if dark else BODY_LT)'''
src = splice(src, old, new, 'cards flow')

# ---- F. tiles_facts: top shift, measured fact rows, flowed read ----
old = '''    tiles = _items(sl, "tiles", 4)
    acc = sl.get("accent_index")
    n = max(len(tiles), 1)
    w = Emu(int((int(BAND) - int(GUT) * (n - 1)) / n))
    x = M
    for i, t in enumerate(tiles):
        rrect(s, x, Inches(2.330), w, Inches(1.58), CARD)
        color = OLIVE if (isinstance(acc, int) and i == acc) else GRAPHITE
        txt(s, x + Inches(0.18), Inches(2.48), w - Inches(0.32),
            Inches(0.70), t.get("big") or "", size=30, bold=True,
            color=color)
        txt(s, x + Inches(0.18), Inches(3.22), w - Inches(0.32),
            Inches(0.52), t.get("label") or "", size=11.5, color=BODY_LT)
        x += w + GUT
    y = Inches(4.12)
    for f in _items(sl, "facts", 6):
        txt(s, M, y, Inches(3.40), Inches(0.32),
            f.get("label") or "", size=11.5)
        txt(s, M + Inches(3.50), y, Inches(1.80), Inches(0.32),
            f.get("fig") or "", size=11.5, bold=True, color=OLIVE)
        txt(s, M + Inches(5.50), y, Inches(5.99), Inches(0.32),
            f.get("note") or "", size=11.5, color=BODY_LT)
        y += Inches(0.32)
    if sl.get("read"):
        txt(s, M, Inches(6.30), BAND, Inches(0.52), sl["read"],
            size=11.5, color=BODY_LT)'''
new = '''    top = d.content_top
    tiles = _items(sl, "tiles", 4)
    acc = sl.get("accent_index")
    n = max(len(tiles), 1)
    w = Emu(int((int(BAND) - int(GUT) * (n - 1)) / n))
    x = M
    for i, t in enumerate(tiles):
        rrect(s, x, Inches(top), w, Inches(1.58), CARD)
        color = OLIVE if (isinstance(acc, int) and i == acc) else GRAPHITE
        txt(s, x + Inches(0.18), Inches(top + 0.15), w - Inches(0.32),
            Inches(0.70), t.get("big") or "", size=30, bold=True,
            color=color)
        txt(s, x + Inches(0.18), Inches(top + 0.89), w - Inches(0.32),
            Inches(0.52), t.get("label") or "", size=11.5,
            color=BODY_LT)
        x += w + GUT
    y = top + 1.79
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
src = splice(src, old, new, 'tiles_facts flow')

# ---- G. bars: top shift, measured row steps, flowed read -----------
old = '''    val_accent = ORCHID if dark else AMETHYST
    y = Inches(2.330)
    tw = Inches(6.2) if show_index else Inches(7.4)
    if show_index:
        txt(s, M, y, Inches(2.4), Inches(0.24), "BRAND", size=10.5,
            bold=True, color=muted, spc=100)
        txt(s, M + Inches(9.05), y, Inches(1.1), Inches(0.24), "PEN.",
            size=10.5, bold=True, color=muted, spc=100,
            align=PP_ALIGN.RIGHT)
        txt(s, M + Inches(10.30), y, Inches(1.19), Inches(0.24), "INDEX",
            size=10.5, bold=True, color=muted, spc=100,
            align=PP_ALIGN.RIGHT)
        y = Inches(2.66)
    vmax = max([_num(r.get("value"), 1) for r in rows] or [1.0])
    n = max(len(rows), 1)
    avail = (6.02 if sl.get("read") else 6.60) - float(y) / 914400.0
    step = min(0.52, max(0.36, avail / n))
    for r in rows:
        accent = bool(r.get("accent"))
        txt(s, M, y, Inches(2.70) if not show_index else Inches(2.55),
            Inches(0.28), r.get("label") or "", size=11.5, bold=accent,
            color=ink)
        bx = M + (Inches(2.80) if not show_index else Inches(2.65))
        _bar(s, bx, y + Inches(0.05), tw,
             _num(r.get("value")) / vmax, accent=accent, dark=dark)
        val_c = val_accent if accent else ink
        if show_index:
            txt(s, M + Inches(8.95), y, Inches(1.20), Inches(0.28),
                _fmt_val(r.get("value"), suffix), size=11, bold=True,
                color=val_c, align=PP_ALIGN.RIGHT)
            txt(s, M + Inches(10.30), y, Inches(1.19), Inches(0.28),
                str(r.get("index") or ""), size=11, color=body,
                align=PP_ALIGN.RIGHT)
        else:
            txt(s, bx + tw + Inches(0.12), y, Inches(1.00), Inches(0.28),
                _fmt_val(r.get("value"), suffix), size=11, bold=True,
                color=val_c)
        y += Inches(step)
    if sl.get("read"):
        txt(s, M, Inches(6.02), BAND, Inches(0.62), sl["read"],
            size=11.5, color=body)'''
new = '''    val_accent = ORCHID if dark else AMETHYST
    y = d.content_top
    tw = Inches(6.2) if show_index else Inches(7.4)
    if show_index:
        txt(s, M, Inches(y), Inches(2.4), Inches(0.24), "BRAND",
            size=10.5, bold=True, color=muted, spc=100)
        txt(s, M + Inches(9.05), Inches(y), Inches(1.1), Inches(0.24),
            "PEN.", size=10.5, bold=True, color=muted, spc=100,
            align=PP_ALIGN.RIGHT)
        txt(s, M + Inches(10.30), Inches(y), Inches(1.19),
            Inches(0.24), "INDEX", size=10.5, bold=True, color=muted,
            spc=100, align=PP_ALIGN.RIGHT)
        y += 0.33
    vmax = max([_num(r.get("value"), 1) for r in rows] or [1.0])
    n = max(len(rows), 1)
    lab_w = 2.70 if not show_index else 2.55
    avail = (6.02 if sl.get("read") else 6.60) - y
    step = min(0.52, max(0.30, avail / n))
    for r in rows:
        accent = bool(r.get("accent"))
        row_step = max(step, _line_count(
            r.get("label") or "", 11.5, lab_w,
            bold=accent) * 0.235 + 0.065)
        txt(s, M, Inches(y), Inches(lab_w), Inches(row_step - 0.04),
            r.get("label") or "", size=11.5, bold=accent, color=ink)
        bx = M + (Inches(2.80) if not show_index else Inches(2.65))
        _bar(s, bx, Inches(y + 0.05), tw,
             _num(r.get("value")) / vmax, accent=accent, dark=dark)
        val_c = val_accent if accent else ink
        if show_index:
            txt(s, M + Inches(8.95), Inches(y), Inches(1.20),
                Inches(0.28), _fmt_val(r.get("value"), suffix),
                size=11, bold=True, color=val_c,
                align=PP_ALIGN.RIGHT)
            txt(s, M + Inches(10.30), Inches(y), Inches(1.19),
                Inches(0.28), str(r.get("index") or ""), size=11,
                color=body, align=PP_ALIGN.RIGHT)
        else:
            txt(s, bx + tw + Inches(0.12), Inches(y), Inches(1.00),
                Inches(0.28), _fmt_val(r.get("value"), suffix),
                size=11, bold=True, color=val_c)
        y += row_step
    if sl.get("read"):
        ry = max(6.02, y + 0.06)
        txt(s, M, Inches(ry), BAND, Inches(0.56), sl["read"],
            size=11.5, color=body)'''
src = splice(src, old, new, 'bars flow')

# ---- H. split_stats_bars: top shift, measured rows, flowed read ----
old = '''    cards = _items(sl, "stat_cards", 2)
    if cards:
        _stat_card(s, M, Inches(2.330), cards[0])
    if len(cards) > 1:
        _stat_card(s, M, Inches(4.40), cards[1])
    rx = M + Inches(3.644) + GUT
    rw = Inches(7.569)
    if sl.get("bars_title"):
        txt(s, rx, Inches(2.330), rw, Inches(0.24),
            str(sl["bars_title"]).upper(), size=10.5, bold=True,
            color=MUTED_LT, spc=100)
    rows = _items(sl, "rows", 8)
    suffix = str(sl.get("value_suffix") if sl.get("value_suffix")
                 is not None else "%")
    vmax = max([_num(r.get("value"), 1) for r in rows] or [1.0])
    y = Inches(2.70)
    tw = Inches(4.6)
    n = max(len(rows), 1)
    step = min(0.50, max(0.40, 3.1 / n))
    for r in rows:
        accent = bool(r.get("accent"))
        txt(s, rx, y, Inches(2.05), Inches(0.28), r.get("label") or "",
            size=11.5, bold=accent)
        _bar(s, rx + Inches(2.15), y + Inches(0.05), tw,
             _num(r.get("value")) / vmax, accent=accent)
        txt(s, rx + Inches(2.15) + tw + Inches(0.10), y, Inches(0.80),
            Inches(0.28), _fmt_val(r.get("value"), suffix), size=11,
            bold=True, color=AMETHYST if accent else GRAPHITE)
        y += Inches(step)
    if sl.get("read"):
        txt(s, rx, Inches(5.90), rw, Inches(0.66), sl["read"],
            size=11.5, color=BODY_LT)'''
new = '''    top = d.content_top
    cards = _items(sl, "stat_cards", 2)
    if cards:
        _stat_card(s, M, Inches(top), cards[0])
    if len(cards) > 1:
        _stat_card(s, M, Inches(top + 2.07), cards[1])
    rx = M + Inches(3.644) + GUT
    rw = Inches(7.569)
    if sl.get("bars_title"):
        txt(s, rx, Inches(top), rw, Inches(0.24),
            str(sl["bars_title"]).upper(), size=10.5, bold=True,
            color=MUTED_LT, spc=100)
    rows = _items(sl, "rows", 8)
    suffix = str(sl.get("value_suffix") if sl.get("value_suffix")
                 is not None else "%")
    vmax = max([_num(r.get("value"), 1) for r in rows] or [1.0])
    y = top + 0.37
    tw = Inches(4.6)
    n = max(len(rows), 1)
    step = min(0.50, max(0.36, (5.80 - y) / n))
    for r in rows:
        accent = bool(r.get("accent"))
        row_step = max(step, _line_count(
            r.get("label") or "", 11.5, 2.05,
            bold=accent) * 0.235 + 0.065)
        txt(s, rx, Inches(y), Inches(2.05), Inches(row_step - 0.04),
            r.get("label") or "", size=11.5, bold=accent)
        _bar(s, rx + Inches(2.15), Inches(y + 0.05), tw,
             _num(r.get("value")) / vmax, accent=accent)
        txt(s, rx + Inches(2.15) + tw + Inches(0.10), Inches(y),
            Inches(0.80), Inches(0.28),
            _fmt_val(r.get("value"), suffix), size=11, bold=True,
            color=AMETHYST if accent else GRAPHITE)
        y += row_step
    if sl.get("read"):
        ry = max(5.90, y + 0.08)
        txt(s, rx, Inches(ry), rw, Inches(0.60), sl["read"],
            size=11.5, color=BODY_LT)'''
src = splice(src, old, new, 'split flow')

# ---- I. tiles_row: flowed label/body inside the tile ---------------
old = '''    tiles = _items(sl, "tiles", 3)
    acc = sl.get("accent_index")
    x, w = M, Inches(3.644)
    for i, t in enumerate(tiles):
        rrect(s, x, Inches(2.330), w, Inches(3.30), CARD)
        color = OLIVE if (isinstance(acc, int) and i == acc) else GRAPHITE
        txt(s, x + Inches(0.24), Inches(2.54), w - Inches(0.48),
            Inches(0.70), t.get("big") or "", size=34, bold=True,
            color=color)
        txt(s, x + Inches(0.24), Inches(3.30), w - Inches(0.48),
            Inches(0.72), t.get("label") or "", size=11.5, bold=True)
        txt(s, x + Inches(0.24), Inches(4.10), w - Inches(0.48),
            Inches(1.30), t.get("body") or "", size=11.5, color=BODY_LT)
        x += w + GUT
    if sl.get("read"):
        txt(s, M, Inches(6.10), BAND, Inches(0.40), sl["read"],
            size=11.5, color=BODY_LT)'''
new = '''    top = d.content_top
    tiles = _items(sl, "tiles", 3)
    acc = sl.get("accent_index")
    x, w = M, Inches(3.644)
    w_in = 3.644
    for i, t in enumerate(tiles):
        rrect(s, x, Inches(top), w, Inches(3.30), CARD)
        color = OLIVE if (isinstance(acc, int) and i == acc) else GRAPHITE
        txt(s, x + Inches(0.24), Inches(top + 0.21), w - Inches(0.48),
            Inches(0.70), t.get("big") or "", size=34, bold=True,
            color=color)
        label_h = max(0.30, _block_h(t.get("label") or "", 11.5,
                                     w_in - 0.48, bold=True))
        txt(s, x + Inches(0.24), Inches(top + 0.97), w - Inches(0.48),
            Inches(label_h), t.get("label") or "", size=11.5,
            bold=True)
        body_y = top + 0.97 + label_h + 0.10
        body_txt, body_sz = _fit_body(t.get("body") or "",
                                      (11.5, 10.5), w_in - 0.48,
                                      top + 3.30 - body_y - 0.14)
        txt(s, x + Inches(0.24), Inches(body_y), w - Inches(0.48),
            Inches(max(0.28, top + 3.30 - body_y - 0.14)), body_txt,
            size=body_sz, color=BODY_LT)
        x += w + GUT
    if sl.get("read"):
        ry = max(6.10, top + 3.30 + 0.18)
        txt(s, M, Inches(ry), BAND, Inches(0.40), sl["read"],
            size=11.5, color=BODY_LT)'''
src = splice(src, old, new, 'tiles_row flow')

# ---- J. hero: fitted figure, flowed line and support ---------------
old = '''    ink = WHITE if orchid else SIGNAL
    body = WHITE if orchid else BODY_DK
    support_c = ORCHID_BODY if orchid else BODY_DK
    txt(s, M, Inches(2.50), BAND, Inches(1.10),
        sl.get("big") or "", size=82, bold=True, color=ink)
    if sl.get("line"):
        txt(s, M, Inches(3.72), BAND, Inches(0.70), sl["line"],
            size=16, color=body)
    if sl.get("support"):
        txt(s, M, Inches(4.70), BAND, Inches(1.20), sl["support"],
            size=16, color=support_c)'''
new = '''    ink = WHITE if orchid else SIGNAL
    body = WHITE if orchid else BODY_DK
    support_c = ORCHID_BODY if orchid else BODY_DK
    band_in = float(BAND) / 914400.0
    big = sl.get("big") or ""
    b_size = _fit_size(str(big), (82, 70, 60, 50, 42), band_in, 1)
    b_h = max(1.00, _block_h(big, b_size, band_in, bold=True,
                             leading=1.10))
    by = max(2.50, d.content_top + 0.08)
    txt(s, M, Inches(by), BAND, Inches(b_h), big, size=b_size,
        bold=True, color=ink)
    y = by + b_h + 0.14
    if sl.get("line"):
        ly = max(3.72, y)
        l_h = max(0.40, _block_h(sl["line"], 16, band_in))
        txt(s, M, Inches(ly), BAND, Inches(l_h), sl["line"],
            size=16, color=body)
        y = ly + l_h + 0.26
    if sl.get("support"):
        sy = max(4.70, y)
        txt(s, M, Inches(sy), BAND,
            Inches(max(0.60, _block_h(sl["support"], 16, band_in))),
            sl["support"], size=16, color=support_c)'''
src = splice(src, old, new, 'hero flow')

# ---- K. table: top shift, measured rows, flowed reads --------------
old = '''    y = Inches(2.330)
    for i, c in enumerate(cols):
        txt(s, Inches(xs[i]), y, Inches(first_w if i == 0 else rest_w),
            Inches(0.24), c.upper(), size=10.5, bold=True, color=MUTED_LT,
            spc=100, align=PP_ALIGN.LEFT if i == 0 else PP_ALIGN.RIGHT)
    rect(s, M, Inches(2.58), BAND, Pt(0.75), TRACK)
    acc_row = sl.get("accent_row")
    acc_col = sl.get("accent_col")
    y = Inches(2.72)
    for ri, row in enumerate(rows):
        row_acc = isinstance(acc_row, int) and ri == acc_row
        for ci in range(n):
            cell = str(row[ci]) if ci < len(row) else ""
            cell_acc = row_acc and isinstance(acc_col, int) and ci == acc_col
            txt(s, Inches(xs[ci]), y,
                Inches(first_w if ci == 0 else rest_w), Inches(0.48),
                cell, size=14, bold=(ci == 0 and row_acc) or cell_acc,
                color=OLIVE if cell_acc else GRAPHITE,
                align=PP_ALIGN.LEFT if ci == 0 else PP_ALIGN.RIGHT)
        y += Inches(0.52)
    reads = [t for t in [sl.get("read"), sl.get("read2")] if t]
    if len(reads) == 2:
        rrect(s, M, Inches(5.50), Inches(5.607), Inches(1.10), CARD)
        txt(s, M + Inches(0.24), Inches(5.66), Inches(5.12), Inches(0.86),
            reads[0], size=13, color=BODY_LT)
        rrect(s, M + Inches(5.607) + GUT, Inches(5.50), Inches(5.607),
              Inches(1.10), CARD)
        txt(s, M + Inches(5.887), Inches(5.66), Inches(5.12), Inches(0.86),
            reads[1], size=13, color=BODY_LT)
    elif reads:
        rrect(s, M, Inches(5.50), BAND, Inches(0.92), CARD)
        txt(s, M + Inches(0.24), Inches(5.66), BAND - Inches(0.48),
            Inches(0.64), reads[0], size=14, color=BODY_LT)'''
new = '''    top = d.content_top
    for i, c in enumerate(cols):
        txt(s, Inches(xs[i]), Inches(top),
            Inches(first_w if i == 0 else rest_w),
            Inches(0.24), c.upper(), size=10.5, bold=True,
            color=MUTED_LT, spc=100,
            align=PP_ALIGN.LEFT if i == 0 else PP_ALIGN.RIGHT)
    rect(s, M, Inches(top + 0.25), BAND, Pt(0.75), TRACK)
    acc_row = sl.get("accent_row")
    acc_col = sl.get("accent_col")
    y = top + 0.39
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
        y += row_h
    reads = [t for t in [sl.get("read"), sl.get("read2")] if t]
    ry = max(5.50, y + 0.12)
    if len(reads) == 2:
        rrect(s, M, Inches(ry), Inches(5.607), Inches(1.10), CARD)
        txt(s, M + Inches(0.24), Inches(ry + 0.16), Inches(5.12),
            Inches(0.86), reads[0], size=13, color=BODY_LT)
        rrect(s, M + Inches(5.607) + GUT, Inches(ry), Inches(5.607),
              Inches(1.10), CARD)
        txt(s, M + Inches(5.887), Inches(ry + 0.16), Inches(5.12),
            Inches(0.86), reads[1], size=13, color=BODY_LT)
    elif reads:
        rrect(s, M, Inches(ry), BAND, Inches(0.92), CARD)
        txt(s, M + Inches(0.24), Inches(ry + 0.16), BAND - Inches(0.48),
            Inches(0.64), reads[0], size=14, color=BODY_LT)'''
src = splice(src, old, new, 'table flow')

# ---- L. hero_proof: fitted figure, flowed line, top-shifted proofs -
old = '''    txt(s, M, Inches(2.28), Inches(5.607), Inches(1.20),
        sl.get("big") or "", size=82, bold=True, color=SIGNAL)
    if sl.get("line"):
        txt(s, M, Inches(3.58), Inches(5.607), Inches(0.80), sl["line"],
            size=16, color=BODY_DK)
    rx = M + Inches(5.607) + GUT
    y = Inches(2.330)
    for p in _items(sl, "proofs", 3):
        rrect(s, rx, y, Inches(5.607), Inches(1.18), SLATE)
        txt(s, rx + Inches(0.24), y + Inches(0.16), Inches(5.12),
            Inches(0.44), p.get("fig") or "", size=22, bold=True,
            color=WHITE)
        txt(s, rx + Inches(0.24), y + Inches(0.64), Inches(5.12),
            Inches(0.40), p.get("label") or "", size=11.5, color=BODY_DK)
        y += Inches(1.32)'''
new = '''    top = d.content_top
    big = sl.get("big") or ""
    b_size = _fit_size(str(big), (82, 64, 52, 44), 5.607, 2)
    b_h = max(1.05, _block_h(big, b_size, 5.607, bold=True,
                             leading=1.10))
    by = max(2.28, top - 0.05)
    txt(s, M, Inches(by), Inches(5.607), Inches(b_h), big,
        size=b_size, bold=True, color=SIGNAL)
    if sl.get("line"):
        ly = max(3.58, by + b_h + 0.14)
        txt(s, M, Inches(ly), Inches(5.607),
            Inches(max(0.60, _block_h(sl["line"], 16, 5.607))),
            sl["line"], size=16, color=BODY_DK)
    rx = M + Inches(5.607) + GUT
    y = top
    for p in _items(sl, "proofs", 3):
        rrect(s, rx, Inches(y), Inches(5.607), Inches(1.18), SLATE)
        txt(s, rx + Inches(0.24), Inches(y + 0.16), Inches(5.12),
            Inches(0.44), p.get("fig") or "", size=22, bold=True,
            color=WHITE)
        txt(s, rx + Inches(0.24), Inches(y + 0.64), Inches(5.12),
            Inches(0.40), p.get("label") or "", size=11.5,
            color=BODY_DK)
        y += 1.32'''
src = splice(src, old, new, 'hero_proof flow')

# ---- M. paths: top shift ------------------------------------------
old = '''    y = Inches(2.330)
    for r in _items(sl, "rows", 13):
        lit = bool(r.get("lit"))
        if lit:
            rrect(s, M, y, BAND, Inches(0.245), SLATE, radius=0.06)
        c = SIGNAL if lit else MUTED_DK
        txt(s, M + Inches(0.16), y, Inches(1.1), Inches(0.245),
            str(r.get("kind") or "").upper(), size=8.5, color=c,
            font="Geist Mono", anchor=MSO_ANCHOR.MIDDLE, spc=80)
        txt(s, M + Inches(1.36), y, BAND - Inches(1.52), Inches(0.245),
            r.get("url") or "", size=8.5, color=c, font="Geist Mono",
            anchor=MSO_ANCHOR.MIDDLE)
        y += Inches(0.28)'''
new = '''    y = d.content_top
    for r in _items(sl, "rows", 13):
        lit = bool(r.get("lit"))
        if lit:
            rrect(s, M, Inches(y), BAND, Inches(0.245), SLATE,
                  radius=0.06)
        c = SIGNAL if lit else MUTED_DK
        txt(s, M + Inches(0.16), Inches(y), Inches(1.1),
            Inches(0.245), str(r.get("kind") or "").upper(),
            size=8.5, color=c, font="Geist Mono",
            anchor=MSO_ANCHOR.MIDDLE, spc=80)
        txt(s, M + Inches(1.36), Inches(y), BAND - Inches(1.52),
            Inches(0.245), r.get("url") or "", size=8.5, color=c,
            font="Geist Mono", anchor=MSO_ANCHOR.MIDDLE)
        y += 0.28'''
src = splice(src, old, new, 'paths flow')

ast.parse(src)
DB.write_text(src, encoding='utf-8')
print('deck_builder.py: measure-and-flow applied, ast clean')

# ---- N. plan prompt: header length ceiling -------------------------
pa = PA.read_text(encoding='utf-8')
old = ('Omit slides the data cannot carry (no live events means no '
       'second-screen slide; no avid cut means no avid tier tile). '
       'Never pad: a 14-slide deck that is all signal beats a '
       '20-slide deck with filler.')
new = ('Omit slides the data cannot carry (no live events means no '
       'second-screen slide; no avid cut means no avid tier tile). '
       'Never pad: a 14-slide deck that is all signal beats a '
       '20-slide deck with filler.\n\nHEADLINES FIT THE PAGE\n'
       '- Every "title" is ONE sentence of 12 words or fewer, plain '
       'words, full stop. The cover title included: one clause, one '
       'idea. Two figures in one title is one too many; move the '
       'second figure to a stat or the intro.\n'
       '- "sub" and "intro" stay under 30 words. Card "head" under '
       '8 words; card "body" under 28 words; tile "label" under 10 '
       'words; bar row "label" under 4 words; "big" is a figure, '
       'never a sentence.')
n = pa.count(old)
if n != 1:
    raise RuntimeError(f'[prompt ceiling] anchor count {n}')
pa = pa.replace(old, new)
ast.parse(pa)
PA.write_text(pa, encoding='utf-8')
print('prometheus_analysis.py: headline ceiling added, ast clean')
