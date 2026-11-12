"""
Brand Partnership IQ - Valuation Deck Builder
=============================================

Generates the valuation deck (.pptx) from a single BPIQ result payload.

The deck is a paid delivery, so it leaves this module ready to send
(2026-11-11, Jenna: "these errors cannot happen. fix and ensure itll be
correct going forward"). What that means here:

  - Every page is drawn on the crosswalk-design grid with the pack's
    chrome: the eyebrow and its dot, the real logo, a headline set as a
    bold claim with a light close, the source line, the footer lockup
    and the page number as a field. Corners are rounded, type comes from
    the pack's scale, and the deck carries a pale Signal page and a pale
    Orchid page.
  - The short version is the second page: what we found, where the value
    sits, what to do.
  - Every page total is the sum of the parts printed on that page. Every
    channel that was priced is in the earned-media table, and the table
    sums to the earned media value. Valuation dollars print exact, with
    cents.
  - No test statistic (no z, no interval, no p-value, no significance
    note), no outside name behind a rate, no word for how a figure was
    made, no dashboard note. With a matched group bought, the lift is a
    figure with a plain comparison. Without one, no page mentions one.
  - A page the campaign created has no visits before it existed.
  - The cover is the pairing in the product's own order and a plain
    claim line. DRAFT prints only when the valuation says it is one
    (`is_draft`).

Usage:

    from migration.bpiq_deck_builder import build_deck
    pptx_bytes = build_deck(data, image_url=..., category=...)

The public entry points are `build_deck(...)` (bytes) and
`build_deck_report(...)` (bytes and the finisher's report). The page
builders are private helpers and take any BPIQ result dict (the shape
served by /api/brand-partnership-iq/results/...): a missing, null or
non-numeric field never raises.

Not yet done: the pages are drawn here on the pack's grid rather than
filled into the 27-layout template file (Crosswalk_Deck_Templates.pptx).
The valuation's tables and calculators have row counts that change run
to run and no template layout carries them. See the plan in the commit
that introduced this note.
"""
from __future__ import annotations

import io
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional

import requests
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt


# ─────────────────────────────────────────────────────────────────────
#  Palette - the ten colors of the crosswalk-design pack and nothing
#  else (assets/tokens.json). Signal Green is the accent on dark and
#  Signal Olive its twin on light; Orchid and Amethyst mark a value that
#  fell. The older aliases (BG_DARK, FG_LIGHT ...) point at the same
#  colors.
# ─────────────────────────────────────────────────────────────────────
GRAPHITE = RGBColor(0x0C, 0x16, 0x18)    # Graphite Teal: the dark page
OFFWHITE = RGBColor(0xE9, 0xE8, 0xE1)    # Neutral Off-White: the light page
SIGNAL   = RGBColor(0xC7, 0xF2, 0x3E)    # Signal Green: the accent on dark
OLIVE    = RGBColor(0x5E, 0x7E, 0x12)    # Signal Olive: the accent on light
ORCHID   = RGBColor(0xE6, 0x82, 0xFF)    # the second voice on dark
AMETHYST = RGBColor(0x8E, 0x3F, 0xA8)    # and on light

BG_DARK   = GRAPHITE
BG_CREAM  = OFFWHITE
FG_LIGHT  = OFFWHITE
FG_DARK   = GRAPHITE
ACCENT    = SIGNAL
MUTED_LT  = RGBColor(0x5C, 0x65, 0x60)   # body text on Off-White
MUTED_DK  = RGBColor(0x9A, 0xA0, 0x9B)   # body text on Graphite

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

# Font: the house face, Crosswalk Inter, from the crosswalk-design pack
# (2026-10-21, Jenna: the design skill is always used). Helvetica Neue
# and Arial are retired faces in an Office file. The finish step at the
# end of build_deck embeds the family so a reader without the font still
# sees the deck as built.
def _design():
    try:
        from migration import design_skill as _ds
        return _ds
    except Exception:
        return None


def _house_face(weight: str = "Regular") -> str:
    ds = _design()
    try:
        return ds.family_name(weight) if ds is not None else "Crosswalk Inter"
    except Exception:
        return "Crosswalk Inter"


FONT_DISPLAY = _house_face("Regular")
FONT_BODY    = _house_face("Regular")
FONT_NUMERIC = _house_face("Regular")


# ─────────────────────────────────────────────────────────────────────
#  Number / money formatting helpers
# ─────────────────────────────────────────────────────────────────────
def fmt_money(n: Any) -> str:
    """Exact dollars with cents, on every figure: $12,030.00, never $12K,
    $12.0K or $1.2M (Jenna 2026-09-23: 'dont round on that page when
    making decks'). Until 2026-11-11 cents printed only when they were
    not zero, so one page read $604,875 beside $683,197.50."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        n = 0.0
    return f"-${abs(n):,.2f}" if n < 0 else f"${n:,.2f}"


def fmt_num(n: Any) -> str:
    """Exact comma-separated counts, never 19K / 2.4M abbreviations
    (same 2026-09-23 mandate). Large values auto-shrink at the render
    site via _stat_font instead of abbreviating here."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{int(round(n)):,}"


def _num(n: Any, default: float = 0.0) -> float:
    """A run document's number as a float: None, '', a non-numeric
    string, a list or a dict all read as `default`. The context derive
    runs before the per-slide safety net, so a run shape the builder has
    not seen (an in-place rerun, a field the research left as text)
    must never raise out of it (2026-10-29)."""
    if isinstance(n, bool):
        return float(n)
    if isinstance(n, (int, float)):
        return float(n)
    try:
        return float(str(n).replace(",", "").replace("$", "").replace("%", "").strip() or default)
    except (TypeError, ValueError):
        return float(default)


def _count(n: Any, default: int = 0) -> int:
    """`_num` rounded to a whole count."""
    try:
        return int(round(_num(n, default)))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def fmt_pct(n: Any, decimals: int = 1) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0%"
    return f"{n:.{decimals}f}%"


def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "BPIQ_Deck"))[:120]


# ─────────────────────────────────────────────────────────────────────
#  Layout primitives - thin wrappers around python-pptx so the slide
#  builders read closer to a layout-DSL than raw XML wrangling.
# ─────────────────────────────────────────────────────────────────────
def _add_bg(slide, color: RGBColor):
    """The page color, set as the slide background (2026-10-21, the
    crosswalk-design pack: never a full-page shape acting as one)."""
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color
    return None


def _line_count(text: str, size_pt: float, width_in: float, *, bold: bool = False) -> int:
    """How many lines a run wraps to at this width, by the design pack's
    own wrap model (falls back to a width estimate when the pack is absent)."""
    pack = _design()
    mod = pack.lint_module() if pack is not None else None
    words = str(text or "").split()
    if not words:
        return 1
    if mod is not None:
        try:
            wt = mod._weight_key(FONT_DISPLAY, bold)
            chars = [(ch, wt, float(size_pt), 0.0) for ch in str(text)]
            return max(1, max(len(mod._wrap(chars, width_in * 72 * k)) for k in mod.WRAP_SCALES))
        except Exception:
            pass
    est = len(str(text)) * size_pt * (0.56 if bold else 0.52) / 72
    return max(1, int(est // width_in) + 1)


def _fit_lines(text: str, sizes, width_in: float, *, bold: bool = False, max_lines: int = 1) -> float:
    """The largest size in `sizes` at which `text` fits in `max_lines`."""
    for sz in sizes:
        if _line_count(text, sz, width_in, bold=bold) <= max_lines:
            return sz
    return sizes[-1]


# ─────────────────────────────────────────────────────────────────────
#  Measure and fit (2026-10-29)
#
#  The Jennifer Beals x Lululemon run put five collisions on the deck
#  the dashboard exports: a three-line cover subtitle under the DRAFT
#  stamp, a 14-character total wrapping inside its tile, a 13-platform
#  rate-sources note on top of the note below it, an eight-row AGE
#  column on the source line, two four-line methodology paragraphs on
#  each other. Every one was a block placed at a fixed height for text
#  whose length changes run to run.
#
#  So every block is now measured with the design pack's own wrap model
#  before it is placed, and the frame takes the height the text needs
#  (`_add_text`). Where the room is bounded, the text fits itself to it
#  (`_fit_body`): it steps down inside the pack's type scale (body never
#  below 10pt, notes never below 9pt), then tightens its leading, then
#  drops trailing sentences; a stat picks the largest size that holds
#  one line (`_fit_stat`); a card grows with its content up to its room
#  (`_callout`); stacked rows share a vertical budget (`_fit_stack`).
#  `check_layout` is the self-check on the finished presentation: the
#  rendered extent of no two text frames may intersect and no frame may
#  hold less than its text. It prints what it finds and never holds a
#  deck (no-rebuild-level-correction).
# ─────────────────────────────────────────────────────────────────────
LINE_EM = 1.21            # Crosswalk Inter: ascender plus descender, in em
ASC_EM = 0.97             # ascender, in em (the line's top to the baseline)
CAP_EM = 0.73             # cap height, in em (where the ink of a line starts)
DESC_EM = 0.24            # descender depth, in em
BODY_FLOOR = 10           # the pack: body copy is never below 10pt
NOTE_FLOOR = 9            # footnotes and method notes run at 9pt
CONTENT_BOTTOM = 6.32     # content stops 0.30in above the source line at 6.62in


@dataclass
class TextMetrics:
    lines: int
    max_w_in: float
    height_in: float


def _line_h_in(size_pt: float, spacing: float = 1.0) -> float:
    """One line's pitch in inches at this size and proportional spacing."""
    return float(size_pt) * LINE_EM * float(spacing) / 72.0


def _paragraphs_for(text: Any, size: float) -> list:
    """The paragraphs `_add_text` writes for this text: one per newline,
    or one joined paragraph for a headline (26pt and up)."""
    text = str(text if text is not None else "")
    if size >= 26:
        return [" ".join(part.strip() for part in text.split("\n") if part.strip())]
    return text.split("\n")


def _wrap_widths(text: str, size_pt: float, width_in: float, *, bold: bool = False) -> list:
    """The width in inches of each wrapped line of ONE paragraph, by the
    pack's wrap model at the scale that breaks the most lines (renderers
    disagree by a percent or two; the tightest read is the safe one).
    A width estimate stands in when the pack is absent."""
    words = str(text or "").split()
    if not words:
        return [0.0]
    pack = _design()
    mod = pack.lint_module() if pack is not None else None
    if mod is not None:
        try:
            wt = mod._weight_key(FONT_DISPLAY, bold)
            chars = [(ch, wt, float(size_pt), 0.0) for ch in str(text)]
            best = None
            for k in mod.WRAP_SCALES:
                lines = mod._wrap(chars, width_in * 72 * k)
                if best is None or len(lines) > len(best):
                    best = lines
            return [w / 72.0 for _words, w in best] or [0.0]
        except Exception:
            pass
    em = 0.56 if bold else 0.52
    widths, cur = [], 0.0
    space = 0.27 * size_pt / 72
    for w in words:
        ww = len(w) * em * size_pt / 72
        if cur and cur + space + ww > width_in:
            widths.append(cur)
            cur = ww
        else:
            cur = cur + space + ww if cur else ww
    widths.append(cur)
    return widths


def _measure(text: Any, size_pt: float, width_in: float, *, bold: bool = False,
             spacing: float = 1.05) -> TextMetrics:
    """How the paragraphs `_add_text` writes for `text` render in a box
    `width_in` wide: line count, widest line, and the height they need."""
    lines, max_w = 0, 0.0
    for para in _paragraphs_for(text, size_pt):
        if not para.strip():
            lines += 1
            continue
        widths = _wrap_widths(para, size_pt, width_in, bold=bold)
        lines += len(widths)
        max_w = max(max_w, max(widths))
    lines = max(1, lines)
    return TextMetrics(lines, max_w, lines * _line_h_in(size_pt, spacing))


def _box_width_for(text: Any, size: float, width_in: float, *, bold: bool = False,
                   font: Optional[str] = None, letter_spacing: float = 0.0) -> float:
    """The width `_add_text` gives this block: the asked width, narrowed
    (never below 80 percent) when the pack's wrap model would otherwise
    leave a lone word on the last line of a body sentence."""
    paragraphs = _paragraphs_for(text, size)
    if size >= 10 and len(paragraphs) == 1 and len(paragraphs[0].split()) >= 4:
        pack = _design()
        if pack is not None:
            try:
                safe = pack.widow_safe_width(paragraphs[0], size, width_in, bold=bold,
                                             face=font or FONT_BODY, tracking_pt=letter_spacing)
                if safe and safe < width_in:
                    return float(safe)
            except Exception:
                pass
    return float(width_in)


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _sentences(text: str) -> list:
    parts = [p for p in _SENTENCE_BREAK.split(str(text or "").strip()) if p]
    return parts or [str(text or "")]


def _shorter(text: str) -> Optional[str]:
    """The text one unit shorter: without its last sentence; a one-sentence
    list without its last clause (after a semicolon). None when the text
    is down to one sentence with nothing to drop. A sentence is never cut
    inside (until 2026-11-11 the last words came off and a period went
    on: 'A look at the value she brought to The.')."""
    text = str(text or "").strip()
    sentences = _sentences(text)
    if len(sentences) > 1:
        return " ".join(sentences[:-1]).strip()
    if text.count(";") >= 1:
        head = text.rsplit(";", 1)[0].rstrip(" ,;")
        return head + "." if head and not head.endswith((".", "!", "?")) else head or None
    return None


def _fit_candidates(text: Any, size: float, *, floor: float, spacing: float, min_spacing: float):
    """(size, spacing, text) in the order a fit tries them: the whole text
    stepping down a point at a time to the floor, then the same at the
    tighter leading, then the text a unit shorter, again and again."""
    sizes = list(range(int(round(size)), int(floor) - 1, -1)) or [int(floor)]
    spacings = [spacing] + ([min_spacing] if min_spacing < spacing else [])
    t = str(text if text is not None else "")
    while t:
        for sp in spacings:
            for sz in sizes:
                yield sz, sp, t
        t = _shorter(t)


def _fit_body(text: Any, width_in: float, max_h_in: float, *, size: float, floor: float = BODY_FLOOR,
              spacing: float = 1.05, min_spacing: Optional[float] = None, bold: bool = False,
              font: Optional[str] = None, letter_spacing: float = 0.0):
    """(size, spacing, text, TextMetrics) for the fullest rendering of
    `text` that holds inside `max_h_in` at `width_in`, measured at the
    width `_add_text` will give it. When nothing fits, the smallest and
    shortest candidate comes back and `check_layout` reports the rest."""
    min_spacing = spacing if min_spacing is None else min_spacing
    last = None
    for sz, sp, t in _fit_candidates(text, size, floor=floor, spacing=spacing, min_spacing=min_spacing):
        w = _box_width_for(t, sz, width_in, bold=bold, font=font, letter_spacing=letter_spacing)
        m = _measure(t, sz, w, bold=bold, spacing=sp)
        last = (sz, sp, t, m)
        if m.height_in <= max_h_in + 1e-6:
            return last
    return last


def _add_body(slide, left, top, width, text: Any, *, max_height, size: float,
              floor: float = BODY_FLOOR, spacing: float = 1.05, min_spacing: Optional[float] = None,
              bottom=None, **style):
    """A body block that fits the room it is given. `max_height` bounds
    the text (see `_fit_body`); the frame takes the fitted height. With
    `bottom` the frame is placed so its lower edge lands there (`top`
    is then the ceiling the block may rise to). Returns (shape, height)."""
    width_in = Emu(width).inches
    max_h_in = Emu(max_height).inches
    if bottom is not None:
        max_h_in = min(max_h_in, Emu(bottom).inches - Emu(top).inches)
    sz, sp, t, m = _fit_body(
        text, width_in, max_h_in, size=size, floor=floor, spacing=spacing, min_spacing=min_spacing,
        bold=bool(style.get("bold", False)), font=style.get("font"),
        letter_spacing=float(style.get("letter_spacing", 0.0) or 0.0))
    h = Inches(round(m.height_in + 0.02, 3))
    y = top if bottom is None else Emu(bottom) - h
    shape = _add_text(slide, left, y, width, h, t, size=sz, spacing=sp, **style)
    return shape, h


def _fit_stat(text: Any, width_in: float, base: int, *, floor: Optional[int] = None,
              bold: bool = True) -> int:
    """The largest size from `base` down at which a stat ('$33,409,966.88',
    '3,593,822', '01 Oct 2026 to 29 Oct 2026') holds one line in `width_in`."""
    base = int(base)
    floor = int(floor if floor is not None else max(12, base // 3))
    step = 8 if base > 80 else 4 if base > 40 else 2
    sizes = list(range(base, floor, -step)) + [floor]
    # a stat is often one unbreakable token, so the test is its measured
    # width (with a 3 percent margin for the renderer), not a line count
    for sz in sizes:
        m = _measure(str(text), sz, width_in, bold=bold)
        if m.lines == 1 and m.max_w_in <= width_in * 0.97:
            return sz
    return sizes[-1]


def _break_long_token(text: Any, width_in: float, size: float, *, bold: bool = False,
                      max_lines: int = 2) -> str:
    """A name wider than its column with no space to wrap at (a URL path,
    a hyphenated slug) is broken at its own separators into at most
    `max_lines` lines that each fit, the remainder trimmed with an
    ellipsis. Text that already holds within `max_lines` lines comes
    back as is (with `max_lines=1` a label that wraps is trimmed to the
    one line its row has)."""
    text = str(text or "")
    width_fit = width_in * 0.97

    def fits(t: str) -> bool:
        m = _measure(t, size, width_in, bold=bold)
        return m.lines == 1 and m.max_w_in <= width_fit

    whole = _measure(text, size, width_in, bold=bold)
    if whole.lines <= max_lines and whole.max_w_in <= width_fit:
        return text
    seps = "/-_.:?&= "
    lines, rest = [], text
    while rest and len(lines) < max_lines:
        if fits(rest):
            lines.append(rest)
            rest = ""
            break
        cut = None
        for i in range(len(rest) - 1, 0, -1):
            if rest[i - 1] in seps and fits(rest[:i]):
                cut = i
                break
        if cut is None:
            for i in range(len(rest), 0, -1):
                if fits(rest[:i]):
                    cut = i
                    break
        cut = cut or 1
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest and lines:
        last = lines[-1]
        while last and not fits(last + "..."):
            last = last[:-1]
        lines[-1] = last.rstrip(" /-_.:?&=") + "..."
    return "\n".join(lines)


def _fit_stack(blocks: list, avail_in: float, gap_in: float, pad_in: float = 0.02) -> list:
    """Fit stacked text blocks into a shared vertical budget. Each block
    is a dict with text, width_in, size and optionally floor, spacing,
    min_spacing, bold, font. Returns one (size, spacing, text, height_in)
    per block (`pad_in` is the room each frame adds under its text and
    is counted in the budget). The whole stack steps down together:
    leading first, then size to each block's floor, then the tallest
    block loses a sentence at a time; the stack keeps its order and
    every block keeps at least its first words."""
    def spec(b):
        return dict(size=float(b["size"]), floor=float(b.get("floor", BODY_FLOOR)),
                    spacing=float(b.get("spacing", 1.05)),
                    min_spacing=float(b.get("min_spacing", b.get("spacing", 1.05))),
                    bold=bool(b.get("bold", False)), font=b.get("font"))

    def measure(b, sz, sp, t):
        s = spec(b)
        w = _box_width_for(t, sz, float(b["width_in"]), bold=s["bold"], font=s["font"])
        return _measure(t, sz, w, bold=s["bold"], spacing=sp).height_in

    state = [[spec(b)["size"], spec(b)["spacing"], str(b["text"] or "")] for b in blocks]

    def total():
        return (sum(measure(b, *st) + pad_in for b, st in zip(blocks, state))
                + gap_in * max(0, len(blocks) - 1))

    if total() > avail_in:
        for b, st in zip(blocks, state):
            st[1] = spec(b)["min_spacing"]
    while total() > avail_in:
        moved = False
        for b, st in zip(blocks, state):
            if st[0] - 1 >= spec(b)["floor"]:
                st[0] -= 1
                moved = True
        if not moved:
            break
    while total() > avail_in:
        tallest = max(range(len(blocks)), key=lambda i: measure(blocks[i], *state[i]))
        shorter = _shorter(state[tallest][2])
        if not shorter:
            break
        state[tallest][2] = shorter
    return [(int(st[0]), st[1], st[2], measure(b, *st)) for b, st in zip(blocks, state)]


def _para_widths(p, size: float, inner_w: float) -> Optional[list]:
    """The wrapped line widths (inches) of a paragraph set in more than
    one weight, each run measured at its own weight, at the renderer
    scale that breaks the most lines. None for a single-weight paragraph
    (the plain measure covers it) or when the pack is absent."""
    runs = list(p.runs)
    if len(runs) < 2:
        return None
    pack = _design()
    mod = pack.lint_module() if pack is not None else None
    if mod is None:
        return None
    try:
        keys = [mod._weight_key(r.font.name, bool(r.font.bold)) for r in runs]
        if len(set(keys)) < 2:
            return None
        chars = []
        for r, wt in zip(runs, keys):
            sz = float(r.font.size.pt) if r.font.size is not None else float(size)
            chars.extend((ch, wt, sz, 0.0) for ch in r.text)
        best = None
        for k in mod.WRAP_SCALES:
            lines = mod._wrap(chars, inner_w * 72 * k)
            if best is None or len(lines) > len(best):
                best = lines
        return [w / 72.0 for _words, w in best] or None
    except Exception:
        return None


def _frame_ink(shape):
    """The rendered extent of a text frame in inches, (x0, y0, x1, y1),
    with the text height and the box height. The box may be generous;
    the ink is where the glyphs land: the frame's anchor places the
    lines, the paragraph alignment places them across, the first line's
    cap height and the last line's descender trim the ends. None for a
    frame with no text."""
    tf = shape.text_frame
    paras = list(tf.paragraphs)
    if not "".join(p.text for p in paras).strip():
        return None
    left, top = Emu(shape.left).inches, Emu(shape.top).inches
    w, h = Emu(shape.width).inches, Emu(shape.height).inches
    lins, rins = Emu(tf.margin_left).inches, Emu(tf.margin_right).inches
    tins, bins = Emu(tf.margin_top).inches, Emu(tf.margin_bottom).inches
    inner_w = max(0.1, w - lins - rins)
    total_h, max_w, align = 0.0, 0.0, None
    first_size, first_sp, last_size, last_text = None, 1.0, 10.0, ""
    for p in paras:
        run = p.runs[0] if p.runs else None
        size = float(run.font.size.pt) if run is not None and run.font.size is not None else 18.0
        if run is None:
            # a field (the page number) carries its size on the field, not on a run
            for fld in p._p.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}fld"):
                rpr = fld.find("{http://schemas.openxmlformats.org/drawingml/2006/main}rPr")
                if rpr is not None and rpr.get("sz"):
                    size = int(rpr.get("sz")) / 100.0
        bold = bool(run.font.bold) if run is not None else False
        ls = p.line_spacing
        sp = float(ls) if isinstance(ls, (int, float)) else (float(ls.pt) / size if ls is not None else 1.0)
        if align is None and p.alignment is not None:
            align = p.alignment
        if first_size is None:
            first_size, first_sp = size, sp
        last_size, last_text = size, p.text
        if not p.text.strip():
            total_h += _line_h_in(size, sp)
            continue
        widths = _para_widths(p, size, inner_w) or _wrap_widths(p.text, size, inner_w, bold=bold)
        total_h += len(widths) * _line_h_in(size, sp)
        max_w = max(max_w, max(widths))
    if tf.vertical_anchor == MSO_ANCHOR.BOTTOM:
        y0 = top + h - bins - total_h
    elif tf.vertical_anchor == MSO_ANCHOR.MIDDLE:
        y0 = top + (h - total_h) / 2
    else:
        y0 = top + tins
    if align == PP_ALIGN.RIGHT:
        x0 = left + w - rins - max_w
    elif align == PP_ALIGN.CENTER:
        x0 = left + (w - max_w) / 2
    else:
        x0 = left + lins
    # proportional leading above 100 percent is added above the line, so
    # the first cap sits (spacing - 1) line heights plus the ascender-to-cap
    # gap below the frame's text top
    cap_top = y0 + max(0.0, (first_sp - 1.0) * LINE_EM + (ASC_EM - CAP_EM)) * (first_size or 10.0) / 72
    has_desc = any(ch in "gjpqy,;()[]{}@$" for ch in last_text)
    ink_bottom = y0 + total_h - (0.0 if has_desc else DESC_EM * last_size / 72)
    return (x0, cap_top, x0 + max_w, max(cap_top, ink_bottom), total_h, h)


def check_layout(prs, *, tol: float = 0.03) -> list:
    """The layout self-check on a finished presentation. Reports, per
    slide: a text frame whose text needs more height than its box, text
    that runs off the page, and any two text frames whose rendered
    extents intersect (by more than `tol` inches each way). Frames are
    compared on where their text lands, not on their boxes, since a box
    may be generous while its text is short. Empty frames are skipped."""
    issues = []
    page_w, page_h = Emu(prs.slide_width).inches, Emu(prs.slide_height).inches
    for si, slide in enumerate(prs.slides, start=1):
        inks = []
        for shp in slide.shapes:
            if not getattr(shp, "has_text_frame", False) or not shp.has_text_frame:
                continue
            ink = _frame_ink(shp)
            if ink is None:
                continue
            x0, y0, x1, y1, text_h, box_h = ink
            label = " ".join(shp.text_frame.text.split())[:48]
            if text_h > box_h + tol:
                issues.append(f"slide {si}: text needs {text_h - box_h:.2f}in more than its box: {label!r}")
            if y1 > page_h + tol or x1 > page_w + tol or y0 < -tol or x0 < -tol:
                issues.append(f"slide {si}: text runs off the page: {label!r}")
            inks.append(((x0, y0, x1, y1), label))
        for i in range(len(inks)):
            (ax0, ay0, ax1, ay1), al = inks[i]
            for j in range(i + 1, len(inks)):
                (bx0, by0, bx1, by1), bl = inks[j]
                ox = min(ax1, bx1) - max(ax0, bx0)
                oy = min(ay1, by1) - max(ay0, by0)
                if ox > tol and oy > tol:
                    issues.append(f"slide {si}: text collides ({ox:.2f} x {oy:.2f}in): {al!r} with {bl!r}")
    return issues


def _weight_face(weight: Optional[str], bold: bool) -> tuple:
    """(family name, bold flag) for a weight. Bold is a style of the
    Regular family; every other weight is its own family (the design
    pack: never switch bold on over a weight family)."""
    w = str(weight or ("Bold" if bold else "Regular"))
    if w == "Bold":
        return _house_face("Regular"), True
    if w == "Regular":
        return _house_face("Regular"), False
    return _house_face(w), False


def _heavy(weight: Optional[str], bold: bool) -> bool:
    """Measure this run with the bold metrics (the safe, wider read)."""
    return bool(bold) or str(weight or "") in ("Bold", "SemiBold", "ExtraBold", "Black")


def _set_tracking(run, pt: float) -> None:
    """Letter spacing on a run, in points (the eyebrow runs at +2.6, the
    footer at +2.0, a caps label at +1.0)."""
    if pt:
        try:
            run._r.get_or_add_rPr().set("spc", str(int(round(float(pt) * 100))))
        except Exception:
            pass


def _add_text(
    slide,
    left, top, width, height,
    text: str,
    *,
    size: float = 18,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor = FG_DARK,
    font: Optional[str] = None,
    weight: Optional[str] = None,
    align: str = "left",
    anchor: str = "top",
    spacing: float = 1.05,
    letter_spacing: float = 0.0,
):
    """Add a text box and return the shape. `text` may contain \\n
    for multi-paragraph blocks; each paragraph inherits the same run
    style. `weight` names a face of the house family ('Light',
    'SemiBold', 'Bold'); `letter_spacing` is tracking in points."""
    face, is_bold = _weight_face(weight, bold)
    if font and not weight:
        face = font
    heavy = _heavy(weight, bold)
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    tf.vertical_anchor = {
        "top":    MSO_ANCHOR.TOP,
        "middle": MSO_ANCHOR.MIDDLE,
        "bottom": MSO_ANCHOR.BOTTOM,
    }.get(anchor, MSO_ANCHOR.TOP)
    align_enum = {
        "left":   PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right":  PP_ALIGN.RIGHT,
    }.get(align, PP_ALIGN.LEFT)
    text = str(text)
    try:
        from migration.design_skill import scrub_door_metaphor
        text = scrub_door_metaphor(text)
    except Exception:
        text = text
    if 0 < size < 9:
        size = 9          # the design pack's floor: notes run at 9pt, body at 10pt
    if size >= 26:
        # a headline is one paragraph that wraps on its own (the design
        # pack fails a sentence split across paragraphs by hand)
        text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    paragraphs = text.split("\n")
    # a sentence never ends on a lone word: the box is narrowed (never
    # widened) until the pack's own wrap model puts two words on the
    # last line. Dates and figures change per deck, so this is settled
    # here, not in the copy. The edge the text is set against stays put.
    safe_w = _box_width_for(text, size, Emu(width).inches, bold=heavy, font=face,
                            letter_spacing=letter_spacing)
    if safe_w < Emu(width).inches:
        gap = Emu(width) - Inches(safe_w)
        if align == "right":
            box.left = Emu(box.left + gap)
        elif align == "center":
            box.left = Emu(box.left + gap // 2)
        box.width = Inches(safe_w)
    # the frame takes the height its text needs (2026-10-29): measured
    # with the pack's wrap model at the final width, the box grows (never
    # shrinks) to hold every line, keeping its anchor edge where it was,
    # so a frame never claims less room than its text and check_layout
    # can tell what the text would run into
    try:
        need = Inches(round(_measure(text, size, Emu(box.width).inches, bold=heavy,
                                     spacing=spacing).height_in + 0.02, 3))
        if need > box.height:
            if anchor == "bottom":
                box.top = Emu(box.top + box.height - need)
            elif anchor == "middle":
                box.top = Emu(box.top - (need - box.height) // 2)
            box.height = need
    except Exception:
        pass
    for i, para_text in enumerate(paragraphs):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.alignment = align_enum
        p.line_spacing = spacing
        r = p.add_run()
        r.text = para_text
        r.font.name = face
        r.font.size = Pt(size)
        r.font.bold = is_bold
        # the house family ships upright faces only; italics never render
        # as Crosswalk Inter, so an italic request is set upright
        r.font.italic = False
        r.font.color.rgb = color
        _set_tracking(r, letter_spacing)
    return box


def _runs_lines(runs, size: float, width_in: float) -> Optional[list]:
    """How a paragraph set in more than one weight wraps at `width_in`,
    by the pack's wrap model with each run measured at its own weight:
    one list of lines per renderer scale. None when the pack is absent."""
    pack = _design()
    mod = pack.lint_module() if pack is not None else None
    if mod is None:
        return None
    try:
        chars = []
        for text, weight, _color in runs:
            face, is_bold = _weight_face(weight, False)
            wt = mod._weight_key(face, is_bold)
            chars.extend((ch, wt, float(size), 0.0) for ch in str(text))
        return [mod._wrap(chars, width_in * 72 * k) for k in mod.WRAP_SCALES]
    except Exception:
        return None


def _runs_fit(runs, size: float, width_in: float) -> tuple:
    """(box width, line count) for a mixed-weight paragraph: the asked
    width, narrowed in 0.05in steps (never below 70 percent) until no
    renderer scale leaves one word alone on the last line. A headline
    that ends on a long figure ('... is worth $421,610.00.') needs the
    deeper step for the word before the figure to come down with it."""
    def read(w):
        got = _runs_lines(runs, size, w)
        if got is None:
            return None, False
        lines = max(len(ls) for ls in got)
        lone = any(len(ls) >= 2 and len(ls[-1][0]) == 1 for ls in got)
        return lines, lone

    lines, lone = read(width_in)
    if lines is None:
        joined = "".join(str(t) for t, _w, _c in runs)
        w = _box_width_for(joined, size, width_in, bold=True, font=FONT_DISPLAY)
        return w, _measure(joined, size, w, bold=True).lines
    if not lone:
        return width_in, lines
    w = width_in
    while w - 0.05 >= width_in * 0.7:
        w = round(w - 0.05, 3)
        n, still = read(w)
        if not still and n <= max(lines, 2):
            return w, n
    return width_in, lines


def _add_runs(slide, left, top, width, runs, *, size: float, spacing: float = 0.95,
              anchor: str = "top", align: str = "left"):
    """One paragraph set in more than one weight: a headline's bold claim
    and its light context. `runs` is [(text, weight, color)]. Returns
    (shape, line count, text height in inches)."""
    joined = "".join(str(t) for t, _w, _c in runs)
    try:
        from migration.design_skill import scrub_door_metaphor
        if scrub_door_metaphor(joined) != joined:
            runs = [(scrub_door_metaphor(str(t)), w, c) for t, w, c in runs]
            joined = "".join(str(t) for t, _w, _c in runs)
    except Exception:
        pass
    width_in = Emu(width).inches
    safe_w, n_lines = _runs_fit(runs, size, width_in)
    m = TextMetrics(n_lines, safe_w, n_lines * _line_h_in(size, spacing))
    box = slide.shapes.add_textbox(left, top, Inches(safe_w), Inches(round(m.height_in + 0.02, 3)))
    if safe_w < width_in and align == "right":
        box.left = Emu(box.left + (Emu(width) - Inches(safe_w)))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                          "bottom": MSO_ANCHOR.BOTTOM}.get(anchor, MSO_ANCHOR.TOP)
    p = tf.paragraphs[0]
    p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}.get(align, PP_ALIGN.LEFT)
    p.line_spacing = spacing
    for text, weight, color in runs:
        if not str(text):
            continue
        face, is_bold = _weight_face(weight, False)
        r = p.add_run()
        r.text = str(text)
        r.font.name = face
        r.font.size = Pt(size)
        r.font.bold = is_bold
        r.font.italic = False
        r.font.color.rgb = color
    return box, m.lines, m.height_in


def _add_rect(slide, left, top, width, height, *,
              fill: Optional[RGBColor] = None,
              line: Optional[RGBColor] = None,
              line_w: Optional[float] = None,
              radius: float = 0.14):
    """A rounded panel (the design pack: every corner is rounded, cards at
    0.14in). `radius=0` draws a square hairline, for rules only."""
    w_in, h_in = Emu(width).inches, Emu(height).inches
    rounded = radius > 0 and min(w_in, h_in) > 0.05
    rect = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                                  left, top, width, height)
    if rounded:
        try:
            rect.adjustments[0] = min(0.5, float(radius) / max(min(w_in, h_in), 0.01))
        except Exception:
            pass
    if fill is None:
        rect.fill.background()
    else:
        rect.fill.solid()
        rect.fill.fore_color.rgb = fill
    if line is None:
        rect.line.fill.background()
    else:
        rect.line.color.rgb = line
        if line_w is not None:
            rect.line.width = Pt(line_w)
    rect.shadow.inherit = False
    _plain_shape(rect)
    return rect


def _plain_shape(shape) -> None:
    """Take the theme style reference off a drawn shape. python-pptx adds
    one to every autoshape (the theme's line, fill and effect), and some
    renderers draw the theme's drop shadow from it even when the shape
    switches its own effects off. A shape here is its fill and its line
    and nothing else."""
    try:
        st = shape._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style")
        if st is not None:
            shape._element.remove(st)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
#  The page (2026-11-11). Every page is drawn on the design pack's
#  grid and carries its chrome in the same place: the eyebrow and its
#  dot top left, the logo top right, the headline as a bold claim with
#  a light close, the source line, the footer lockup and the page
#  number as a field. The four surfaces are the pack's: Graphite,
#  Off-White, and the pale Signal and Orchid gradients.
# ─────────────────────────────────────────────────────────────────────
M = 0.92                  # the content band's left edge
RIGHT = 12.413            # and its right edge
BAND = RIGHT - M          # 11.493in
GUT = 0.28                # the gutter between columns
COL2 = (BAND - GUT) / 2           # 5.607in
COL3 = (BAND - 2 * GUT) / 3       # 3.644in
TITLE_Y = 1.05
SUB_Y = 1.846
TOP_ONE = 2.33            # content top under a one-line subhead
TOP_TWO = 2.75            # and under a two-line subhead
TITLE_STEP = 0.53         # what a second headline line pushes down
SRC_Y = 6.62
FOOT_Y = 7.08
FOOTER_LOCKUP = "CROSSWALK / BEHAVIORAL INTELLIGENCE ENGINE"


def _rgb(hex6: str) -> RGBColor:
    return RGBColor.from_string(str(hex6).lstrip("#").upper())


@dataclass(frozen=True)
class Surf:
    """One page surface and the colors type takes on it."""
    name: str
    dark: bool
    title: RGBColor
    body: RGBColor
    muted: RGBColor
    footer: RGBColor
    accent: RGBColor
    negative: RGBColor
    dot: RGBColor
    card: RGBColor
    card_title: RGBColor
    card_body: RGBColor
    card_label: RGBColor
    card_accent: RGBColor
    rule: RGBColor
    logo: str


S_DARK = Surf("dark", True, _rgb("E9E8E1"), _rgb("9AA09B"), _rgb("7C878A"), _rgb("7C878A"),
              _rgb("C7F23E"), _rgb("E682FF"), _rgb("C7F23E"),
              _rgb("15252A"), _rgb("E9E8E1"), _rgb("A8AFA9"), _rgb("9BAAAC"), _rgb("C7F23E"),
              _rgb("27393D"), "white")
S_LIGHT = Surf("light", False, _rgb("0C1618"), _rgb("5C6560"), _rgb("888C89"), _rgb("5C6466"),
               _rgb("5E7E12"), _rgb("8E3FA8"), _rgb("5E7E12"),
               _rgb("E1E0D7"), _rgb("0C1618"), _rgb("5C6560"), _rgb("5C6466"), _rgb("5E7E12"),
               _rgb("C9C6BA"), "black")
# the pale gradients: all type Graphite, cards Graphite with the dark card's type
S_SIGNAL = Surf("signal", False, _rgb("0C1618"), _rgb("0C1618"), _rgb("0C1618"), _rgb("0C1618"),
                _rgb("0C1618"), _rgb("8E3FA8"), _rgb("0C1618"),
                _rgb("0C1618"), _rgb("E9E8E1"), _rgb("A8AFA9"), _rgb("9BAAAC"), _rgb("C7F23E"),
                _rgb("0C1618"), "black")
S_ORCHID = Surf("orchid", False, _rgb("0C1618"), _rgb("0C1618"), _rgb("0C1618"), _rgb("0C1618"),
                _rgb("0C1618"), _rgb("8E3FA8"), _rgb("0C1618"),
                _rgb("0C1618"), _rgb("E9E8E1"), _rgb("A8AFA9"), _rgb("9BAAAC"), _rgb("C7F23E"),
                _rgb("0C1618"), "black")


def _normalize_project_label(label: str) -> str:
    """A project label for display: an em dash or an en dash becomes a
    comma, so no page carries a stray dash (Liz item 20)."""
    if not label:
        return ""
    return (
        str(label)
        .replace(" \u2014 ", ", ")
        .replace(" \u2013 ", ", ")
        .replace("\u2014", ",")
        .replace("\u2013", ",")
    )


def _blank(prs):
    """python-pptx ships several layouts; the 'blank' one (index 6) is the
    cleanest base because there are no placeholder text boxes to fight."""
    layout = prs.slide_layouts[6]
    return prs.slides.add_slide(layout)


def _ground(slide, surf: Surf) -> None:
    """The page ground, set as the slide background: a solid for Graphite
    and Off-White, the pack's own gradient (built at full resolution) for
    the two color pages. A gradient that cannot be built falls back to
    the gradient's lead color, so the deck still carries its color page."""
    if surf.name in ("signal", "orchid"):
        pack = _design()
        try:
            data = pack.gradient_image_bytes(surf.name) if pack is not None else None
            if data:
                pack.set_slide_background_image(slide, data)
                return
        except Exception:
            pass
        _add_bg(slide, _rgb("C7F23E") if surf.name == "signal" else _rgb("E682FF"))
        return
    _add_bg(slide, GRAPHITE if surf.dark else OFFWHITE)


def T(slide, x, y, w, h, text, **kw):
    """`_add_text` in inches."""
    return _add_text(slide, Inches(x), Inches(y), Inches(w), Inches(h), text, **kw)


def BODY(slide, x, y, w, text, *, max_h, size, floor: float = BODY_FLOOR, **kw):
    """`_add_body` in inches: a block that fits `max_h`. Returns
    (shape, height in inches)."""
    shape, h = _add_body(slide, Inches(x), Inches(y), Inches(w), text, max_height=Inches(max(0.2, max_h)),
                         size=size, floor=floor, **kw)
    return shape, Emu(h).inches


def _card(slide, x, y, w, h, *, fill=None, line=None, line_w: float = 0.75, radius: float = 0.14):
    return _add_rect(slide, Inches(x), Inches(y), Inches(w), Inches(h), fill=fill, line=line,
                     line_w=line_w, radius=radius)


def _rule(slide, x, y, w, color: RGBColor):
    """A hairline."""
    return _add_rect(slide, Inches(x), Inches(y), Inches(w), Inches(0.011), fill=color, radius=0)


def _label(slide, x, y, w, text, color: RGBColor, *, size: float = 9.5, align: str = "left"):
    """A caps label: SemiBold, tracked +1.0, one line."""
    return T(slide, x, y, w, 0.2, str(text).upper(), size=size, weight="SemiBold", color=color,
             letter_spacing=1.0, align=align)


def _page_number(slide, x, y, w, h, color: RGBColor, *, size: float = 8, page: Optional[int] = None):
    """The page number as a slide-number field (the design pack fails a
    typed one): PowerPoint keeps it right when pages move."""
    from lxml import etree
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Emu(0)
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.RIGHT
    fld = etree.SubElement(para._p, ns + "fld")
    fld.set("id", "{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}")
    fld.set("type", "slidenum")
    rpr = etree.SubElement(fld, ns + "rPr")
    rpr.set("lang", "en-US")
    rpr.set("sz", str(int(size * 100)))
    rpr.set("b", "0")
    rpr.set("spc", "200")
    fill = etree.SubElement(rpr, ns + "solidFill")
    etree.SubElement(fill, ns + "srgbClr").set("val", "%02X%02X%02X" % (color[0], color[1], color[2]))
    for tag in ("latin", "ea", "cs"):
        etree.SubElement(rpr, ns + tag).set("typeface", _house_face("Regular"))
    etree.SubElement(fld, ns + "t").text = str(page) if page else "‹#›"
    return box


def _one_line(text: str, size: float, width_in: float, *, tracking: float = 0.0, bold: bool = False) -> str:
    """A caps line held to one line: it gives up its last words until it
    fits (the eyebrow sits on the dot's line; a second line would land
    on the headline)."""
    words = str(text or "").split()
    while len(words) > 1:
        t = " ".join(words)
        need = _measure(t, size, 99.0, bold=bold).max_w_in + len(t) * tracking / 72.0
        if need <= width_in:
            break
        words.pop()
    return " ".join(words)


def _chrome(slide, surf: Surf, idx: int, eyebrow: str, logo_path: Optional[str], *,
            footer: str = FOOTER_LOCKUP, source: Optional[str] = None, page: Optional[int] = None):
    """The parts of a page that never move: the eyebrow and its dot, the
    logo, the source line, the footer lockup and the page number."""
    dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(M), Inches(0.62), Inches(0.085), Inches(0.085))
    dot.fill.solid()
    dot.fill.fore_color.rgb = surf.dot
    dot.line.fill.background()
    dot.shadow.inherit = False
    _plain_shape(dot)
    label = _one_line(f"{idx:02d}  {str(eyebrow or '').upper()}", 10.5, 9.4, tracking=2.6)
    T(slide, 1.129, 0.54, 9.6, 0.24, label, size=10.5, color=surf.body, letter_spacing=2.6, anchor="middle")
    if logo_path:
        try:
            slide.shapes.add_picture(logo_path, Inches(RIGHT - 1.33), Inches(0.558), width=Inches(1.33))
        except Exception:
            pass
    if source:
        T(slide, M, SRC_Y, BAND, 0.26, _one_line(source, 9, BAND), size=9, color=surf.muted)
    T(slide, M, FOOT_Y, 8.2, 0.22, footer, size=8, color=surf.footer, letter_spacing=2.0)
    _page_number(slide, RIGHT - 2.2, FOOT_Y, 2.2, 0.22, surf.footer, page=page or idx)


_TITLE_SIZES = (34, 32, 30, 28)


def _pick_title(options) -> tuple:
    """The first headline that holds: one sentence of twelve words or
    fewer that fits two lines of the band at a title size. Each option is
    (bold claim, light close). The last option is the plain fallback and
    is taken when none of the others hold (a long partnership name)."""
    opts = [o for o in options if o and (o[0] or o[1])]
    for bold, light in opts:
        text = f"{bold}{light}"
        if len(text.split()) > 12:
            continue
        if _line_count(text, _TITLE_SIZES[-1], BAND, bold=True) <= 2:
            return bold, light
    return opts[-1] if opts else ("", "")


def _head(slide, surf: Surf, idx: int, eyebrow: str, title, sub: Optional[str], logo_path: Optional[str], *,
          source: Optional[str] = None, footer: str = FOOTER_LOCKUP) -> float:
    """Chrome, headline and subhead. Returns the y the content starts at:
    2.33in under a one-line subhead, 2.75in under two lines, and a
    headline that wraps pushes both down by one title line."""
    _chrome(slide, surf, idx, eyebrow, logo_path, footer=footer, source=source)
    bold, light = title if isinstance(title, (tuple, list)) else (str(title or ""), "")
    runs = [(bold, "Bold", surf.title), (light, "Light", surf.title)]
    size = _TITLE_SIZES[-1]
    for sz in _TITLE_SIZES:
        if _runs_fit(runs, sz, BAND)[1] <= 2:
            size = sz
            break
    _box, lines, _h = _add_runs(slide, Inches(M), Inches(TITLE_Y), Inches(BAND), runs, size=size)
    shift = TITLE_STEP * (min(lines, 2) - 1) if lines > 1 else 0.0
    if not sub:
        return TOP_ONE + shift - 0.30
    sub = str(sub)
    sub_lines = _measure(sub, 16, BAND, spacing=1.15).lines
    if sub_lines > 2:
        _sz, _sp, sub, _m = _fit_body(sub, BAND, 2 * _line_h_in(16, 1.15) + 0.01, size=16, floor=16, spacing=1.15)
        sub_lines = 2
    T(slide, M, SUB_Y + shift, BAND, 0.3, sub, size=16, color=surf.body, spacing=1.15)
    return (TOP_ONE if sub_lines <= 1 else TOP_TWO) + shift


# ─────────────────────────────────────────────────────────────────────
#  Image helpers - the cover photo.
# ─────────────────────────────────────────────────────────────────────
_S3_BUCKET_NAME = "dashboard-inputs"
_S3_REGION = "us-east-2"
_IMAGE_PROXY_PREFIX = "/api/profile-image-file/"
# A real browser header: the public photo hosts (press sites, CDNs) answer
# a bare library User-Agent with 403 or an HTML interstitial.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _s3_client():
    """A boto3 client on the app's own credentials, built only when a
    cover photo lives in the private bucket. None when boto3 is absent."""
    try:
        import boto3  # deferred: the deck must build where boto3 is missing
        from botocore.config import Config as _BotoConfig
        return boto3.client("s3", region_name=_S3_REGION,
                            config=_BotoConfig(retries={"max_attempts": 2, "mode": "standard"}))
    except Exception:
        return None


def _proxy_key(url: str) -> Optional[str]:
    """The S3 key behind a dashboard image path. The admin upload stores
    the photo in the private bucket and the sidecar records it as
    `/api/profile-image-file/<key>` (relative, or absolute on the
    dashboard's own origin). None when the URL is not that proxy."""
    try:
        path = url
        if re.match(r"^https?://", url, re.I):
            path = urllib.parse.urlsplit(url).path or ""
        if path.startswith(_IMAGE_PROXY_PREFIX):
            key = urllib.parse.unquote(path[len(_IMAGE_PROXY_PREFIX):]).strip("/")
            return key or None
    except Exception:
        pass
    return None


def _image_bytes(url: str, s3_client=None) -> Optional[bytes]:
    """The cover photo's bytes, or None (the cover then takes the
    gradient ground).

      1. `/api/profile-image-file/<key>` (the admin upload, the shape the
         Willow Smith, MMG, Jennifer Beals and Lainey Wilson runs carry):
         the object is read from the private bucket; the proxy itself
         needs a dashboard session, so a GET on it never worked (2026-10-29).
      2. an absolute http(s) URL: fetched with a browser User-Agent.
      3. anything else: None.
    Never raises."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    key = _proxy_key(url)
    if key:
        try:
            s3 = s3_client or _s3_client()
            if s3 is None:
                return None
            data = s3.get_object(Bucket=_S3_BUCKET_NAME, Key=key)["Body"].read()
            return data if data and len(data) > 200 else None
        except Exception as exc:
            print(f"[bpiq_deck_builder] cover photo read failed for {key!r}: {exc}")
            return None
    if re.match(r"^https?://", url, re.I):
        try:
            r = requests.get(url, timeout=8, headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            })
            if r.status_code != 200 or not r.content or len(r.content) < 200:
                return None
            return r.content
        except Exception as exc:
            print(f"[bpiq_deck_builder] cover photo fetch failed for {url!r}: {exc}")
            return None
    return None


def _open_image(data: bytes):
    """Decode uploaded photo bytes with Pillow, loaded. The admin upload
    keeps whatever extension the file came with, so a `.jpg` can be an
    AVIF inside (the Jennifer Beals photo, 2026-10-29): Pillow decodes
    AVIF from 11.3.0 in its wheels; on an older Pillow the optional
    pillow_avif plugin is tried once. None when nothing can read it."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im
    except Exception as first:
        if data[4:12] == b"ftypavif" or data[4:12] == b"ftypavis":
            try:
                import pillow_avif  # noqa: F401  (optional plugin for Pillow < 11.3)
                im = Image.open(io.BytesIO(data))
                im.load()
                return im
            except Exception:
                pass
        print(f"[bpiq_deck_builder] cover photo could not be decoded: {first}")
        return None


# Where the cover's type sits, in page fractions: the logo, the kicker,
# the pairing and its claim on the left, and the three figures along the
# bottom. The scrim is deepened until body type reads over each.
_COVER_ZONES = ((0.06, 0.08, 0.70, 0.66), (0.06, 0.70, 0.94, 0.90))


def _cover_photo_background(url: Optional[str], s3_client=None) -> Optional[bytes]:
    """The cover photo as a page background, the house way: read from the
    bucket or the web, decoded whatever the upload was (webp, png, AVIF),
    cover-cropped to the page at 3840 x 2160 with the Graphite scrim
    baked in and deepened until the type over it reads. None when there
    is no photo, when it cannot be read, or when it is too light to
    carry type; the cover then takes the gradient ground. Never raises."""
    if not url or not isinstance(url, str):
        return None
    try:
        data = _image_bytes(url, s3_client=s3_client)
        if not data:
            return None
        im = _open_image(data)
        if im is None:
            return None
        if im.mode in ("RGBA", "LA", "P"):
            # flatten transparency onto the cover's dark ground
            im = im.convert("RGBA")
            ground = Image.new("RGBA", im.size, (0x0C, 0x16, 0x18, 255))
            im = Image.alpha_composite(ground, im)
        im = im.convert("RGB")
        if max(im.size) > 4200:
            ratio = 4200 / max(im.size)
            im = im.resize((int(im.width * ratio), int(im.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        pack = _design()
        if pack is None:
            return None
        zones = [z + ((0x9A, 0xA0, 0x9B),) for z in _COVER_ZONES]
        baked, _alpha = pack.bake_photo_for_text(buf.getvalue(), zones, scrim_hex="#0C1618", alpha=0.62)
        return baked or None
    except Exception as exc:
        print(f"[bpiq_deck_builder] cover photo not used: {exc}")
        return None


def _render_crosswalk_logo_png(on_dark: bool = True) -> Optional[str]:
    """The path to the Crosswalk lockup for a surface: white on dark,
    black on light and on the pale gradients. The real file from the
    design pack (a transparent PNG); the older static file stands in when
    the pack is absent. None when neither is on the machine (the page
    then carries no logo rather than a drawn one)."""
    _ds = _design()
    if _ds is not None:
        try:
            _lp = _ds.logo_path("white" if on_dark else "black")
            if _lp and os.path.exists(str(_lp)):
                return str(_lp)
        except Exception:
            pass
    target = _BRAND_LOGO_WHITE if on_dark else _BRAND_LOGO_BLACK
    return target if os.path.exists(target) else None


_BRAND_LOGO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)
_BRAND_LOGO_WHITE = os.path.join(_BRAND_LOGO_DIR, "crosswalk-logo-brand-white.png")
_BRAND_LOGO_BLACK = os.path.join(_BRAND_LOGO_DIR, "crosswalk-logo-brand-black.png")


# ─────────────────────────────────────────────────────────────────────
#  What a reader never sees (2026-11-11, Jenna: "these errors cannot
#  happen"). The valuation deck is a paid delivery. It carries no test
#  statistic, no outside name behind a rate, no word for how a figure
#  was made, and no dashboard note. Lines the valuation itself supplies
#  (the campaign's account of the drop, a share's basis, a study note)
#  pass through `_safe_prose`, which takes out any sentence that does.
# ─────────────────────────────────────────────────────────────────────
_OUTSIDE_NAMES = re.compile(
    r"\b(hootsuite|sprout social|emarketer|iab\b|smi\b|nielsen|comscore|statista|pew\b|sensor tower|data\.ai|"
    r"yougov|circana|bookscan|edison|luminate|similarweb|semrush|mri simmons|kantar|ipsos|gartner|forrester|"
    r"marketing solutions|amazon ads|benchmark report|benchmarks? 20\d\d|ratecard|rate card|rate guide|"
    r"ad rate|sources?:(?!\s*crosswalk)|according to)", re.I)
_METHOD_WORDS = re.compile(
    r"\b(panel|panels|panelists?|modeled|modelled|modeling|estimat(?:e|ed|es|ion)|synthetic|synth|calibrat\w+|anchored|"
    r"z[- ]?score|p[- ]?value|confidence interval|statistical\w*|significan\w+|margin of error|holdout|"
    r"difference[- ]in[- ]differences?|cohort weight|incidence|dashboard|editable)\b", re.I)
_TEST_STATS = re.compile(r"(\bz\s*=|\bp\s*[<=]|95%\s*CI|\bCI\b|Δ)", re.I)


def reader_blocked(text: Any) -> list:
    """The words in `text` that a paid deck never carries: outside names,
    method words, test statistics. Empty for clean copy. The regression
    test runs this over every page of a finished deck."""
    s = str(text or "")
    return [m.group(0) for rx in (_OUTSIDE_NAMES, _METHOD_WORDS, _TEST_STATS) for m in rx.finditer(s)]


def _safe_prose(text: Any, *, brand: str = "", talent: str = "") -> str:
    """A line the valuation supplied, made safe to print: dashes out,
    'the talent' and 'the brand' named, and any sentence that names an
    outside source, a method or a test statistic taken out whole."""
    s = str(text or "").replace("\u2014", ", ").replace("\u2013", " to ").replace(" - ", ", ")
    s = " ".join(s.split())
    if not s:
        return ""
    if talent:
        s = re.sub(r"\bthe talent's\b", f"{talent}'s", s)
        s = re.sub(r"\bthe talent\b", talent, s)
    if brand:
        s = re.sub(r"\bthe brand's\b", f"{brand}'s", s)
    # a sentence past thirty words is not plain English; it goes too
    kept = [x for x in _sentences(s) if not reader_blocked(x) and len(x.split()) <= 30]
    return " ".join(kept).strip()


def _basis_sentences(text: Any, *, brand: str, talent: str, has_control: bool) -> str:
    """A share's stated basis ('68% of the exposure carried the talent;
    those assets drew 22% more response ...') as plain sentences. The
    clause about a comparison group goes when none was bought."""
    t = _plain_basis(text, has_control)
    parts = [c.strip().rstrip(".") for c in re.split(r";\s*", t) if c.strip()]
    out = []
    for c in parts:
        # 'the matched group kept 95% of the lift' reads the wrong way round
        c = re.sub(r"^the matched group kept (\d+(?:\.\d+)?)% of the lift$",
                   r"\1% of the rise held up against the matched group", c, flags=re.I)
        c = _safe_prose(c + ".", brand=brand, talent=talent)
        if c:
            out.append(c[:1].upper() + c[1:])
    return " ".join(out)


# ─────────────────────────────────────────────────────────────────────
#  Narrative helpers - derive headlines and readings from the data.
#  Pure functions, easy to extend / tune in isolation.
# ─────────────────────────────────────────────────────────────────────
NO_BRAND = "the brand"          # what the copy says when the valuation names no brand


@dataclass
class DeckCtx:
    """All the derived numbers the slide builders need, computed once
    so individual slides stay readable."""
    project:        str
    brand:          str
    talent:         str
    category_label: str
    is_auto:        bool
    cover_bg:       Optional[bytes]
    logo_white:     Optional[str]
    logo_black:     Optional[str]
    total_value:    float
    emv:            float
    bev:            float
    blv:            float
    cv:             float
    lenses:         list
    emv_rows:       list
    incr_users:     int
    audience_proj:  int
    pre_users_proj: int
    post_users_proj:int
    pre_pen:        float
    post_pen:       float
    delta_pp:       float
    delta_rel:      float
    c_pre_pen:      float
    c_post_pen:     float
    c_delta_pp:     float
    lift_pp:        float
    pre_days:       int
    post_days:      int
    pre_range:      str
    post_range:     str
    after_word:     str
    before_word:    str
    has_conversions:bool
    has_yearly:     bool
    has_control:    bool
    open_window:    bool
    site:           str
    draft:          bool
    sources_line:   str
    data:           dict

    def logo(self, surf: Surf) -> Optional[str]:
        return self.logo_white if surf.logo == "white" else self.logo_black

    @property
    def long_brand(self) -> bool:
        """A name that running copy says as 'the brand': three words or
        more, or no name at all (the placeholder)."""
        return len(self.brand.split()) >= 3 or self.brand == NO_BRAND

    @property
    def b(self) -> str:
        """The brand in running copy. A name of three words or more is
        said in full on the cover and in a headline, and as 'the brand'
        in the copy under it: a long name repeated down every page reads
        as a stamp."""
        return "the brand" if self.long_brand else self.brand

    @property
    def ba(self) -> str:
        """The brand before a noun: 'Gap engagers', or 'brand engagers'
        for a long name."""
        return "brand" if self.long_brand else self.brand

    def titles(self, options) -> tuple:
        """`_pick_title`, without the options that carry a brand name of
        five words or more (the name alone is then a phrase the reader
        meets on every page)."""
        opts = list(options)
        if len(self.brand.split()) >= 5 or self.brand == NO_BRAND:
            kept = [o for o in opts if self.brand not in f"{o[0]}{o[1]}"]
            opts = kept or opts[-1:]
        return _pick_title(opts)


def _has_control(data: dict) -> bool:
    """True when the valuation carries a comparison group (the $1,000
    valuation). A valuation bought without one (2026-11-11) is a before
    and after read: no page may speak of a matched group, an adjusted
    lift or a held baseline."""
    cg = _dict(_dict(data).get("control_group"))
    if not cg or not bool(cg.get("enabled", True)):
        return False
    return _count(cg.get("control_size")) > 0 or cg.get("incremental_lift_pp") is not None


def is_draft(data: dict) -> bool:
    """What puts DRAFT on the cover: the valuation itself says so, with
    `"draft": true` or `"status": "draft"`. Nothing the product saves
    today does, so a valuation a client paid for never carries the word
    (2026-11-11: every cover was stamped DRAFT with the build date). An
    operator preparing a read ahead of delivery sets the flag."""
    d = _dict(data)
    if d.get("draft") is True or str(d.get("status") or "").strip().lower() == "draft":
        return True
    return _dict(d.get("diagnostics")).get("draft") is True


def _plain_basis(text: str, has_control: bool) -> str:
    """A share's stated basis with any comparison-group clause removed
    when no comparison group was bought."""
    t = str(text or "").strip()
    if has_control or not t:
        return t
    kept = [c.strip() for c in re.split(r";\s*", t)
            if c.strip() and not re.search(r"matched|holdout|comparison group|control", c, re.I)]
    return "; ".join(kept)


def _dict(o: Any) -> dict:
    """A sub-document that may be missing, null, or the wrong type."""
    return o if isinstance(o, dict) else {}


_MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
_MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"]


def _ymd(iso: Any) -> Optional[tuple]:
    try:
        y, m, d = (int(x) for x in str(iso or "")[:10].split("-"))
        if 1 <= m <= 12 and 1 <= d <= 31:
            return y, m, d
    except (TypeError, ValueError):
        pass
    return None


def _fmt_date(iso: str, long: bool = False) -> str:
    """YYYY-MM-DD as 'Sep 22, 2026' (or 'September 22, 2026'). The input
    comes back unchanged when it is not a date."""
    p = _ymd(iso)
    if p is None:
        return str(iso or "")
    y, m, d = p
    return f"{(_MONTHS_LONG if long else _MONTHS_SHORT)[m - 1]} {d}, {y}"


def _fmt_range(start: Any, end: Any, long: bool = False) -> str:
    """'Sep 22 to Nov 11, 2026'; the year prints twice only when the
    range crosses one."""
    a, b = _ymd(start), _ymd(end)
    if a is None or b is None:
        return " to ".join(x for x in (str(start or ""), str(end or "")) if x)
    names = _MONTHS_LONG if long else _MONTHS_SHORT
    if a[0] == b[0]:
        return f"{names[a[1] - 1]} {a[2]} to {names[b[1] - 1]} {b[2]}, {b[0]}"
    return f"{names[a[1] - 1]} {a[2]}, {a[0]} to {names[b[1] - 1]} {b[2]}, {b[0]}"


_SHARE_WORDS = ((10.0, "one in ten"), (20.0, "one in five"), (25.0, "one in four"), (33.3, "one in three"),
                (40.0, "four in ten"), (50.0, "half"), (60.0, "six in ten"), (66.7, "two in three"),
                (75.0, "three in four"), (80.0, "eight in ten"), (90.0, "nine in ten"))


def _plain_share(pct: float) -> str:
    """A share a reader can picture: 61.5 -> 'six in ten', 34.3 -> 'one
    in three'. Outside the plain range the figure itself comes back."""
    p = float(pct or 0)
    if p >= 95:
        return "nearly all"
    if p < 7.5:
        return f"{p:.1f}%"
    return min(_SHARE_WORDS, key=lambda sw: abs(sw[0] - p))[1]


def _about_share(pct: float, *, of: str = "of") -> str:
    """'about six in ten of', 'about half of', 'nearly all of', '4.2% of'."""
    w = _plain_share(pct)
    if w.endswith("%") or w == "nearly all":
        return f"{w} {of}".strip()
    return f"about {w} {of}".strip()


_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def _n_word(n: int) -> str:
    """A small count in words ('four parts'); a larger one in figures."""
    return _NUMBER_WORDS[n] if 0 <= int(n) < len(_NUMBER_WORDS) else f"{int(n):,}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _times(x: float) -> str:
    """'27 times', '2.4 times' for a ratio."""
    x = float(x or 0)
    return f"{x:.0f} times" if x >= 10 else f"{x:.1f} times"


_CHANNEL_NAMES = {"x (twitter)": "X", "twitter": "X", "direct (brand site)": None}
_HOST_RX = re.compile(r"(?<![\w.@-])([a-z0-9][a-z0-9-]*)\.(com|co|net|org|io|us|tv|shop|store)\b", re.I)


def _brand_site(data: dict, brand: str) -> str:
    """The brand's own site as a reader knows it ('Gap.com'), read off
    the pages the valuation itself lists: a host whose name is the
    brand's. '' when the valuation lists none (the channel is then
    'the brand's site and app'); a domain is never guessed."""
    fold = re.sub(r"[^a-z0-9]", "", str(brand or "").lower())
    if not fold or brand == NO_BRAND:
        return ""
    for key in ("top_brand_properties", "top_brand_properties_pre"):
        for r in (_dict(data).get(key) or []):
            if not isinstance(r, dict):
                continue
            for m in _HOST_RX.finditer(str(r.get("common_name") or "")):
                if m.group(1).lower().replace("-", "") == fold:
                    return f"{re.sub(r'[^A-Za-z0-9]', '', str(brand))}.{m.group(2).lower()}"
    return ""


def _channel(name: str, brand: str, site: str = "") -> str:
    """A channel as the table prints it, by the name a reader knows:
    'Gap.com and app' (2026-11-11), 'X'."""
    key = str(name or "").strip().lower()
    if key == "direct (brand site)":
        return f"{site} and app" if site else f"{brand} site and app"
    return _CHANNEL_NAMES.get(key) or str(name or "").strip()


def _channel_in_sentence(name: str, brand: str, site: str = "") -> str:
    """The same channel inside a sentence: 'press', 'the launch event'."""
    key = str(name or "").strip().lower()
    if key == "press":
        return "press"
    if key == "launch event":
        return "the launch event"
    if key == "direct (brand site)":
        return f"{site} and app" if site else f"the {brand} site and app"
    return _channel(name, brand, site)


_SMALL_LABEL_WORDS = {"and", "or", "to", "of", "than", "under", "older", "more", "not", "say", "over", "less"}
_LABEL_MAP = {"another race/ethnicity": "Another race or ethnicity"}


def _bucket_label(label: Any) -> str:
    """A demographic bucket in sentence case, as it reads in a table: '17
    and under', '65 or older', 'Prefer not to say', '$25,000 to $49,999'.
    Names keep their capitals ('Hispanic or Latino', 'Black or African
    American')."""
    s = " ".join(str(label or "n/a").split())
    if s.lower() in _LABEL_MAP:
        return _LABEL_MAP[s.lower()]
    s = re.sub(r"\s+-\s+", " to ", s)
    words = s.split(" ")
    out = []
    for i, w in enumerate(words):
        out.append(w.lower() if (i > 0 and w.lower() in _SMALL_LABEL_WORDS) else w)
    return " ".join(out)


def _matched_group_lines(cg: dict, sizes: Optional[tuple] = None) -> tuple:
    """The reader-facing account of the comparison group (Jenna
    2026-10-20: same size, roughly the same brand engagement before the
    partnership). Returns (one_line, side_by_side) where side_by_side
    is '' when the payload carries no match figures. `sizes` is
    (exposed, matched) as projected US people, the counts every other
    page of the deck uses; without it the match block's own sizes print."""
    try:
        from migration.bpiq_matched_control import READER_NOTE as _note
    except ImportError:  # pragma: no cover - twin layout
        _note = ("Compared against a matched group of the same size with the "
                 "same brand engagement before the partnership.")
    m = _dict(_dict(cg).get("match"))
    if not m:
        return _note, ""
    exposed, matched = sizes if sizes else (_count(m.get("exposed_size")), _count(m.get("control_size")))
    side = (f"Exposed group {fmt_num(exposed)} people, "
            f"{_num(m.get('exposed_pre_rate_pct')):.2f}% engaged with the brand before; "
            f"matched group {fmt_num(matched)} people, "
            f"{_num(m.get('control_pre_rate_pct')):.2f}% before.")
    return _note, side


def _same_size(ctx) -> bool:
    """True when the matched group holds as many people as the campaign
    reached, the only case in which a page may say 'the same size'."""
    cg = _dict(_dict(ctx.data).get("control_group")) if hasattr(ctx, "data") else {}
    n = _count(cg.get("projected_control_size"))
    return (not n) or n == ctx.audience_proj


def _control_people(cg: dict, projected: int) -> int:
    """The matched group's gain in brand engagers over the windows."""
    if cg.get("control_pre_users_projected") is not None:
        return _count(cg.get("control_post_users_projected")) - _count(cg.get("control_pre_users_projected"))
    size = _count(cg.get("projected_control_size")) or projected
    return int(round(size * _num(cg.get("control_delta_pp")) / 100.0))


def _emv_rows(data: dict, brand: str) -> list:
    """The earned-media table: every channel the valuation priced, in
    value order. Nothing is left off, so the rows add up to the earned
    media value the deck prints (2026-11-11: LinkedIn was priced in the
    total and missing from the table)."""
    rows = []
    site = _brand_site(data, brand)
    for r in (_dict(data.get("valuation")).get("emv_breakdown") or []):
        if not isinstance(r, dict):
            continue
        value = round(_num(r.get("emv_value")), 2)
        if value <= 0:
            continue
        rows.append({
            "name": str(r.get("platform") or "").strip(),
            "display": _channel(str(r.get("platform") or ""), brand, site),
            "people": _count(r.get("incremental_users_projected")),
            "rate": _num(r.get("emv_per_user_rate")),
            "value": value,
        })
    rows.sort(key=lambda r: -r["value"])
    return rows


def _window_open(data: dict, post_p: dict) -> bool:
    """Is the valuation's window still open: the campaign was still
    running when it was valued (the order said so, or it runs on a
    figure given while the campaign ran), or the window ends on the day
    the valuation was made. The opening line then says the value is the
    value so far (2026-11-11); a window that closed before the
    valuation was made keeps 'delivered'. Read from the valuation's own
    fields, so the deck is the same deck on whatever day it is built."""
    data = _dict(data)
    if _dict(data.get("windows")).get("open") is True:
        return True
    if _dict(data.get("diagnostics")).get("post_window_clipped_to_today") is True:
        return True
    end = str(_dict(post_p).get("end") or data.get("end_date") or "")[:10]
    made = str(data.get("created_at") or "")[:10]
    return bool(_ymd(end) and _ymd(made) and end >= made)


def _derive_context(data: dict, image_url: Optional[str],
                    category: Optional[str], s3_client=None) -> DeckCtx:
    data   = _dict(data)
    totals = _dict(data.get("totals"))
    val    = _dict(data.get("valuation"))
    cg     = _dict(data.get("control_group"))
    is_auto = (str(category or "").strip().lower() == "automotive")
    conv = _dict(data.get("conversions"))
    has_control = _has_control(data)

    project = _normalize_project_label(str(data.get("project_name") or "Brand Partnership Valuation"))
    brand   = str(data.get("brand_partner") or "").strip() or NO_BRAND
    q = data.get("qualifier_value")
    names = [str(v).strip() for v in q if str(v).strip()] if isinstance(q, (list, tuple)) else [str(q or "").strip()]
    talent = names[0] if names and names[0] else ""
    cat_lbl = (category or "").strip().title() or "Brand Partnership"

    pre_pen  = _num(totals.get("audience_pen_pre_pct"))
    post_pen = _num(totals.get("audience_pen_post_pct"))
    delta_pp = round(post_pen - pre_pen, 2)
    delta_rel = ((post_pen - pre_pen) / pre_pen * 100) if pre_pen else 0.0
    projected = _count(data.get("projected_audience_size"))

    # ── the value, as the pages will show it ─────────────────────────
    # Every page total is the sum of the parts printed on that page
    # (2026-11-11: the total carried conversion value while the
    # conversion tile was hidden as a thin signal, so the page did not
    # add up). The earned media figure is the sum of its table. A lens
    # is shown whenever it carries value; the one exception is the
    # automotive policy (purchases there are not visible), where the
    # lens and its dollars both stay out.
    emv_rows = _emv_rows(data, brand)
    emv = round(sum(r["value"] for r in emv_rows), 2) if emv_rows else round(_num(val.get("earned_media_value")), 2)
    bev = round(_num(val.get("brand_engagement_value")), 2)
    blv = round(_num(val.get("brand_lift_value")), 2)
    cv_all = round(_num(val.get("conversion_value")), 2)
    has_conv = (not is_auto) and cv_all > 0 and conv.get("enabled", True) is not False
    cv = cv_all if has_conv else 0.0
    lenses = [("Earned media", emv), ("Brand engagement", bev), ("Brand lift", blv)]
    if has_conv:
        lenses.append(("Conversion", cv))
    total = round(sum(v for _l, v in lenses), 2)
    stated = round(_num(val.get("total_brand_value")), 2)
    if stated and abs(stated - total) > 0.01 and not (is_auto and abs(stated - cv_all - total) <= 0.01):
        print(f"[bpiq_deck_builder] the deck's total {total:,.2f} is the sum of its parts; "
              f"the valuation stated {stated:,.2f}")

    # ── the comparison group, when one was bought ───────────────────
    c_pre = _num(cg.get("control_pre_pen_pct")) if has_control else 0.0
    c_post = _num(cg.get("control_post_pen_pct")) if has_control else 0.0
    c_delta = _num(cg.get("control_delta_pp")) if has_control else 0.0
    if has_control and cg.get("control_delta_pp") is None:
        c_delta = round(c_post - c_pre, 2)
    if has_control:
        lift_pp = _num(cg.get("incremental_lift_pp")) if cg.get("incremental_lift_pp") is not None \
            else round(delta_pp - c_delta, 2)
    else:
        lift_pp = delta_pp

    pre_p, post_p, ev_p = _dict(data.get("pre_period")), _dict(data.get("post_period")), _dict(data.get("event_period"))
    if not post_p:
        post_p = ev_p or {"start": data.get("start_date"), "end": data.get("end_date")}
    pre_days = _count(pre_p.get("days")) or _count(data.get("pre_period_days"))
    post_days = _count(post_p.get("days")) or _count(data.get("post_period_days")) or _count(data.get("attribution_window_days"))
    pre_range = _fmt_range(pre_p.get("start"), pre_p.get("end")) if pre_p.get("start") and pre_p.get("end") else ""
    post_range = _fmt_range(post_p.get("start"), post_p.get("end")) if post_p.get("start") and post_p.get("end") else ""
    ev_start = str(ev_p.get("start") or data.get("start_date") or "")
    from_launch = bool(ev_start) and str(post_p.get("start") or "") <= ev_start
    after_word = "after launch" if from_launch else "after the campaign"
    before_word = "before launch" if from_launch else "before the campaign"

    yearly = data.get("yearly_breakdown") or []
    has_yearly = isinstance(yearly, list) and len(yearly) >= 2

    # Always project up (Jenna 2026-09-23): the raw observed n never
    # prints on a client-facing surface; the projected count is the only
    # sample figure a reader sees.
    src = "Source: Crosswalk. " + (f"{projected:,} projected US people" if projected else "Projected US people")
    if pre_range and post_range:
        src += f", {pre_range} and {post_range}."
    elif post_range:
        src += f", {post_range}."
    else:
        src += "."

    return DeckCtx(
        project=project,
        brand=brand,
        talent=talent,
        category_label=cat_lbl,
        is_auto=is_auto,
        cover_bg=_cover_photo_background(image_url, s3_client=s3_client) if image_url else None,
        logo_white=_render_crosswalk_logo_png(on_dark=True),
        logo_black=_render_crosswalk_logo_png(on_dark=False),
        total_value=total,
        emv=emv,
        bev=bev,
        blv=blv,
        cv=cv,
        lenses=lenses,
        emv_rows=emv_rows,
        incr_users=_count(val.get("incremental_users")),
        audience_proj=projected,
        pre_users_proj=_count(totals.get("pre_users_projected")),
        post_users_proj=_count(totals.get("post_users_projected")),
        pre_pen=pre_pen,
        post_pen=post_pen,
        delta_pp=delta_pp,
        delta_rel=delta_rel,
        c_pre_pen=c_pre,
        c_post_pen=c_post,
        c_delta_pp=c_delta,
        lift_pp=lift_pp,
        pre_days=pre_days,
        post_days=post_days,
        pre_range=pre_range,
        post_range=post_range,
        after_word=after_word,
        before_word=before_word,
        has_conversions=has_conv,
        has_yearly=has_yearly,
        has_control=has_control,
        open_window=_window_open(data, post_p),
        site=_brand_site(data, brand),
        draft=is_draft(data),
        sources_line=src,
        data=data,
    )


def _is_counterfactual(ctx: DeckCtx) -> bool:
    return "counterfactual" in (ctx.project or "").lower()


def _moved(ctx: DeckCtx) -> bool:
    """Did engagement move in the partnership's favor: against the days
    before, or against the matched group when one was bought."""
    return (ctx.lift_pp if ctx.has_control else ctx.delta_pp) > 0 and not _is_counterfactual(ctx)


def _days_phrase(n: int) -> str:
    """'51 days'; 'this window' when the valuation carries no length."""
    return f"{n} days" if n and n != 1 else ("1 day" if n == 1 else "this window")


def _the_days(n: int) -> str:
    """'the 51 days'; 'this window'."""
    return f"the {_days_phrase(n)}" if n else "this window"


def _same_days(n: int) -> str:
    """'the same 51 days'; 'the same window'."""
    return f"the same {_days_phrase(n)}" if n else "the same window"


def _lens_words(ctx: DeckCtx) -> str:
    names = [l.lower() for l, _v in ctx.lenses]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else (names[0] if names else "")


def _top_lens(ctx: DeckCtx) -> tuple:
    return max(ctx.lenses, key=lambda lv: lv[1]) if ctx.lenses else ("", 0.0)


def _campaign_ctx(ctx: DeckCtx) -> dict:
    try:
        from migration import bpiq_campaign_assets as _ca
        return _ca.context_from_payload(ctx.data)
    except Exception:
        return {}


def _touchpoints(ctx: DeckCtx, limit: int = 7) -> list:
    """The touchpoint table's rows: name, visits before, visits after,
    and whether the page is the campaign's own. A page the campaign
    created has no visits before the campaign went public, whatever the
    saved valuation says (2026-11-11: 2,211 visits to a collection page
    in the 51 days before it existed)."""
    post = [r for r in (ctx.data.get("top_brand_properties") or []) if isinstance(r, dict)]
    pre = [r for r in (ctx.data.get("top_brand_properties_pre") or []) if isinstance(r, dict)]
    pre_map = {str(p.get("common_name") or "").lower(): _count(p.get("hits_projected")) for p in pre}
    try:
        from migration import bpiq_campaign_assets as _ca
        kw = _ca.context_from_payload(ctx.data)
    except Exception:
        _ca, kw = None, {}
    rows = []
    for r in sorted(post, key=lambda r: -_count(r.get("hits_projected"))):
        name = " ".join(str(r.get("common_name") or "").split())
        if not name:
            continue
        before = pre_map.get(name.lower(), 0)
        new = bool(r.get("new_in_window"))
        if not new and _ca is not None:
            try:
                new = _ca.no_history_before(name, flag=r.get("new"), **kw)
            except Exception:
                new = False
        if new:
            before = 0
        rows.append({"name": name, "before": before, "after": _count(r.get("hits_projected")), "new": new})
    return rows[:limit] if limit else rows


def _next_step(ctx: DeckCtx) -> tuple:
    """(the short version, the fuller version) of what to do next, read
    off the valuation: where the value sits, whether engagement moved,
    and whether orders have followed."""
    days = _days_phrase(ctx.post_days)
    top = ctx.emv_rows[0] if ctx.emv_rows else None
    channel = _channel_in_sentence(top["name"], ctx.brand, ctx.site) if top else "the earned channels"
    own = next((r["name"] for r in _touchpoints(ctx, 0) if r["new"]), "")
    where = own or f"{ctx.b}'s product pages"
    if not _moved(ctx):
        return (
            f"Change the creative or the channel mix before the next flight. {_cap(ctx.ba)} engagement did not move in this window.",
            f"Hold spend on this plan. Test new creative or a new channel mix, then read {_same_days(ctx.post_days)} again.",
        )
    smallest = min(ctx.lenses, key=lambda lv: lv[1])[0] if ctx.lenses else ""
    if ctx.has_conversions and smallest == "Conversion" and ctx.total_value and ctx.cv / ctx.total_value < 0.10:
        return (
            f"Keep {channel} running and point it at {where}. Attention is high and orders are still few.",
            f"Use the next {days if ctx.post_days else 'window'} to turn this attention into orders. Lead with {channel} and send people to {where}.",
        )
    return (
        f"Put the next flight behind {channel}, the largest source of earned value here.",
        f"Lead the next flight with {channel}, then read {_same_days(ctx.post_days)} again to see if the gain holds.",
    )


def _final_statement(ctx: DeckCtx) -> str:
    """The one sentence the closing page states: twelve words or fewer,
    with the names in full when they fit and in short when they do not."""
    who = ctx.talent or "The partnership"
    if ctx.total_value <= 0:
        options = [f"{ctx.brand} engagement did not move in this window.", "Brand engagement did not move in this window."]
    elif _is_counterfactual(ctx):
        options = [f"{ctx.brand} was not in the partnership, and its engagement barely moved.",
                   "This brand was not in the partnership, and its engagement barely moved."]
    elif ctx.has_control and ctx.lift_pp > 0:
        options = [f"{who} moved {ctx.brand} engagement past a matched group.",
                   f"{who} moved {ctx.ba} engagement past a matched group.",
                   "The partnership moved brand engagement past a matched group."]
    elif ctx.has_control:
        options = [f"{ctx.brand} engagement moved no more than the matched group.",
                   "Brand engagement moved no more than the matched group."]
    elif ctx.delta_pp > 0:
        options = [f"{who} got more people engaging with {ctx.brand}.",
                   f"{who} got more people engaging with {ctx.b}.",
                   "The partnership got more people engaging with the brand."]
    else:
        options = [f"{ctx.brand} engagement did not rise in this window.", "Brand engagement did not rise in this window."]
    if len(ctx.brand.split()) >= 5 or ctx.brand == NO_BRAND:
        options = [o for o in options if ctx.brand not in o] or options[-1:]
    for o in options:
        if len(o.split()) <= 12:
            return o
    return options[-1]


# ─────────────────────────────────────────────────────────────────────
#  Page parts shared by more than one page.
# ─────────────────────────────────────────────────────────────────────
def _stat_card(slide, surf: Surf, x, y, w, h, label, value, caption, *, hero: bool = False,
               value_size: Optional[int] = None):
    """A card that carries one figure: a caps label, the figure, a line
    under it. `hero` is the one dark card on a light page."""
    if hero and not surf.dark:
        fill, c_label, c_value, c_cap = GRAPHITE, _rgb("9BAAAC"), SIGNAL, _rgb("A8AFA9")
    else:
        fill, c_label, c_value, c_cap = surf.card, surf.card_label, surf.card_title, surf.card_body
    _card(slide, x, y, w, h, fill=fill)
    pad = 0.26
    compact = h < 1.35          # a short card sets its three lines closer
    _label(slide, x + pad, y + (0.14 if compact else 0.22), w - 2 * pad, label, c_label)
    size = value_size or _fit_stat(value, w - 2 * pad, 34, floor=14)
    vh = _line_h_in(size, 0.95)
    vy = y + (0.36 if compact else 0.56)
    T(slide, x + pad, vy, w - 2 * pad, vh, value, size=size, weight="Bold", color=c_value, spacing=0.95)
    if caption:
        cy = vy + vh + (0.03 if compact else 0.14)
        room = y + h - (0.08 if compact else 0.2) - cy
        if room >= 0.17:
            BODY(slide, x + pad, cy, w - 2 * pad, caption, max_h=room, size=10.5 if compact else 11.5, floor=10,
                 spacing=1.25, min_spacing=1.05, color=c_cap)


def _stat_card_height(w: float, value_size: float, captions: list) -> float:
    """The height a row of stat cards needs: label, figure, and the
    tallest caption, with the card ending just under its last line."""
    cap_h = max([_measure(c, 11.5, w - 0.52, spacing=1.25).height_in for c in captions if c] or [0.0])
    return round(0.56 + _line_h_in(value_size, 0.95) + (0.14 + cap_h if cap_h else 0.0) + 0.26, 3)


def _label_fit(text: str, sizes, width_in: float) -> tuple:
    """(size, box width) for a row label: on one line at the largest size
    that holds it; failing that on two lines that do not end on a lone
    word, at the full width or a narrower one. The finisher's widow net
    then has nothing to do."""
    for sz in sizes:
        if _measure(text, sz, width_in).lines <= 1:
            return sz, width_in
    for sz in sizes:
        w = width_in
        while w >= width_in * 0.6:
            if _measure(text, sz, w).lines <= 2 and not _lone_last_word(text, sz, w):
                return sz, round(w, 3)
            w -= 0.05
    return sizes[-1], width_in


def _calc_card(slide, surf: Surf, x, y, w, rows, total_label: str, total_value: str) -> float:
    """The arithmetic behind a value, as a card: each row a label and a
    figure, a rule, then the total. The rows multiply (or subtract) out
    to the total printed under them. Returns the card's bottom edge."""
    pad = 0.3
    row_h = 0.62 if len(rows) <= 3 else 0.52
    h = 0.22 + len(rows) * row_h + 0.16 + 0.92
    _card(slide, x, y, w, h, fill=surf.card)
    lab_w = (w - 2 * pad) * 0.62
    val_w = (w - 2 * pad) - lab_w
    ry = y + 0.22
    for i, (label, value) in enumerate(rows):
        lab_size, lab_fit_w = _label_fit(label, (12, 11.5, 11, 10.5, 10), lab_w)
        T(slide, x + pad, ry, lab_fit_w, row_h, label, size=lab_size, color=surf.card_body, anchor="middle", spacing=1.1)
        T(slide, x + pad + lab_w, ry, val_w, row_h, value,
          size=_fit_stat(value, val_w, 20, floor=12), weight="Bold", color=surf.card_title,
          align="right", anchor="middle")
        ry += row_h
        if i < len(rows) - 1:
            _rule(slide, x + pad, ry, w - 2 * pad, surf.rule)
    ry += 0.08
    _rule(slide, x + pad, ry, w - 2 * pad, surf.card_accent)
    ry += 0.16
    _label(slide, x + pad, ry + 0.24, lab_w * 0.75, total_label, surf.card_label)
    tot_w = (w - 2 * pad) * 0.6
    T(slide, x + w - pad - tot_w, ry, tot_w, 0.62, total_value,
      size=_fit_stat(total_value, tot_w, 30, floor=18), weight="Bold", color=surf.card_accent,
      align="right", anchor="middle")
    return y + h


def _note_card(slide, surf: Surf, x, y, w, h, label: str, body: str, *, size: float = 11.5,
               hug: bool = False):
    """A reading beside a figure: an outlined card, a caps label, the
    plain account. The body fits the card or loses its last sentence.
    With `hug` the card ends just under its text instead of filling
    `h` (which is then the most it may take)."""
    pad = 0.26
    if hug:
        sz, sp, t, m = _fit_body(body, w - 2 * pad, h - 0.56 - 0.2, size=size, floor=10, spacing=1.3, min_spacing=1.15)
        h = min(h, round(0.56 + m.height_in + 0.3, 3))
    _card(slide, x, y, w, h, fill=None, line=surf.rule, line_w=0.75)
    _label(slide, x + pad, y + 0.22, w - 2 * pad, label, surf.accent)
    BODY(slide, x + pad, y + 0.56, w - 2 * pad, body, max_h=h - 0.56 - 0.2, size=size, floor=10,
         spacing=1.3, min_spacing=1.15, color=surf.title if not surf.dark else surf.card_title)


def _reading(slide, surf: Surf, y: float, text: str, *, bottom: float = CONTENT_BOTTOM):
    """The plain line a page ends on."""
    if text and bottom - y > 0.2:
        BODY(slide, M, y, BAND, text, max_h=bottom - y, size=12, floor=10, spacing=1.25, min_spacing=1.1,
             color=surf.body)


def _rate(ctx: DeckCtx, key: str) -> float:
    return _num(_dict(_dict(ctx.data.get("valuation")).get("rates")).get(key))


def _base_for(value: float, rate: float, stated: int) -> int:
    """The count a value was priced on. The valuation's own count when it
    multiplies out to the value; otherwise the count the value implies at
    the rate, so the arithmetic on the page holds."""
    if rate > 0 and abs(stated * rate - value) > 0.011:
        return int(round(value / rate))
    return int(stated)


# ─────────────────────────────────────────────────────────────────────
#  Slide builders. Each takes (prs, ctx, idx, total) and returns the
#  new slide.
# ─────────────────────────────────────────────────────────────────────
def _cover_claim(ctx: DeckCtx, width_in: float = 9.6) -> str:
    """The claim line under the pairing: what the deck is about, in
    plain words. The first wording that holds two lines is taken, so a
    long pairing gets the shorter claim rather than a cut one."""
    options = []
    if _is_counterfactual(ctx):
        options.append(f"A control read of {ctx.brand}, a brand outside the partnership.")
        options.append("A control read of a brand outside the partnership.")
    else:
        if ctx.talent and ctx.post_days:
            options.append(f"A look at the value {ctx.talent} brought to {ctx.brand} in {_days_phrase(ctx.post_days)}.")
        if ctx.talent:
            options.append(f"A look at the value {ctx.talent} brought to {ctx.brand}.")
        if ctx.brand != NO_BRAND:
            options.append(f"A look at what the partnership was worth to {ctx.brand}.")
        options.append("A look at what the partnership was worth.")
    for o in options:
        if _measure(o, 20, width_in, spacing=1.15).lines <= 2:
            return o
    return options[-1]


def _slide_cover(prs, ctx: DeckCtx, idx: int, total: int):
    """The cover: the pairing in the product's own order, a plain claim
    line, the window, and three figures. A photo, when the valuation has
    one, is the page background under a baked scrim; otherwise the
    pack's Graphite wash."""
    s = _blank(prs)
    surf = S_DARK
    pack = _design()
    photo = False
    if ctx.cover_bg and pack is not None:
        try:
            pack.set_slide_background_image(s, ctx.cover_bg)
            photo = True
        except Exception:
            photo = False
    if not photo:
        done = False
        try:
            wash = pack.gradient_image_bytes("graphiteWash") if pack is not None else None
            if wash:
                pack.set_slide_background_image(s, wash)
                done = True
        except Exception:
            done = False
        if not done:
            _add_bg(s, GRAPHITE)
    body = _rgb("C9CCC6") if photo else surf.body
    if ctx.logo_white:
        try:
            s.shapes.add_picture(ctx.logo_white, Inches(M), Inches(0.72), width=Inches(2.1))
        except Exception:
            pass
    T(s, M, 2.34, 9.0, 0.24, "BRAND PARTNERSHIP VALUATION", size=10.5, weight="SemiBold", color=surf.accent,
      letter_spacing=2.6, anchor="middle")
    title = ctx.project
    size = 32
    for sz in (60, 54, 48, 44, 40, 36, 32):
        lines = _line_count(title, sz, BAND, bold=True)
        if lines <= 1 or (sz <= 44 and lines <= 2):
            size = sz
            break
    t_h = _measure(title, size, BAND, bold=True, spacing=0.95).height_in
    T(s, M, 2.72, BAND, t_h, title, size=size, weight="Bold", color=surf.title, spacing=0.95)
    y = 2.72 + t_h + 0.2
    _shape, c_h = BODY(s, M, y, 9.6, _cover_claim(ctx), max_h=max(0.4, 5.1 - y - 0.4), size=20, floor=16,
                       spacing=1.15, weight="Light", color=surf.title)
    y += c_h + 0.12
    window = _fmt_range(ctx.data.get("start_date"), ctx.data.get("end_date"), long=True) \
        if ctx.data.get("start_date") and ctx.data.get("end_date") else ctx.post_range
    if window and y < 5.0:
        T(s, M, y, 9.6, 0.28, window + ".", size=13, color=body)
    _rule(s, M, 5.318, BAND, surf.rule)
    stats = [
        (fmt_money(ctx.total_value), "Total brand value"),
        (fmt_num(ctx.audience_proj), "People reached in the US"),
        (f"{ctx.pre_pen:.1f}% to {ctx.post_pen:.1f}%", f"{_cap(ctx.ba)} engagement, before and after"),
    ]
    st_size = min(_fit_stat(v, COL3 - 0.1, 26, floor=14) for v, _l in stats)
    for i, (value, label) in enumerate(stats):
        x = M + i * (COL3 + GUT)
        T(s, x, 5.56, COL3, 0.46, value, size=st_size, weight="Bold", color=surf.accent if i == 0 else surf.title)
        T(s, x, 6.08, COL3, 0.3, _one_line(label, 11.5, COL3), size=11.5, color=body)
    foot = "CONFIDENTIAL"
    if ctx.draft:
        created = str(ctx.data.get("created_at") or "")[:10]
        foot = "CONFIDENTIAL \u00b7 DRAFT" + (f" {created}" if created else "")
    T(s, M, FOOT_Y, 8.2, 0.22, foot, size=8, color=surf.footer, letter_spacing=1.6)
    _page_number(s, RIGHT - 2.2, FOOT_Y, 2.2, 0.22, surf.footer, page=idx)
    return s


def _reach_sentence(ctx: DeckCtx) -> str:
    """Who the read follows, in one sentence."""
    basis = str(_dict(ctx.data.get("campaign")).get("audience_basis") or "")
    n = fmt_num(ctx.audience_proj)
    if basis == "campaign reach":
        return f"The campaign reached {n} people in the US in {_days_phrase(ctx.post_days)}."
    if ctx.talent:
        return f"The read follows {n} people in the US who engaged with {ctx.talent}."
    return f"The read follows {n} people in the US."


def _tldr_rows(ctx: DeckCtx) -> list:
    """The short version: what we found, why it matters, what to do."""
    rows = []
    found = _reach_sentence(ctx)
    if abs(ctx.delta_pp) < 0.05:
        found += f" The share of them engaging with {ctx.b} held at {ctx.post_pen:.1f}%."
    else:
        found += (f" The share of them engaging with {ctx.b} went from "
                  f"{ctx.pre_pen:.1f}% to {ctx.post_pen:.1f}%.")
    if ctx.has_control:
        found += f" A matched group moved from {ctx.c_pre_pen:.1f}% to {ctx.c_post_pen:.1f}% over the same days."
    rows.append(("What we found", found))
    lens, value = _top_lens(ctx)
    if ctx.total_value > 0 and lens:
        cents = value / ctx.total_value * 100
        where = f"{lens} carries {fmt_money(value)} of the total, or {cents:.0f} cents of every dollar."
        if lens == "Earned media" and ctx.emv_rows:
            top = ctx.emv_rows[0]
            where += f" {_cap(_channel_in_sentence(top['name'], ctx.brand, ctx.site))} alone accounts for {fmt_money(top['value'])}."
        rows.append(("Where the value sits", where))
    if ctx.blv > 0:
        if ctx.has_control:
            new = f"Brand lift is worth {fmt_money(ctx.blv)}. It is the gain the matched group did not make."
        else:
            # (2026-11-11: "the part of the total the partnership added" read as a second
            # answer beside the talent's share on the total page)
            new = (f"New engagement is worth {fmt_money(ctx.blv)}. It prices only the people who "
                   f"began engaging with {ctx.b} {ctx.after_word}.")
        rows.append(("What is new", new))
    if ctx.has_conversions:
        orders = fmt_num(_base_for(ctx.cv, _rate(ctx, "conv_value_per_user"),
                                   _count(_dict(ctx.data.get("conversions")).get("post_users_projected"))))
        if min(ctx.lenses, key=lambda lv: lv[1])[0] == "Conversion":
            # the headline already says "so far" on an open window; the row does not say it twice
            part = "Sales are the smallest part." if ctx.open_window else "Sales are the smallest part so far."
            sold = f"{part} We count {orders} orders worth {fmt_money(ctx.cv)}."
        else:
            sold = f"Orders add {fmt_money(ctx.cv)}. We count {orders} of them in the window."
        rows.append(("What sold", sold))
    rows.append(("What to do", _next_step(ctx)[0]))
    return rows[:5]


def _slide_tldr(prs, ctx: DeckCtx, idx: int, total: int):
    """The short version, up front: someone who reads only this page
    leaves with the answer."""
    s = _blank(prs)
    surf = S_LIGHT
    _ground(s, surf)
    who = f"{ctx.talent}'s partnership" if ctx.talent else "The partnership"
    if ctx.total_value > 0 and ctx.open_window:
        # the campaign is still running, or the window ends the day it was valued: the value so far
        # (2026-11-11: "delivered" read as a closed account of a partnership that was still running)
        title = ctx.titles([
            (f"{who} is worth {fmt_money(ctx.total_value)}", f" to {ctx.brand} so far."),
            (f"The partnership is worth {fmt_money(ctx.total_value)}", " so far."),
        ])
    elif ctx.total_value > 0:
        title = ctx.titles([
            (f"{who} delivered {fmt_money(ctx.total_value)}", f" in brand value for {ctx.brand}."),
            (f"The partnership delivered {fmt_money(ctx.total_value)}", " in brand value."),
        ])
    else:
        title = ("The partnership ran", " and the value did not move.")
    sub = f"The short version of {_days_phrase(ctx.post_days)}" + (f", {ctx.post_range}." if ctx.post_range else ".")
    top = _head(s, surf, idx, "The short version", title, sub, ctx.logo(surf))
    rows = _tldr_rows(ctx)
    body_x, body_w = 3.7, RIGHT - 3.7
    gap = 0.3
    room = CONTENT_BOTTOM - top - 0.05
    fitted = _fit_stack(
        [dict(text=body, width_in=body_w, size=16, floor=11, spacing=1.25, min_spacing=1.12) for _l, body in rows],
        room, gap)
    # the rows share whatever room is left, so the page ends near the floor
    spare = room - (sum(h + 0.02 for _a, _b, _c, h in fitted) + gap * (len(rows) - 1))
    if spare > 0 and len(rows) > 1:
        gap += min(0.34, spare / (len(rows) - 1))
    y = top + 0.05
    for i, ((label, _body), (sz, sp, txt, h_in)) in enumerate(zip(rows, fitted)):
        _label(s, M, y + 0.07, 2.6, label, surf.body)
        T(s, body_x, y, body_w, round(h_in + 0.02, 3), txt, size=sz, color=surf.title, spacing=sp)
        y += h_in + 0.02
        if i < len(rows) - 1:
            _rule(s, M, y + gap / 2 - 0.005, BAND, surf.rule)
        y += gap
    return s


def _campaign_account(ctx: DeckCtx) -> str:
    """The campaign in plain words, from the valuation's own fields: who
    it reached and how, what the pieces cost, what a typical order came
    to. Every figure is exact; no outside name appears."""
    camp = _dict(ctx.data.get("campaign"))
    out = []
    lines = [ln for ln in (_dict(camp.get("reach")).get("lines") or [])
             if isinstance(ln, dict) and _count(ln.get("people")) > 0 and str(ln.get("line") or "") != "search"]
    if lines:
        how = {"collection pages": "on the collection pages", "press": "through press",
               "the film and the launch posts": "through the film and the launch posts"}
        parts = [f"{fmt_num(_count(ln['people']))} people {how.get(str(ln['line']), 'around ' + str(ln['line']))}"
                 if i == 0 else f"{fmt_num(_count(ln['people']))} {how.get(str(ln['line']), 'around ' + str(ln['line']))}"
                 for i, ln in enumerate(lines[:4])]
        joined = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
        first = _count(lines[0]["people"])
        if len(lines) > 1 and ctx.audience_proj and first == ctx.audience_proj:
            # one line is the whole group (2026-11-11: "319,506 through press and 16,415 around the
            # launch event" read as two groups that add up to more than the people reached)
            also = [f"{fmt_num(_count(ln['people']))} of them were also reached "
                    f"{how.get(str(ln['line']), 'around ' + str(ln['line']))}" for ln in lines[1:4]]
            out.append(f"All {fmt_num(first)} people were reached {how.get(str(lines[0]['line']), 'around ' + str(lines[0]['line']))}. "
                       + _cap(", and ".join(also)) + ".")
        else:
            out.append(f"It reached {joined}. Nobody is counted twice.")
    # The talent's own audience, when the valuation carries it beside the
    # reach (2026-11-11): the two counts measure different things, and
    # naming both once keeps the deck and the read it follows from
    # looking like two answers to one question.
    held = _count(camp.get("talent_audience_held"))
    if held > ctx.audience_proj > 0 and ctx.talent:
        out.append(f"{ctx.talent}'s projected US audience is {fmt_num(held)}. The campaign reached a part of it.")
    band = _dict(camp.get("price_band"))
    lo, hi = _num(band.get("low_usd")), _num(band.get("high_usd"))

    def price(v):
        return f"${v:,.0f}" if abs(v - round(v)) < 0.005 else f"${v:,.2f}"
    if lo and hi and hi > lo:
        out.append(f"Pieces in the collection ran {price(lo)} to {price(hi)}.")
    aov = _num(camp.get("aov_usd")) or _rate(ctx, "conv_value_per_user")
    if aov and ctx.has_conversions:
        out.append(f"A typical order came to {fmt_money(aov)}.")
    if camp.get("sold_out"):
        out.append("Most of the collection sold out inside the window." if camp.get("partial_sell_out")
                   else "The collection sold out inside the window.")
    study = _safe_prose(_dict(ctx.data.get("diagnostics")).get("study_context"), brand=ctx.brand, talent=ctx.talent)
    if study:
        out.append(study)
    return " ".join(out)


def _slide_measured(prs, ctx: DeckCtx, idx: int, total: int):
    """Who the read follows and when: the people, the two windows, and
    the matched group when one was bought."""
    s = _blank(prs)
    surf = S_DARK
    _ground(s, surf)
    launch = "the launch" if ctx.after_word == "after launch" else "the campaign"
    if ctx.has_control:
        title = ("We compared the people reached", " with a matched group.")
    else:
        title = ("We followed the same people", f" before and after {launch}.")
    sub = (f"{fmt_num(ctx.audience_proj)} people in the US, {_days_phrase(ctx.pre_days)} before "
           f"and {_days_phrase(ctx.post_days)} after.")
    top = _head(s, surf, idx, "Who and when", title, sub, ctx.logo(surf), source=ctx.sources_line)
    basis = str(_dict(ctx.data.get("campaign")).get("audience_basis") or "")
    who = "the people the campaign reached" if basis == "campaign reach" or not ctx.talent \
        else f"people who engaged with {ctx.talent}"
    para1 = ("Crosswalk sees the full digital life of 30M+ opted-in US consumers. "
             f"For this read we followed {who} through two windows. "
             f"We counted what they did: the sites they visited, the apps they used, "
             f"and the {ctx.ba} pages and posts in their sessions.")
    para2 = _campaign_account(ctx)
    blocks = [dict(text=para1, width_in=COL2, size=13, floor=11, spacing=1.3, min_spacing=1.15)]
    if para2:
        blocks.append(dict(text=para2, width_in=COL2, size=11.5, floor=10, spacing=1.3, min_spacing=1.15))
    fitted = _fit_stack(blocks, CONTENT_BOTTOM - top, 0.24)
    y = top
    for k, (sz, sp, txt, h_in) in enumerate(fitted):
        T(s, M, y, COL2, round(h_in + 0.02, 3), txt, size=sz, color=surf.title if k == 0 else surf.body, spacing=sp)
        y += h_in + 0.02 + 0.24
    cards = [("People reached", fmt_num(ctx.audience_proj), "Projected US people")]
    if ctx.has_control:
        cg = _dict(ctx.data.get("control_group"))
        size = _count(cg.get("projected_control_size")) or ctx.audience_proj
        cards.append(("Matched group", fmt_num(size), "Same size, never reached"))
    if ctx.pre_range:
        cards.append((ctx.before_word, ctx.pre_range, _days_phrase(ctx.pre_days)))
    if ctx.post_range:
        cards.append((ctx.after_word, ctx.post_range, _days_phrase(ctx.post_days)))
    x0 = M + COL2 + GUT
    room = CONTENT_BOTTOM - top
    if len(cards) >= 4:
        cw = (COL2 - 0.2) / 2
        ch = (room - 0.2) / 2
        for i, (label, value, cap) in enumerate(cards[:4]):
            # the two cards of a row share a size: counts above, dates below
            pair = cards[(i // 2) * 2:(i // 2) * 2 + 2]
            size = min(_fit_stat(v, cw - 0.52, 24, floor=12) for _l, v, _c in pair)
            _stat_card(s, surf, x0 + (i % 2) * (cw + 0.2), top + (i // 2) * (ch + 0.2), cw, ch, label, value, cap,
                       value_size=size)
    else:
        n = max(1, len(cards))
        ch = (room - 0.18 * (n - 1)) / n
        size = min(_fit_stat(v, COL2 - 0.52, 22, floor=12) for _l, v, _c in cards)
        for i, (label, value, cap) in enumerate(cards):
            _stat_card(s, surf, x0, top + i * (ch + 0.18), COL2, ch, label, value, cap if ch >= 0.98 else "",
                       value_size=size)
    return s


ENGAGING_MEANS = "Engaging means a visit, a view or a follow. It does not mean an order."


def _slide_audience_scale(prs, ctx: DeckCtx, idx: int, total: int):
    """Reach and value: the people, how many engaged, what it is worth."""
    s = _blank(prs)
    surf = S_LIGHT
    _ground(s, surf)
    title = ctx.titles([
        (f"{_cap(_about_share(ctx.post_pen))} the people reached", f" engaged with {ctx.brand}."),
        (f"{_cap(_about_share(ctx.post_pen))} the people reached", " engaged with the brand."),
    ])
    sub = f"Projected US counts{', ' + ctx.post_range if ctx.post_range else ''}."
    top = _head(s, surf, idx, "Reach and value", title, sub, ctx.logo(surf), source=ctx.sources_line)
    basis = str(_dict(ctx.data.get("campaign")).get("audience_basis") or "")
    reach_cap = (f"Everyone in the US the campaign reached in {_days_phrase(ctx.post_days)}."
                 if basis == "campaign reach" else "Everyone in the US this read follows.")
    cards = [
        ("People reached", fmt_num(ctx.audience_proj), reach_cap, False),
        (f"Engaged with {ctx.b}", fmt_num(ctx.post_users_proj),
         f"{ctx.post_pen:.1f}% of the people reached, counted {ctx.after_word}.", False),
        ("Total brand value", fmt_money(ctx.total_value),
         f"The sum of the {_n_word(len(ctx.lenses))} parts on the total value page.", True),
    ]
    size = min(_fit_stat(v, COL3 - 0.52, 34, floor=14) for _l, v, _c, _h in cards)
    h = min(_stat_card_height(COL3, size, [c for _l, _v, c, _h in cards]), CONTENT_BOTTOM - top - 1.5)
    for i, (label, value, cap, hero) in enumerate(cards):
        _stat_card(s, surf, M + i * (COL3 + GUT), top, COL3, h, _one_line(label, 9.5, COL3 - 0.52, tracking=1.0, bold=True),
                   value, cap, hero=hero, value_size=size)
    # the headline as a picture: of everyone reached, the share who engaged;
    # under the bar, what the word means, where the reader first meets the
    # share (2026-11-11); then the page's plain line. The three share the
    # room under the cards, so a two-line headline keeps all of them.
    reading = f"What this means: every share in this deck is a share of these {fmt_num(ctx.audience_proj)} people."
    d_h = _measure(ENGAGING_MEANS, 12, BAND, spacing=1.25).height_in
    r_h = _measure(reading, 12, BAND, spacing=1.25).height_in
    y = top + h
    share = max(0.0, min(ctx.post_pen, 100.0))
    bar = CONTENT_BOTTOM - y >= 0.2 + 0.54 + 0.12 + d_h + 0.06 + r_h
    spare = CONTENT_BOTTOM - y - (0.54 if bar else 0.0) - d_h - r_h
    g_bar = min(0.34, max(0.2, spare * 0.45))
    g_def = min(0.18, max(0.12, spare * 0.3))
    g_read = min(0.14, max(0.06, spare * 0.2))
    if bar:
        y += g_bar
        _label(s, M, y, 8.0, f"Share of people reached who engaged {ctx.after_word}", surf.card_label)
        T(s, RIGHT - 2.0, y - 0.06, 2.0, 0.28, f"{share:.1f}%", size=14, weight="Bold", color=surf.title, align="right")
        _card(s, M, y + 0.3, BAND, 0.24, fill=surf.rule, radius=0.12)
        if share > 0:
            _card(s, M, y + 0.3, max(0.24, BAND * share / 100.0), 0.24, fill=surf.accent, radius=0.12)
        y += 0.3 + 0.24
    y += g_def if bar else 0.3
    if CONTENT_BOTTOM - y >= d_h:
        T(s, M, y, BAND, round(d_h + 0.02, 3), ENGAGING_MEANS, size=12, color=surf.title, spacing=1.25)
        y += d_h + g_read
    _reading(s, surf, y, reading)
    return s


def _plate(slide, surf: Surf, x, y, w, h, label, pct, count_line, window_line, *, lit: bool, fig: int = 60):
    """One side of the before and after pair. The lit plate is the page's
    one Signal Green tile."""
    if lit:
        fill, c_label, c_fig, c_line = SIGNAL, GRAPHITE, GRAPHITE, GRAPHITE
    else:
        fill, c_label, c_fig, c_line = surf.card, surf.card_accent, surf.card_title, surf.card_body
    _card(slide, x, y, w, h, fill=fill)
    pad = 0.3
    _label(slide, x + pad, y + 0.22, w - 2 * pad, label, c_label)
    size = fig
    ph = _line_h_in(size, 0.95)
    T(slide, x + pad, y + 0.5, w - 2 * pad, ph, pct, size=size, weight="Bold", color=c_fig, spacing=0.95)
    cy = y + 0.5 + ph + 0.06
    T(slide, x + pad, cy, w - 2 * pad, 0.28, count_line, size=14, weight="SemiBold", color=c_fig)
    if window_line and cy + 0.3 + 0.2 <= y + h - 0.12:
        T(slide, x + pad, cy + 0.3, w - 2 * pad, 0.22, window_line, size=11, color=c_line)


def _lift_count_line(ctx: DeckCtx) -> str:
    """The count behind the move, from the two counts the page prints:
    how many more people engaged, and the rise that is (2026-11-11: the
    subhead described the share the headline had just stated). '' when
    engagement held or the valuation carries no counts."""
    pre, post = ctx.pre_users_proj, ctx.post_users_proj
    gain = post - pre
    if pre <= 0 or post <= 0 or abs(ctx.delta_pp) < 0.05 or gain == 0 or (gain > 0) != (ctx.delta_pp > 0):
        return ""
    if gain > 0:
        return (f"{fmt_num(gain)} more people engaged with {ctx.b} {ctx.after_word} than before it, "
                f"a {gain / pre * 100:.1f}% rise.")
    return (f"{fmt_num(-gain)} fewer people engaged with {ctx.b} {ctx.after_word} than before it, "
            f"a {-gain / pre * 100:.1f}% fall.")


def _slide_engagement_lift(prs, ctx: DeckCtx, idx: int, total: int):
    """Engagement before and after. With a matched group bought, its own
    move sits under the pair and the difference is stated flat."""
    s = _blank(prs)
    surf = S_DARK
    _ground(s, surf)
    if ctx.delta_pp >= 0.05:
        title = ctx.titles([(f"{ctx.brand} engagement rose", f" from {ctx.pre_pen:.1f}% to {ctx.post_pen:.1f}%."),
                             ("Brand engagement rose", f" from {ctx.pre_pen:.1f}% to {ctx.post_pen:.1f}%.")])
    elif ctx.delta_pp <= -0.05:
        title = ctx.titles([(f"{ctx.brand} engagement fell", f" from {ctx.pre_pen:.1f}% to {ctx.post_pen:.1f}%."),
                             ("Brand engagement fell", f" from {ctx.pre_pen:.1f}% to {ctx.post_pen:.1f}%.")])
    else:
        title = ctx.titles([(f"{ctx.brand} engagement held", f" at {ctx.post_pen:.1f}%, before and after."),
                             ("Brand engagement held", f" at {ctx.post_pen:.1f}%, before and after.")])
    if ctx.has_control:
        # the two groups start close, never identical, so the line says
        # only what the figures on the page bear out
        sub = (f"A matched group{' of the same size' if _same_size(ctx) else ''} moved from "
               f"{ctx.c_pre_pen:.1f}% to {ctx.c_post_pen:.1f}%.")
    else:
        sub = _lift_count_line(ctx) or (f"The share of the {fmt_num(ctx.audience_proj)} people reached who had a "
                                        f"{ctx.ba} touchpoint, window by window.")
    top = _head(s, surf, idx, "Engagement before and after", title, sub, ctx.logo(surf), source=ctx.sources_line)
    room = CONTENT_BOTTOM - top
    strip_h = 0.84 if ctx.has_control else 0.0
    plate_room = room - 0.62 - (strip_h + 0.18 if strip_h else 0.0)
    fig = 72 if plate_room >= 2.85 else (60 if plate_room >= 2.4 else 44)
    plate_h = min(plate_room, round(0.5 + _line_h_in(fig, 0.95) + 0.06 + 0.3 + 0.22 + 0.3, 3))
    _plate(s, surf, M, top, COL2, plate_h, f"{_days_phrase(ctx.pre_days)} {ctx.before_word}",
           f"{ctx.pre_pen:.1f}%", f"{fmt_num(ctx.pre_users_proj)} people", ctx.pre_range, lit=False, fig=fig)
    _plate(s, surf, M + COL2 + GUT, top, COL2, plate_h, f"{_days_phrase(ctx.post_days)} {ctx.after_word}",
           f"{ctx.post_pen:.1f}%", f"{fmt_num(ctx.post_users_proj)} people", ctx.post_range, lit=True, fig=fig)
    y = top + plate_h + 0.18
    if ctx.has_control:
        _card(s, M, y, BAND, strip_h, fill=surf.card)
        cells = [("People reached", f"{ctx.delta_pp:+.1f} points", surf.card_title),
                 ("Matched group", f"{ctx.c_delta_pp:+.1f} points", surf.card_title),
                 ("Lift over the matched group", f"{ctx.lift_pp:+.1f} points", surf.card_accent)]
        cw = (BAND - 0.6) / 3
        for i, (label, value, color) in enumerate(cells):
            cx = M + 0.3 + i * cw
            _label(s, cx, y + 0.16, cw - 0.2, label, surf.card_label)
            T(s, cx, y + 0.4, cw - 0.2, 0.34, value, size=18, weight="Bold", color=color)
        y += strip_h + 0.18
        if ctx.lift_pp > 0:
            read = (f"What this means: the people reached gained {ctx.lift_pp:.1f} points more than a group "
                    "like them that never saw the campaign.")
            if ctx.c_delta_pp > 0.05 and ctx.delta_pp / ctx.c_delta_pp >= 1.5:
                read += f" Their rise was {_times(ctx.delta_pp / ctx.c_delta_pp)} the matched group's."
        else:
            read = ("What this means: the matched group moved as much as the people reached, "
                    "so the rise is not the campaign's.")
    else:
        if ctx.delta_pp >= 0.05:
            read = (f"What this means: {_about_share(ctx.pre_pen, of='').strip()} engaged with {ctx.b} "
                    f"{ctx.before_word}, and {_about_share(ctx.post_pen, of='').strip()} did {ctx.after_word}.")
        else:
            read = (f"What this means: the same share of people engaged with {ctx.b} "
                    f"{ctx.before_word} and {ctx.after_word}.")
    _reading(s, surf, y, read)
    return s


def _slide_total_value_hero(prs, ctx: DeckCtx, idx: int, total: int):
    """The total, and the parts that add up to it. Every lens that is in
    the total has its tile on this page."""
    s = _blank(prs)
    surf = S_SIGNAL
    _ground(s, surf)
    lens, value = _top_lens(ctx)
    if ctx.total_value > 0 and value / ctx.total_value >= 0.5:
        title = ("Most of the value", f" is {lens.lower()}.")
    elif lens:
        title = (f"{lens} is the largest part", " of the value.")
    else:
        title = ("The value", " of the partnership.")
    top = _head(s, surf, idx, "Total value", title, None, ctx.logo(surf), source=ctx.sources_line)
    tiles_h = 0.92
    tiles_y = CONTENT_BOTTOM - tiles_h
    _label(s, M, top, BAND, "Total brand value" + (f" \u00b7 {ctx.post_range}" if ctx.post_range else ""), surf.title)
    hero = fmt_money(ctx.total_value)
    room = tiles_y - 0.2 - (top + 0.28)
    hero_size = _fit_stat(hero, BAND, 82 if room >= 2.9 else 60, floor=36)
    hero_h = _line_h_in(hero_size, 0.88)
    T(s, M, top + 0.28, BAND, hero_h, hero, size=hero_size, weight="Bold", color=surf.title, spacing=0.88)
    y2 = top + 0.28 + hero_h + 0.16
    # "Attributable to partnership" subtotal. 2026-09-01 house standard:
    # BLV + CV x min(1.0, max(0.0, adj_lift_pp / pre_baseline_pct)). See
    # migration/bpiq_attributable.py. With no comparison group it is the
    # brand lift value: the engagement that is new.
    from .bpiq_attributable import resolve_attributable_from_payload
    attributable = float(resolve_attributable_from_payload(
        ctx.data, fallback_blv=ctx.blv, fallback_cv=ctx.cv, fallback_has_conversions=ctx.has_conversions))
    if not ctx.has_control or attributable <= 0:
        attributable = ctx.blv
    val = _dict(ctx.data.get("valuation"))
    share = val.get("talent_share")
    has_share = share is not None and _num(share) > 0 and bool(ctx.talent)
    col_w = COL2 if has_share else BAND
    note_h = tiles_y - 0.14 - (y2 + 0.74)
    since = "since the campaign began"
    _label(s, M, y2, col_w, "Of which attributable to partnership" if ctx.has_control else f"Of which new {since}", surf.title)
    a_txt = fmt_money(attributable)
    T(s, M, y2 + 0.24, col_w, 0.44, a_txt, size=_fit_stat(a_txt, col_w, 26, floor=16), weight="Bold", color=surf.title)
    if note_h >= 0.2:
        if ctx.has_control:
            note = (f"Brand lift, plus the part of conversion value the lift accounts for. The rest prices "
                    f"people who already engaged with {ctx.b}.")
        else:
            note = (f"This is brand lift, the engagement that is new {since}. "
                    "The rest prices everyone who was reached.")
        BODY(s, M, y2 + 0.74, col_w - 0.3, note, max_h=note_h, size=11, floor=10, spacing=1.25, min_spacing=1.1,
             color=surf.body)
    if has_share:
        x2 = M + COL2 + GUT
        t_usd = round(ctx.total_value * _num(share), 2)
        _label(s, x2, y2, COL2, f"Of which {ctx.talent}'s share", surf.title)
        s_txt = f"{fmt_money(t_usd)} ({_num(share) * 100:.0f}%)"
        T(s, x2, y2 + 0.24, COL2, 0.44, s_txt, size=_fit_stat(s_txt, COL2, 26, floor=16), weight="Bold", color=surf.title)
        basis = _basis_sentences(val.get("talent_share_basis"), brand=ctx.brand, talent=ctx.talent,
                                 has_control=ctx.has_control)
        if basis and note_h >= 0.2:
            BODY(s, x2, y2 + 0.74, COL2 - 0.2, basis, max_h=note_h, size=11, floor=10, spacing=1.25,
                 min_spacing=1.1, color=surf.body)
    n = max(1, len(ctx.lenses))
    gap = 0.2
    tw = (BAND - gap * (n - 1)) / n
    size = min(_fit_stat(fmt_money(v), tw - 0.48, 20, floor=12) for _l, v in ctx.lenses) if ctx.lenses else 18
    for i, (label, v) in enumerate(ctx.lenses):
        x = M + i * (tw + gap)
        _card(s, x, tiles_y, tw, tiles_h, fill=surf.card)
        _label(s, x + 0.24, tiles_y + 0.16, tw - 0.48, label, surf.card_accent)
        T(s, x + 0.24, tiles_y + 0.42, tw - 0.48, 0.38, fmt_money(v), size=size, weight="Bold", color=surf.card_title)
    return s


def _slide_emv(prs, ctx: DeckCtx, idx: int, total: int):
    """Earned media, channel by channel. Every channel the valuation
    priced is in the table and the last row is their sum, which is the
    earned media value printed everywhere else in the deck. Rates are
    ours and carry no attribution."""
    s = _blank(prs)
    surf = S_LIGHT
    _ground(s, surf)
    rows = ctx.emv_rows
    if rows:
        title = _pick_title([(f"{rows[0]['display']} is the largest source", " of earned media."),
                             ("One channel is the largest source", " of earned media.")])
        sub = (f"{fmt_money(ctx.emv)} across {len(rows)} channel{'s' if len(rows) != 1 else ''}, "
               "each priced per new person reached.")
    else:
        title = ("No channel earned media", " in this window.")
        sub = "No channel showed new reach to price."
    top = _head(s, surf, idx, "Earned media", title, sub, ctx.logo(surf), source=ctx.sources_line)
    if not rows:
        return s
    tx, tw = M, 7.85
    c_name = (tx, 2.5)
    c_people = (tx + 2.5, 1.85)
    c_rate = (tx + 4.4, 1.6)
    c_value = (tx + 6.05, 1.8)
    for label, (x, w), align in (("Channel", c_name, "left"), ("New people reached", c_people, "right"),
                                 ("Value per person", c_rate, "right"), ("Earned media value", c_value, "right")):
        _label(s, x, top, w, label, surf.card_label, align=align, size=9)
    _rule(s, tx, top + 0.3, tw, surf.title)
    avail = CONTENT_BOTTOM - (top + 0.38) - 0.36
    pitch = max(0.19, min(0.3, avail / len(rows)))
    size = 11 if pitch >= 0.26 else (10.5 if pitch >= 0.215 else 10)
    y = top + 0.38
    for i, r in enumerate(rows):
        lead = i == 0
        T(s, c_name[0], y, c_name[1], pitch, _one_line(r["display"], size, c_name[1]), size=size,
          weight="SemiBold" if lead else None, color=surf.title, anchor="middle")
        T(s, c_people[0], y, c_people[1], pitch, fmt_num(r["people"]), size=size, color=surf.body,
          align="right", anchor="middle")
        T(s, c_rate[0], y, c_rate[1], pitch, f"${r['rate']:,.2f}", size=size, color=surf.body,
          align="right", anchor="middle")
        T(s, c_value[0], y, c_value[1], pitch, fmt_money(r["value"]), size=size,
          weight="SemiBold" if lead else None, color=surf.accent if lead else surf.title,
          align="right", anchor="middle")
        y += pitch
    _rule(s, tx, y + 0.04, tw, surf.title)
    T(s, c_name[0], y + 0.08, 4.0, 0.28, "Total earned media", size=11, weight="Bold", color=surf.title, anchor="middle")
    T(s, c_value[0] - 0.6, y + 0.08, c_value[1] + 0.6, 0.28, fmt_money(ctx.emv), size=11, weight="Bold",
      color=surf.title, align="right", anchor="middle")
    lead = rows[0]
    note = (f"Each new person {ctx.b} reached on a channel is priced at our 2026 rate for that channel.")
    if len(rows) >= 3 and lead["value"] > rows[1]["value"] + rows[2]["value"]:
        note += (f" {lead['display']} leads at {fmt_money(lead['value'])}, more than "
                 f"{rows[1]['display']} and {rows[2]['display']} put together.")
    elif len(rows) >= 2:
        note += (f" {lead['display']} leads at {fmt_money(lead['value'])}, ahead of "
                 f"{rows[1]['display']} at {fmt_money(rows[1]['value'])}.")
    if ctx.audience_proj and sum(r["people"] for r in rows) > ctx.audience_proj:
        note += (" One person can be reached on more than one channel, so the channel counts add up "
                 f"to more than the {fmt_num(ctx.audience_proj)} people in the read.")
    nx = tx + tw + GUT
    _note_card(s, surf, nx, top, RIGHT - nx, CONTENT_BOTTOM - top, "What this means", note, size=11, hug=True)
    return s


def _value_page(prs, ctx: DeckCtx, idx: int, surf: Surf, eyebrow: str, title, sub: str,
                rows: list, total_label: str, total_value: str, note: str):
    """One lens, priced: the arithmetic on the left, the reading on the
    right. Shared by the engagement, lift and conversion pages."""
    s = _blank(prs)
    _ground(s, surf)
    top = _head(s, surf, idx, eyebrow, title, sub, ctx.logo(surf), source=ctx.sources_line)
    calc_w = 7.0
    bottom = _calc_card(s, surf, M, top, calc_w, rows, total_label, total_value)
    nx = M + calc_w + GUT
    _note_card(s, surf, nx, top, RIGHT - nx, min(bottom, CONTENT_BOTTOM) - top, "What this means", note)
    return s


def _slide_brand_engagement_value(prs, ctx: DeckCtx, idx: int, total: int):
    """Brand engagement value: everyone who engaged, at one rate."""
    rate = _rate(ctx, "bev_per_user")
    people = _base_for(ctx.bev, rate, ctx.post_users_proj)
    title = ctx.titles([(f"Everyone who engaged with {ctx.brand}", f" adds up to {fmt_money(ctx.bev)}."),
                         ("Everyone who engaged", f" adds up to {fmt_money(ctx.bev)}.")])
    rows = [(f"{_cap(ctx.ba)} engagers {ctx.after_word}", fmt_num(people))]
    if rate > 0:
        rows.append(("x our 2026 value per engaged person", f"${rate:,.2f}"))
    note = (f"This counts every {ctx.ba} engager in {_the_days(ctx.post_days)}, new or not. "
            f"{fmt_num(ctx.pre_users_proj)} people were already engaging {ctx.before_word}. "
            "The next page prices only the new ones.")
    return _value_page(prs, ctx, idx, S_DARK, "Brand engagement", title,
                       f"Brand engagement value{', ' + ctx.post_range if ctx.post_range else ''}.",
                       rows, "Brand engagement value", fmt_money(ctx.bev), note)


def _slide_brand_lift_value(prs, ctx: DeckCtx, idx: int, total: int):
    """Brand lift value: only the people who are new. With no matched
    group it is a before and after count; with one, the gain among the
    people reached less the matched group's gain over the same days. No
    test statistic appears: the lift is a figure with a plain reading."""
    rate = _rate(ctx, "blv_per_incr_user")
    base = _base_for(ctx.blv, rate, ctx.incr_users)
    gain = ctx.post_users_proj - ctx.pre_users_proj
    cg = _dict(ctx.data.get("control_group"))
    since = "since the campaign began"
    if ctx.blv <= 0:
        title = ("No new engagement", " to price in this window.")
    elif ctx.has_control:
        title = ("The lift over the matched group", f" is worth {fmt_money(ctx.blv)}.")
    else:
        title = ctx.titles([(f"New {ctx.brand} engagers {since}", f" are worth {fmt_money(ctx.blv)}."),
                             (f"New engagers {since}", f" are worth {fmt_money(ctx.blv)}.")])
    rows = []
    if ctx.has_control:
        matched = gain - base if (base > 0 and gain - base >= 0) else max(_control_people(cg, ctx.audience_proj), 0)
        rows.append((f"More {ctx.ba} engagers among those reached", fmt_num(max(gain, 0))))
        rows.append(("Less the same gain in the matched group", fmt_num(matched)))
        rows.append(("New people beyond the matched group", fmt_num(base)))
    else:
        rows.append((f"New {ctx.ba} engagers {since}", fmt_num(base)))
    if rate > 0:
        rows.append(("x our 2026 value per new engaged person", f"${rate:,.2f}"))
    if ctx.has_control:
        note = (f"Brand lift counts the gain in {ctx.ba} engagers beyond what a matched group gained over "
                "the same days.")
        # the two groups side by side: the projected counts and the one
        # decimal every other page of the deck uses
        matched_n = _count(cg.get("projected_control_size")) or ctx.audience_proj
        if _same_size(ctx):
            note += f" Both groups hold {fmt_num(ctx.audience_proj)} people."
        else:
            note += (f" The campaign reached {fmt_num(ctx.audience_proj)} people and the matched group "
                     f"holds {fmt_num(matched_n)}.")
        if ctx.pre_pen > 0 and ctx.c_pre_pen > 0:
            note += (f" {_cap(ctx.before_word)}, {ctx.pre_pen:.1f}% of the people reached engaged with "
                     f"{ctx.b}, against {ctx.c_pre_pen:.1f}% of the matched group.")
    else:
        note = (f"Brand lift counts only people who engaged with {ctx.b} {ctx.after_word} and had not in "
                f"{_the_days(ctx.pre_days)} before. It is the part of the total that is new {since}.")
    return _value_page(prs, ctx, idx, S_LIGHT, "Brand lift", title,
                       f"Brand lift value{', ' + ctx.post_range if ctx.post_range else ''}.",
                       rows, "Brand lift value", fmt_money(ctx.blv), note)


def _slide_conversion_value(prs, ctx: DeckCtx, idx: int, total: int):
    """Conversion value: the orders in the window at the average order
    value. Shown whenever conversion is part of the total (the page and
    the total always agree); never for automotive, where purchases are
    not visible and the lens stays out of the total too."""
    conv = _dict(ctx.data.get("conversions"))
    camp = _dict(ctx.data.get("campaign"))
    rate = _rate(ctx, "conv_value_per_user")
    orders = _base_for(ctx.cv, rate, _count(conv.get("post_users_projected")))
    title = _pick_title([(f"{fmt_num(orders)} orders", f" brought in {fmt_money(ctx.cv)}."),
                         ("Orders in the window", f" brought in {fmt_money(ctx.cv)}.")])
    rows = [("Orders in the window", fmt_num(orders))]
    if rate > 0:
        rows.append(("x average order value", f"${rate:,.2f}"))
    parts = []
    units = _count(_dict(camp.get("drop")).get("units_us")) or _count(conv.get("units_us"))
    if units and orders:
        parts.append(f"{fmt_num(units)} pieces sold in the US across {fmt_num(orders)} orders.")
    band = _dict(camp.get("price_band"))
    lo, hi = _num(band.get("low_usd")), _num(band.get("high_usd"))
    if lo and hi and hi > lo:
        parts.append(f"Pieces ran ${lo:,.0f} to ${hi:,.0f}, and a typical order held more than one.")
    account = _safe_prose(conv.get("note"), brand=ctx.brand, talent=ctx.talent)
    if account:
        parts.append(account)
    if conv.get("low_signal"):
        parts.append("Orders are still few, so read this as an early count.")
    if not parts:
        parts.append(f"Each order placed with {ctx.b} in the window is priced at the average order value.")
    return _value_page(prs, ctx, idx, S_DARK, "Sales", title,
                       f"Conversion value{', ' + ctx.post_range if ctx.post_range else ''}.",
                       rows, "Conversion value", fmt_money(ctx.cv), " ".join(parts))


def _lone_last_word(text: str, size: float, width_in: float) -> bool:
    """True when this label wraps and leaves one word on its last line at
    any renderer scale (the pack's own widow reading)."""
    if len(str(text).split()) < 3:
        return False
    pack = _design()
    try:
        return bool(pack.widow_lines(text, size, width_in)[0]) if pack is not None else False
    except Exception:
        return False


def _under_35(rows: list) -> Optional[float]:
    """The share under 35 from an AGE column, when its buckets end
    cleanly at 34; None when a bucket straddles the line."""
    total = 0.0
    seen = False
    for r in rows:
        label = str(r.get("value") or "").lower()
        nums = [int(n) for n in re.findall(r"\d+", label)]
        if not nums:
            continue
        if "under" in label or "less" in label or "younger" in label:
            lo, hi = 0, nums[0]
        elif len(nums) >= 2:
            lo, hi = nums[0], nums[1]
        else:
            lo, hi = nums[0], 200
        if hi <= 34:
            total += _num(r.get("percentage"))
            seen = True
        elif lo <= 34:
            return None
    return total if seen else None


def _slide_demographics(prs, ctx: DeckCtx, idx: int, total: int):
    """Who engaged: age, gender, ethnicity and income, every bucket
    shown, labels in sentence case."""
    s = _blank(prs)
    surf = S_LIGHT
    _ground(s, surf)
    demo_block = _dict(ctx.data.get("demographics"))
    demos = _dict(demo_block.get("post"))
    cats = [("Age", demos.get("age") or []), ("Gender", demos.get("gender") or []),
            ("Ethnicity", demos.get("ethnicity") or []), ("Income", demos.get("income") or [])]
    cats = [(lab, [r for r in items if isinstance(r, dict)]) for lab, items in cats]
    cats = [(lab, items) for lab, items in cats if items]
    note = _safe_prose(re.sub(r"^\s*Source:[^.]*\.\s*", "", str(demo_block.get("source_note") or "")),
                       brand=ctx.brand, talent=ctx.talent)
    own = f"{ctx.brand}'s" if not ctx.brand.lower().endswith("s") else f"{ctx.brand}'"
    title = None
    gender = dict(cats).get("Gender") or []
    if gender and not note:
        lead = max(gender, key=lambda r: _num(r.get("percentage")))
        word = {"female": "women", "male": "men"}.get(str(lead.get("value") or "").strip().lower())
        if word and _num(lead.get("percentage")) >= 55:
            title = ctx.titles([(f"{_cap(_about_share(_num(lead.get('percentage'))))} {own} engagers", f" are {word}."),
                                 (f"{_cap(_about_share(_num(lead.get('percentage'))))} the engagers", f" are {word}.")])
    if title is None:
        title = ("Who engaged,", " by age, gender, ethnicity and income.")
    if note:
        sub = note
    else:
        sub = f"{_cap(ctx.ba)} engagers {ctx.after_word}, among the {fmt_num(ctx.audience_proj)} people reached."
        u35 = _under_35(dict(cats).get("Age") or [])
        if u35 is not None and u35 > 0:
            sub += f" {u35:.1f}% are under 35."
    top = _head(s, surf, idx, f"Who engaged with {ctx.brand}" if not note else "Who engaged", title, sub,
                ctx.logo(surf), source=ctx.sources_line)
    if not cats:
        return s
    n = len(cats)
    col_w = (BAND - GUT * (n - 1)) / n
    max_rows = max(len(items) for _lab, items in cats)
    rows_top = top + 0.4
    pitch_fit = (CONTENT_BOTTOM - rows_top) / max_rows
    buckets = [_bucket_label(r.get("value")) for _lab, items in cats for r in items]
    shares = [fmt_pct(r.get("percentage") or 0) for _lab, items in cats for r in items]
    pct_w = max([_measure(sh_, 12, 2.0, bold=True).max_w_in for sh_ in shares] or [0.6]) + 0.12
    label_w = max(1.0, col_w - pct_w - 0.1)
    row_size, row_lines, trimmed = NOTE_FLOOR, 1, False
    fits = []
    # a table label may step to 9pt before it is cut (ten long buckets)
    for size in (12, 11.5, 11, 10.5, 10, 9.5, 9):
        lines = max([_measure(b, size, label_w).lines for b in buckets] or [1])
        if lines * _line_h_in(size, 1.05) + 0.03 <= pitch_fit:
            fits.append((size, lines))
    if fits:
        # one size for the whole table: the largest at which no label ends
        # on a lone word ('Black or African / American'); the finisher
        # would otherwise shrink that one label and leave its column uneven
        row_size, row_lines = next(((sz, ln) for sz, ln in fits
                                    if not any(_lone_last_word(b, sz, label_w) for b in buckets)), fits[0])
    else:
        trimmed = True          # every label holds the one line its row has
    row_h = max(row_lines * _line_h_in(row_size, 1.05) + 0.03, min(0.44, pitch_fit))
    for i, (label, items) in enumerate(cats):
        x = M + (col_w + GUT) * i
        # age and income read in their own order; the others largest first
        ordered = list(items) if label in ("Age", "Income") else sorted(
            items, key=lambda r: -_num(r.get("percentage")))
        _label(s, x, top, col_w, label, surf.card_label)
        _rule(s, x, top + 0.28, col_w, surf.rule)
        for j, r in enumerate(ordered):
            y = rows_top + j * row_h
            bucket = _bucket_label(r.get("value"))
            if trimmed:
                bucket = _break_long_token(bucket, label_w, row_size, max_lines=1)
            T(s, x, y, label_w, row_h - 0.02, bucket, size=row_size, color=surf.title, anchor="middle")
            T(s, x + col_w - pct_w, y, pct_w, row_h - 0.02, fmt_pct(r.get("percentage") or 0),
              size=row_size, weight="SemiBold", color=surf.title, align="right", anchor="middle")
    return s


def _change_cell(before: int, after: int, new: bool) -> tuple:
    """(text, kind) for the change column: 'New' for a page with no
    history, a multiple for a very large rise, a signed percent
    otherwise."""
    if new or before <= 0:
        return "New", "new"
    pct = (after - before) / before * 100.0
    if pct >= 1000:
        return f"{after / before:.0f}x", "up"
    return f"{pct:+.1f}%", ("up" if pct >= 0 else "down")


NOT_LIVE = "Not live"


def _slide_top_touchpoints(prs, ctx: DeckCtx, idx: int, total: int):
    """Where people met the brand: its pages and accounts by visits,
    before and after. A page the campaign created shows no visits before
    it existed."""
    s = _blank(prs)
    surf = S_DARK
    _ground(s, surf)
    rows = _touchpoints(ctx, 7)
    own = next((r for r in rows if r["new"]), None)
    if own is not None:
        title = _pick_title([("The campaign's own page drew",
                              f" {fmt_num(own['after'])} visits in {_days_phrase(ctx.post_days)}."),
                             ("The campaign's own page", " drew visits from day one.")])
    elif rows and all(r["after"] > r["before"] for r in rows):
        title = ctx.titles([("Visits rose on every", f" {ctx.brand} page and account listed."),
                             ("Visits rose", " on every page and account listed.")])
    else:
        title = ctx.titles([("Where people met", f" {ctx.brand} {ctx.after_word}."),
                             ("Where people met", " the brand.")])
    sub = f"Visits to {ctx.ba} pages and accounts by the people reached, before and after."
    top = _head(s, surf, idx, f"Where people met {ctx.brand}", title, sub, ctx.logo(surf), source=ctx.sources_line)
    if not rows:
        T(s, M, top + 0.2, BAND, 0.4, "No page or account showed visits in this window.", size=13, color=surf.body)
        return s
    c_name = (M, 5.7)
    c_pre = (M + 5.9, 1.7)
    c_post = (M + 7.8, 1.7)
    c_chg = (M + 9.7, BAND - 9.7)
    _label(s, c_name[0], top, c_name[1], _one_line(f"{ctx.ba} page or account", 9, c_name[1], tracking=1.0, bold=True),
           surf.card_label, size=9)
    _label(s, c_pre[0], top, c_pre[1], "Visits before", surf.card_label, align="right", size=9)
    _label(s, c_post[0], top, c_post[1], "Visits after", surf.card_label, align="right", size=9)
    _label(s, c_chg[0], top, c_chg[1], "Change", surf.card_label, align="right", size=9)
    _rule(s, M, top + 0.3, BAND, surf.muted)
    note = ("A visit is one session on a page or account. One person can visit many times, "
            "so visits run higher than the people counts on other pages.")
    if any(r["new"] for r in rows):
        note += f" {NOT_LIVE} marks a page or account that did not exist before the launch."
    note_h = _measure(note, 11, BAND, spacing=1.25).height_in + 0.04
    pitch = max(0.3, min(0.42, (CONTENT_BOTTOM - note_h - 0.16 - (top + 0.38)) / len(rows)))
    y = top + 0.38
    for r in rows:
        name_size = _fit_stat(r["name"], c_name[1], 12.5, floor=10, bold=True)
        name = _break_long_token(r["name"], c_name[1], name_size, bold=True, max_lines=1)
        T(s, c_name[0], y, c_name[1], pitch, name, size=name_size, weight="SemiBold", color=surf.title, anchor="middle")
        # a page that did not exist before the launch was not live; it never reads as a count of 0
        T(s, c_pre[0], y, c_pre[1], pitch, NOT_LIVE if r["new"] else fmt_num(r["before"]), size=12,
          color=surf.muted if (r["new"] or r["before"] == 0) else surf.title, align="right", anchor="middle")
        T(s, c_post[0], y, c_post[1], pitch, fmt_num(r["after"]), size=12, color=surf.title, align="right",
          anchor="middle")
        text, kind = _change_cell(r["before"], r["after"], r["new"])
        T(s, c_chg[0], y, c_chg[1], pitch, text, size=12, weight="Bold",
          color={"new": surf.accent, "down": surf.negative}.get(kind, surf.title), align="right", anchor="middle")
        y += pitch
    BODY(s, M, y + 0.14, BAND, note, max_h=CONTENT_BOTTOM - (y + 0.14), size=11, floor=10, spacing=1.25,
         min_spacing=1.1, color=surf.body)
    return s


def _slide_year_over_year(prs, ctx: DeckCtx, idx: int, total: int):
    """Earned media by year of the partnership, as bars."""
    s = _blank(prs)
    surf = S_LIGHT
    _ground(s, surf)
    years = [y for y in (ctx.data.get("yearly_breakdown") or []) if isinstance(y, dict)]
    vals = [_num(y.get("earned_media_value")) for y in years]
    if len(vals) >= 2 and abs(vals[-1] - vals[0]) >= 0.01:
        verb = "grew" if vals[-1] > vals[0] else "fell"
        title = (f"Earned media {verb}", f" from {fmt_money(vals[0])} to {fmt_money(vals[-1])} a year.")
    else:
        title = ("Earned media held", " level from year to year.")
    top = _head(s, surf, idx, "Year by year", title, "Earned media value for each year of the partnership.",
                ctx.logo(surf), source=ctx.sources_line)
    if not years:
        return s
    n = len(years)
    gap = GUT
    bw = min(2.6, (BAND - gap * (n - 1)) / n)
    # the page ends on what the bars mean, in the reader's terms
    first = str(years[0].get("label") or years[0].get("year") or "the first year")
    last = str(years[-1].get("label") or years[-1].get("year") or "the latest year")
    read = ""
    if n >= 2 and vals[0] > 0 and abs(vals[-1] - vals[0]) >= 0.01:
        change = (vals[-1] - vals[0]) / vals[0] * 100.0
        if abs(change) >= 1:
            read = f"What this means: {last} earned {abs(change):.0f}% {'more' if change > 0 else 'less'} than {first}."
    chart_top, chart_bottom = top + 0.45, CONTENT_BOTTOM - (1.0 if read else 0.62)
    chart_h = max(0.6, chart_bottom - chart_top)
    peak = max(vals) or 1.0
    best = vals.index(max(vals))
    for i, (yr, v) in enumerate(zip(years, vals)):
        x = M + i * (bw + gap)
        h = max(0.12, chart_h * (v / peak))
        _card(s, x, chart_bottom - h, bw, h, fill=surf.accent if i == best else surf.title, radius=0.06)
        T(s, x, chart_bottom - h - 0.34, bw, 0.28, fmt_money(v), size=_fit_stat(fmt_money(v), bw, 13, floor=10),
          weight="Bold", color=surf.title, align="center")
        _label(s, x, chart_bottom + 0.1, bw, str(yr.get("label") or yr.get("year") or ""), surf.title, align="center")
        people = _count(yr.get("users_projected"))
        if people:
            T(s, x, chart_bottom + 0.34, bw, 0.22, f"{fmt_num(people)} people", size=10, color=surf.body, align="center")
    _reading(s, surf, CONTENT_BOTTOM - 0.3, read)
    return s


def _final_reading(ctx: DeckCtx) -> str:
    days = _days_phrase(ctx.post_days)
    if not _moved(ctx):
        return (f"The read covers {fmt_num(ctx.audience_proj)} people and {fmt_money(ctx.total_value)} in brand value. "
                f"What it does not show is a rise in {ctx.ba} engagement.")
    if ctx.blv > 0 and ctx.incr_users > 0:
        out = (f"In {days} the partnership added {fmt_num(_base_for(ctx.blv, _rate(ctx, 'blv_per_incr_user'), ctx.incr_users))} "
               f"{ctx.ba} engagers and {fmt_money(ctx.total_value)} in brand value.")
    else:
        out = f"In {days} the partnership carried {fmt_money(ctx.total_value)} in brand value."
    if ctx.has_conversions and min(ctx.lenses, key=lambda lv: lv[1])[0] == "Conversion":
        orders = _base_for(ctx.cv, _rate(ctx, "conv_value_per_user"),
                           _count(_dict(ctx.data.get("conversions")).get("post_users_projected")))
        out += f" It has not sold much yet: {fmt_num(orders)} orders."
    return out


def _slide_final_insight(prs, ctx: DeckCtx, idx: int, total: int):
    """The close: one statement, its reading, and the next step."""
    s = _blank(prs)
    surf = S_ORCHID
    _ground(s, surf)
    _chrome(s, surf, idx, "What to do next", ctx.logo(surf), page=idx)
    statement = _final_statement(ctx)
    size = 32
    for sz in (40, 36, 34, 32):
        if _line_count(statement, sz, BAND, bold=True) <= 2:
            size = sz
            break
    st_h = _measure(statement, size, BAND, bold=True, spacing=1.0).height_in
    T(s, M, 2.0, BAND, st_h, statement, size=size, weight="Bold", color=surf.title, spacing=1.0)
    y = 2.0 + st_h + 0.3
    card_h = 1.3
    card_y = CONTENT_BOTTOM - card_h
    BODY(s, M, y, 10.2, _final_reading(ctx), max_h=card_y - 0.3 - y, size=20, floor=14, spacing=1.2,
         min_spacing=1.1, weight="Light", color=surf.title)
    _card(s, M, card_y, BAND, card_h, fill=surf.card)
    _label(s, M + 0.3, card_y + 0.24, 3.0, "Next step", surf.card_accent)
    BODY(s, M + 0.3, card_y + 0.54, BAND - 0.6, _next_step(ctx)[1], max_h=card_h - 0.54 - 0.16, size=14, floor=11,
         spacing=1.25, min_spacing=1.1, color=surf.card_title)
    return s


def _slide_source(prs, ctx: DeckCtx, idx: int, total: int):
    """How each figure is counted, in plain words. No test statistic, no
    outside name, no word for how a number was made."""
    s = _blank(prs)
    surf = S_DARK
    _ground(s, surf)
    top = _head(s, surf, idx, "How it is counted", ("How each figure", " in this deck is counted."), None,
                ctx.logo(surf))
    reach = fmt_num(ctx.audience_proj)
    same = ctx.pre_days and ctx.pre_days == ctx.post_days
    windows = []
    if ctx.pre_range:
        windows.append(f"Before: {ctx.pre_range}.")
    if ctx.post_range:
        windows.append(f"After: {ctx.post_range}.")
    if same:
        windows.append(f"Both windows run {_days_phrase(ctx.post_days)}, so before and after compare like for like.")
    elif ctx.pre_days and ctx.post_days:
        windows.append(f"The first runs {_days_phrase(ctx.pre_days)} and the second {_days_phrase(ctx.post_days)}.")
    if ctx.has_control:
        lift = ("Brand lift is the gain among the people reached minus the gain in a matched group over the same "
                f"days. That group {'is the same size, ' if _same_size(ctx) else ''}started at about the same "
                f"{ctx.ba} engagement{',' if _same_size(ctx) else ''} and had no exposure to the campaign.")
    else:
        lift = (f"Brand lift is the change in {ctx.ba} engagement inside the same group of people, "
                f"{ctx.after_word} against before. Only people newly engaging count toward its value.")
    notes = [
        ("The data",
         "Crosswalk follows 30M+ opted-in US consumers across their full digital life. Everything in this deck "
         "is behavior we observed: visits, sessions and views. Nothing comes from a survey."),
        ("The people",
         f"Counts are projected to the {reach} US people in the read. Each person is counted once."),
        ("Engagement",
         f"An engagement rate is the share of those people with at least one {ctx.ba} touchpoint in a window."),
        ("The two windows", " ".join(windows) or "The read compares a window before the campaign with one after."),
        ("Brand lift", lift),
        ("The dollar values",
         f"Total brand value adds {_n_word(len(ctx.lenses))} parts: {_lens_words(ctx)}. The value per person behind "
         "each part is our 2026 rate, shown on its page."),
    ]
    gap = 0.22
    body_x, body_w = 3.7, RIGHT - 3.7
    room = SRC_Y - 0.05 - top
    fitted = _fit_stack(
        [dict(text=body, width_in=body_w, size=13, floor=BODY_FLOOR, spacing=1.3, min_spacing=1.12)
         for _label_, body in notes],
        room, gap)
    spare = room - (sum(h + 0.02 for _a, _b, _c, h in fitted) + gap * (len(notes) - 1))
    if spare > 0:
        gap += min(0.2, spare / (len(notes) - 1))
    y = top
    for (label, _body), (sz, sp, txt, h_in) in zip(notes, fitted):
        _label(s, M, y + 0.07, 2.6, label, surf.body)
        T(s, body_x, y, body_w, round(h_in + 0.02, 3), txt, size=sz, color=surf.title, spacing=sp)
        y += h_in + 0.02 + gap
    return s


def _slide_failed(prs, ctx: DeckCtx, idx: int):
    """A page that could not be drawn. The reader sees a plain line; the
    reason goes to the log."""
    s = _blank(prs)
    surf = S_DARK
    _ground(s, surf)
    _chrome(s, surf, idx, "Brand partnership valuation", ctx.logo(surf), page=idx)
    T(s, M, 3.2, BAND, 0.5, PAGE_FAILED_LINE, size=16, color=surf.body)
    return s


PAGE_FAILED_LINE = "This page could not be drawn."


# ─────────────────────────────────────────────────────────────────────
#  Public entry point
# ─────────────────────────────────────────────────────────────────────
def _plan(ctx: DeckCtx) -> list:
    """The pages of this deck, in order. The short version is second, so
    someone who reads only that page leaves with the answer; the method
    note closes the deck."""
    builders = [
        _slide_cover,
        _slide_tldr,
        _slide_measured,
        _slide_audience_scale,
        _slide_engagement_lift,
        _slide_total_value_hero,
        _slide_emv,
        _slide_brand_engagement_value,
        _slide_brand_lift_value,
    ]
    if ctx.has_conversions:
        builders.append(_slide_conversion_value)
    builders.extend([
        _slide_demographics,
        _slide_top_touchpoints,
    ])
    if ctx.has_yearly:
        builders.append(_slide_year_over_year)
    builders.extend([
        _slide_final_insight,
        _slide_source,
    ])
    return builders


def build_presentation(data: dict, image_url: Optional[str] = None, category: Optional[str] = None,
                       s3_client=None):
    """The deck as a python-pptx Presentation, before it is finished
    (fonts embedded, checks run). `build_deck` is the delivery path; this
    is for a caller that reads the pages (the layout self-check, tests)."""
    ctx = _derive_context(data, image_url, category, s3_client=s3_client)

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    builders = _plan(ctx)
    total = len(builders)
    for i, builder in enumerate(builders, start=1):
        before = len(prs.slides._sldIdLst)
        try:
            builder(prs, ctx, i, total)
        except Exception as e:
            # Never let a single page failure kill the whole deck: the
            # page is replaced with a plain line and the reason is
            # logged, so the reader still gets a deck rather than a 500.
            print(f"[bpiq_deck_builder] page {i} ({builder.__name__}) failed: {type(e).__name__}: {e}")
            try:
                ids = prs.slides._sldIdLst
                while len(ids) > before:
                    rid = ids[-1].rId
                    prs.part.drop_rel(rid)
                    ids.remove(ids[-1])
            except Exception:
                pass
            _slide_failed(prs, ctx, i)
    return prs


def build_deck_report(data: dict,
                      image_url: Optional[str] = None,
                      category: Optional[str] = None,
                      s3_client=None,
                      *, render_check: bool = False) -> tuple:
    """Build the deck and finish it. Returns (pptx bytes, the finisher's
    report or None when the design pack is not on the machine). The
    report's `ready` is True only when Crosswalk Inter is embedded, the
    brand lint found no FAIL and the plain-English check found no flag."""
    prs = build_presentation(data, image_url=image_url, category=category, s3_client=s3_client)

    # 2026-10-29: the layout self-check. Every block was measured and
    # fitted as it was placed; this proves it on the finished slides
    # (no text frame's rendered extent meets another's, none needs more
    # height than its box) and reports anything it finds. The deck
    # still ships: a correction is made in the builder, never by
    # holding a file (no-rebuild-level-correction).
    try:
        for issue in check_layout(prs):
            print(f"[bpiq_deck_builder] layout check: {issue}")
    except Exception as exc:
        print(f"[bpiq_deck_builder] layout check did not run: {exc}")

    out = io.BytesIO()
    prs.save(out)
    data_out, report = out.getvalue(), None
    # 2026-10-21: the delivered deck carries Crosswalk Inter embedded and
    # passes the pack's checks (fixed in place; never held).
    _ds = _design()
    if _ds is not None:
        try:
            data_out, report = _ds.gate_attachment("deck.pptx", data_out, render_check=render_check)
        except Exception:
            data_out, report = out.getvalue(), None
    return data_out, report


def build_deck(data: dict,
               image_url: Optional[str] = None,
               category: Optional[str] = None,
               s3_client=None) -> bytes:
    """Build a full BPIQ analysis deck and return the .pptx bytes.

    Parameters
    ----------
    data       : the BPIQ result JSON (matches /api/brand-partnership-iq/
                 results/<key> 'data' field).
    image_url  : admin-uploaded hero photo from the metadata sidecar
                 (the cover's background, under a baked scrim). Optional:
                 the cover takes the pack's Graphite wash when it is
                 missing or unfetchable. A `/api/profile-image-file/<key>`
                 path is read from the private bucket (2026-10-29), an
                 http(s) URL is fetched.
    category   : admin-managed category (case-insensitive). 'automotive'
                 keeps the conversion lens out of the deck and its total.
    s3_client  : optional boto3-style client for the bucket read (the
                 app passes its own; tests pass a fake). None builds one.
    """
    return build_deck_report(data, image_url=image_url, category=category, s3_client=s3_client)[0]


FILE_SUFFIX = "Brand_Partnership_Valuation"


def suggested_filename(data: dict, ext: str = "pptx") -> str:
    """The deck's file name, the same wherever it is handed over (the
    chat's link, the email's attachment, the tab's download): the
    pairing as the cover prints it, then what the file is. No date and
    no working word (2026-11-11): 'JYT_x_Gap_Brand_Partnership_Valuation.pptx'."""
    data = _dict(data)
    name = safe_filename(_normalize_project_label(str(data.get("project_name") or "")).strip())[:90].strip("._-") \
        if str(data.get("project_name") or "").strip() else ""
    if name.lower().replace("_", " ").endswith("brand partnership valuation"):
        name = name[:-len(FILE_SUFFIX)].strip("._-")
    return f"{name}_{FILE_SUFFIX}.{ext}" if name else f"{FILE_SUFFIX}.{ext}"
