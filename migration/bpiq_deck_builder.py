"""
Brand Partnership IQ - Analysis Deck Builder
============================================

Generates a high-design analysis deck (.pptx) from a single BPIQ result
payload. Style direction:

  - Layout / typography / color: matched to the 2026 LISA Audience
    Profile deck (16:9, dark forest + cream alternating sections, big
    editorial typography, eyebrow + headline + stat-tile rhythm).
  - Narrative voice / data framing: matched to the MIDG / CAA Eiza
    Gonzalez deck - declarative dollar-impact headlines, pre vs post
    stat comparisons, "Read As:" plain-English translations, and a
    closing brand-by-brand wrap.

Usage:

    from migration.bpiq_deck_builder import build_deck
    pptx_bytes = build_deck(data, image_url=..., category=...)

The single public entry point is `build_deck(...)`. All slide builders
are private helpers and assume a fully-formed BPIQ result dict
(matching the shape served by /api/brand-partnership-iq/results/...).
"""
from __future__ import annotations

import io
import os
import re
import tempfile
import textwrap
import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt


# ─────────────────────────────────────────────────────────────────────
#  Palette - lifted from the LISA deck.
#
#  We keep this list small on purpose: every slide picks one BG_* and
#  the typography palette inherits from it. Accent colors are reserved
#  for stat numerals and the +X% lift callouts.
# ─────────────────────────────────────────────────────────────────────
BG_DARK   = RGBColor(0x0C, 0x16, 0x18)   # primary - dark forest
BG_CREAM  = RGBColor(0xE9, 0xE8, 0xE1)   # secondary - warm cream
BG_PINK   = RGBColor(0xE6, 0x82, 0xFF)   # accent insight slide
BG_LAV    = RGBColor(0xB7, 0xB3, 0xD8)   # final-insight slide
BG_WARM   = RGBColor(0xEA, 0xE8, 0xE2)   # variant cream

FG_LIGHT  = RGBColor(0xF4, 0xF1, 0xEA)   # on dark backgrounds
FG_DARK   = RGBColor(0x0C, 0x16, 0x18)   # on cream backgrounds
ACCENT    = RGBColor(0xC8, 0xE6, 0x00)   # Crosswalk green - hero callouts
LIFT_UP   = RGBColor(0x10, 0xB9, 0x81)   # positive lift
LIFT_DOWN = RGBColor(0xEF, 0x44, 0x44)   # negative lift
MUTED_LT  = RGBColor(0x6B, 0x72, 0x80)   # muted text on cream
MUTED_DK  = RGBColor(0x8F, 0x94, 0x8C)   # muted text on dark

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
    """Exact dollars, never $12K / $1.2M abbreviations (Jenna
    2026-09-23: 'dont round on that page when making decks'). Cents
    print only when they are real."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "$0"
    if abs(n - round(n)) >= 0.005:
        return f"${n:,.2f}"
    return f"${round(n):,.0f}"


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


def fmt_signed_pct(n: Any) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0%"
    sign = "+" if n > 0 else ("" if n == 0 else "")
    return f"{sign}{n:.1f}%"


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
CONTENT_BOTTOM = 7.05     # the page footer sits at 7.10in; content stops here


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
    list without its last clause (after a semicolon); otherwise without its
    last words. None when the text is already down to a few words."""
    text = str(text or "").strip()
    sentences = _sentences(text)
    if len(sentences) > 1:
        return " ".join(sentences[:-1]).strip()
    if text.count(";") >= 1:
        head = text.rsplit(";", 1)[0].rstrip(" ,;")
        return head + "." if head and not head.endswith((".", "!", "?")) else head or None
    words = text.rstrip(" .!?,;:").split()
    if len(words) <= 4:
        return None
    keep = max(4, int(len(words) * 0.8))
    return " ".join(words[:keep]).rstrip(" ,;:") + "."


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


def _last_line_words(text: Any, size_pt: float, width_in: float, *, bold: bool = False) -> int:
    """Words on the last wrapped line of one paragraph, the fewest across
    the pack's renderer scales (2 means no widow). 2 when the pack is
    absent or the text is a single line."""
    words = str(text or "").split()
    if len(words) < 2:
        return 2
    pack = _design()
    mod = pack.lint_module() if pack is not None else None
    if mod is None:
        return 2
    try:
        wt = mod._weight_key(FONT_DISPLAY, bold)
        chars = [(ch, wt, float(size_pt), 0.0) for ch in str(text)]
        counts = []
        for k in mod.WRAP_SCALES:
            lines = mod._wrap(chars, width_in * 72 * k)
            counts.append(2 if len(lines) < 2 else len(lines[-1][0]))
        return min(counts)
    except Exception:
        return 2


def _fit_title(text: Any, sizes, width_in: float, max_h_in: float, *, spacing: float = 0.95) -> int:
    """The largest size in `sizes` at which a title fits `max_h_in` and
    ends on a line of at least two words, measured at the width
    `_add_text` will give it. Falls back to the largest size that fits
    the height, then to the smallest size."""
    fits_height = None
    for sz in sizes:
        w = _box_width_for(text, sz, width_in, bold=True, font=FONT_DISPLAY)
        if _measure(text, sz, w, bold=True, spacing=spacing).height_in > max_h_in:
            continue
        if fits_height is None:
            fits_height = sz
        if _last_line_words(text, sz, w, bold=True) >= 2:
            return int(sz)
    return int(fits_height if fits_height is not None else sizes[-1])


def _add_headline(slide, text: Any, *, color: RGBColor, base: int = 40, floor: int = 30,
                  top=None, width=None, max_lines: int = 2):
    """A slide headline at the house position (0.6in, 1.0in, 11in wide):
    the largest display size from `base` down (2pt steps) at which the
    sentence holds `max_lines` lines, so a long figure or brand name
    never pushes a third line into the content below."""
    top = Inches(1.0) if top is None else top
    width = Inches(11) if width is None else width
    joined = " ".join(part.strip() for part in str(text or "").split("\n") if part.strip())
    sizes = tuple(range(int(base), int(floor) - 1, -2)) or (int(floor),)
    size = _fit_lines(joined, sizes, Emu(width).inches, bold=True, max_lines=max_lines)
    return _add_text(slide, Inches(0.6), top, width, Inches(1.3), text,
                     size=size, bold=True, color=color, spacing=0.95, font=FONT_DISPLAY)


def _callout(slide, left, top, width, label: str, body: Any, *, height, max_height,
             line: RGBColor, line_w: float, label_color: RGBColor, body_color: RGBColor,
             body_size: float = 9, floor: float = NOTE_FLOOR, spacing: float = 1.35,
             min_spacing: float = 1.15, pad: float = 0.2, body_top: float = 0.5):
    """A WHY IT MATTERS card: an outlined box, a tracked label, a body.
    The card is `height` tall when the body fits inside that, grows to
    `max_height` when the body needs more, and the body steps down and
    shortens when even that is not enough. Returns the card's bottom edge."""
    width_in = Emu(width).inches
    design_h, room_h = Emu(height).inches, Emu(max_height).inches
    body_w = width_in - 2 * pad
    sz, sp, t, m = _fit_body(body, body_w, max(0.1, room_h - body_top - pad), size=body_size,
                             floor=floor, spacing=spacing, min_spacing=min_spacing)
    body_h = m.height_in + 0.02
    card_h = min(room_h, max(design_h, body_top + body_h + pad))
    _add_rect(slide, left, top, width, Inches(card_h), fill=None, line=line, line_w=line_w)
    _add_text(slide, left + Inches(pad), top + Inches(0.15), Inches(body_w), Inches(0.3),
              label, size=9, bold=True, color=label_color, letter_spacing=0.18)
    _add_text(slide, left + Inches(pad), top + Inches(body_top), Inches(body_w), Inches(body_h),
              t, size=sz, color=body_color, spacing=sp)
    return top + Inches(card_h)


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
        widths = _wrap_widths(p.text, size, inner_w, bold=bold)
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


def _crop_to_fill(pic):
    """Crop a placed picture to its slot's proportions instead of
    stretching it (the design pack fails a stretched image)."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(pic.image.blob)) as im:
            nw, nh = im.size
    except Exception:
        return
    if not (nw and nh and pic.width and pic.height):
        return
    native, placed = nw / nh, pic.width / pic.height
    if abs(native - placed) / placed < 0.02:
        return
    if native > placed:          # too wide: trim the sides
        keep = placed / native
        pic.crop_left = pic.crop_right = (1 - keep) / 2
    else:                        # too tall: trim top and bottom
        keep = native / placed
        pic.crop_top = pic.crop_bottom = (1 - keep) / 2


def _add_text(
    slide,
    left, top, width, height,
    text: str,
    *,
    size: float = 18,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor = FG_DARK,
    font: str = FONT_BODY,
    align: str = "left",
    anchor: str = "top",
    spacing: float = 1.05,
    letter_spacing: float = 0.0,
):
    """Add a text box and return the shape. `text` may contain \\n
    for multi-paragraph blocks; each paragraph inherits the same run
    style."""
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
    # here, not in the copy.
    safe_w = _box_width_for(text, size, Emu(width).inches, bold=bold, font=font,
                            letter_spacing=letter_spacing)
    if safe_w < Emu(width).inches:
        box.width = Inches(safe_w)
    # the frame takes the height its text needs (2026-10-29): measured
    # with the pack's wrap model at the final width, the box grows (never
    # shrinks) to hold every line, keeping its anchor edge where it was,
    # so a frame never claims less room than its text and check_layout
    # can tell what the text would run into
    try:
        need = Inches(round(_measure(text, size, Emu(box.width).inches, bold=bold,
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
        r.font.name = font
        r.font.size = Pt(size)
        r.font.bold = bold
        # the house family ships upright faces only; italics never render
        # as Crosswalk Inter, so an italic request sets a quiet weight instead
        r.font.italic = False
        r.font.color.rgb = color
    return box


def _add_rect(slide, left, top, width, height, *,
              fill: Optional[RGBColor] = None,
              line: Optional[RGBColor] = None,
              line_w: Optional[float] = None):
    rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
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
    return rect


def _eyebrow(slide, left, top, text: str, *, on_dark: bool = True, width=Inches(10)):
    """Tiny uppercase tracked eyebrow label - LISA's signature
    'THE LISA AUDIENCE · FRAGRANCE' bar."""
    return _add_text(
        slide, left, top, width, Inches(0.3),
        text.upper(),
        size=10, bold=True,
        color=(MUTED_DK if on_dark else MUTED_LT),
        letter_spacing=0.18,
    )


#: Small prepositions / articles / conjunctions that should NOT be
#: capitalized inside a display name (e.g., "World of Coca-Cola",
#: not "World Of Coca-Cola"). Fixes C12.
_TITLE_LOWERCASE_TOKENS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "if", "in", "of",
    "on", "or", "the", "to", "vs", "vs.", "via", "with",
}


def _touchpoint_display_name(name: str) -> str:
    """Render a brand-property name for the touchpoint table. If the
    source string already contains any uppercase letters, treat it as
    author-cased and preserve it verbatim (payloads now provide
    exact-case names). Otherwise apply a title-case that lowercases
    small prepositions."""
    if not name:
        return ""
    s = str(name).strip()
    if any(c.isupper() for c in s):
        return s
    words = s.split()
    out = []
    for i, w in enumerate(words):
        wl = w.lower()
        if i > 0 and wl in _TITLE_LOWERCASE_TOKENS:
            out.append(wl)
        else:
            # Capitalize each hyphen-separated segment so all-lowercase
            # compound brand names title-case correctly
            # ("coca-cola" -> "Coca-Cola", not "Coca-cola").
            out.append("-".join(
                seg[:1].upper() + seg[1:].lower() if seg else seg
                for seg in w.split("-")))
    return " ".join(out)


def _normalize_project_label(label: str) -> str:
    """Normalize a project label for footer / display use. Replaces
    em-dashes and en-dashes with commas so the footer never carries a
    stray dash (Liz item 20)."""
    if not label:
        return ""
    return (
        str(label)
        .replace(" — ", ", ")
        .replace(" – ", ", ")
        .replace("—", ",")
        .replace("–", ",")
    )


def _page_footer(slide, idx: int, total: int, project_label: str, *, on_dark: bool = True):
    """Bottom bar: project tag (left), CROSSWALK BehaviorGraph (center
    bottom-left), and `idx / total` (right). Echoes LISA's footer."""
    color = MUTED_DK if on_dark else MUTED_LT
    _add_text(
        slide, Inches(0.5), Inches(7.10), Inches(6), Inches(0.3),
        _normalize_project_label(project_label).upper(),
        size=8, color=color, letter_spacing=0.14, bold=True,
    )
    _add_text(
        slide, Inches(11.0), Inches(7.10), Inches(1.85), Inches(0.3),
        f"{idx:02d} / {total:02d}",
        size=8, color=color, align="right", bold=True, letter_spacing=0.14,
    )


def _crosswalk_mark(slide, left, top, *, on_dark: bool = True, size: float = 0.18):
    """Inline 'CROSSWALK · BehaviorGraph' wordmark drawn as text. Uses
    Pillow only when we need a bitmap stamp (cover slide); for inline
    footers a text shape is faster and crisper at scale."""
    color = FG_LIGHT if on_dark else FG_DARK
    _add_text(
        slide, left, top, Inches(4), Inches(0.3),
        "CROSSWALK · BehaviorGraph",
        size=size * 60,  # ~10-12pt depending on size arg
        bold=True, color=color, letter_spacing=0.18,
        font=FONT_DISPLAY,
    )


# ─────────────────────────────────────────────────────────────────────
#  Image helpers - download + crop to slide-ready PNGs.
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
    """The cover photo's bytes, or None (the caller then draws the plate).

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


def _download_image(url: str, max_dim: int = 2000,
                    crop_aspect: Optional[float] = None,
                    s3_client=None) -> Optional[str]:
    """Resolve the cover photo (see `_image_bytes`) to a tmp JPEG. Pillow
    decodes whatever the admin uploaded (webp, png, jpeg) and the save
    is always JPEG, the format python-pptx places without a fuss.
    Optional `crop_aspect` (w/h) center-crops the image to fit a target
    slot - lets the cover slide place a fixed-aspect hero without
    distortion. Returns path or None on any failure (deck-builder must
    NEVER crash because of a bad photo URL)."""
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
        # Center-crop to target aspect first so any subsequent resize
        # doesn't deform the subject.
        if crop_aspect and crop_aspect > 0:
            src_ar = im.width / im.height
            if src_ar > crop_aspect:
                # Source is wider than target → trim left/right
                new_w = int(im.height * crop_aspect)
                off = (im.width - new_w) // 2
                im = im.crop((off, 0, off + new_w, im.height))
            elif src_ar < crop_aspect:
                # Source is taller than target → trim top/bottom
                new_h = int(im.width / crop_aspect)
                # Bias the crop slightly above center so faces don't
                # lose their forehead - this is the classic editorial
                # crop heuristic.
                off = max(0, (im.height - new_h) // 3)
                im = im.crop((0, off, im.width, off + new_h))
        # Cap the long edge so we don't bloat the pptx unnecessarily.
        if max(im.size) > max_dim:
            ratio = max_dim / max(im.size)
            im = im.resize((int(im.width * ratio), int(im.height * ratio)),
                           Image.LANCZOS)
        out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        im.save(out.name, "JPEG", quality=88, optimize=True)
        return out.name
    except Exception:
        return None


def _generate_typographic_hero(text: str, palette: str = "dark") -> str:
    """Fallback hero image - bold typographic block with the brand
    name. Used when the partnership has no admin-uploaded photo so the
    cover slide still has visual weight (mirrors LISA's typographic
    insight slides)."""
    w, h = 1600, 1000
    if palette == "cream":
        bg = (233, 232, 225)
        fg = (12, 22, 24)
    elif palette == "pink":
        bg = (230, 130, 255)
        fg = (12, 22, 24)
    else:
        bg = (12, 22, 24)
        fg = (244, 241, 234)
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    # Try a few candidate fonts; fall back to Pillow's default if none
    # are present (the test environment ships a couple of system fonts).
    font_path_candidates = []
    _ds = _design()
    if _ds is not None:
        try:
            _fp = _ds.font_path("Bold")
            if _fp:
                font_path_candidates.append(str(_fp))
        except Exception:
            pass
    font_path_candidates += [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    font = None
    for p in font_path_candidates:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, 220)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
    txt = (text or "").upper()
    bbox = d.textbbox((0, 0), txt, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((w - tw) / 2, (h - th) / 2 - 40), txt, fill=fg, font=font)
    # Soft vignette so the typographic plate doesn't read as flat.
    overlay = Image.new("RGB", (w, h), bg)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((-200, -200, w + 200, h + 200), fill=0)
    md.ellipse((100, 100, w - 100, h - 100), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(120))
    im = Image.composite(im, overlay, mask)
    out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    im.save(out.name, "JPEG", quality=88, optimize=True)
    return out.name


_BRAND_LOGO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)
_BRAND_LOGO_WHITE = os.path.join(_BRAND_LOGO_DIR, "crosswalk-logo-brand-white.png")
_BRAND_LOGO_BLACK = os.path.join(_BRAND_LOGO_DIR, "crosswalk-logo-brand-black.png")


def _render_crosswalk_logo_png(on_dark: bool = True) -> str:
    """Return the path to the brand-provided Crosswalk wordmark PNG:
    white variant for dark backgrounds, black variant for light. If
    the shipped asset is missing (e.g. running outside the repo),
    fall back to a Pillow-rendered wordmark so the deck still builds."""
    target = _BRAND_LOGO_WHITE if on_dark else _BRAND_LOGO_BLACK
    if os.path.exists(target):
        return target
    # Fallback: render a wordmark with Pillow. Kept as a safety net
    # for environments that don't ship the static assets.
    w, h = 760, 100
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fg = (244, 241, 234, 255) if on_dark else (12, 22, 24, 255)
    font_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    font = None
    for p in font_candidates:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, 64)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
    d.text((0, 8), "CROSSWALK", fill=fg, font=font)
    out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    im.save(out.name, "PNG", optimize=True)
    return out.name


# ─────────────────────────────────────────────────────────────────────
#  Narrative helpers - derive headlines & "read as" sentences from the
#  data. Pure functions, easy to extend / tune in isolation.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class DeckCtx:
    """All the derived numbers the slide builders need, computed once
    so individual slides stay readable."""
    project:        str
    brand:          str
    category_label: str
    is_auto:        bool
    cover_image:    Optional[str]
    logo:           str
    total_value:    float
    emv:            float
    bev:            float
    blv:            float
    cv:             float
    incr_users:     int
    target_size:    int
    audience_proj:  int
    pre_users:      int
    post_users:     int
    pre_users_proj: int
    post_users_proj:int
    pre_pen:        float
    post_pen:       float
    delta_pp:       float
    delta_rel:      float
    incr_lift_pp:   float
    incr_lift_rel:  float
    campaign_window:str
    attribution_d:  int
    panel_size:     int
    sample_pre:     int
    sample_post:    int
    has_conversions:bool
    has_yearly:     bool
    sources_line:   str
    data:           dict


def _adjusted_lift_rel(cg: dict) -> float:
    """Matched lift (pp) as a share of the exposed group's pre-period
    rate, in percent. Falls back to the payload's own relative figure
    when the baseline is missing."""
    cg = cg if isinstance(cg, dict) else {}
    pp = _num(cg.get("incremental_lift_pp"))
    pre = _num(cg.get("treat_pre_pen_pct"))
    if pre > 0:
        return round(pp / pre * 100.0, 1)
    return _num(cg.get("incremental_lift_rel_pct"))


def _dict(o: Any) -> dict:
    """A sub-document that may be missing, null, or the wrong type."""
    return o if isinstance(o, dict) else {}


def _derive_context(data: dict, image_url: Optional[str],
                    category: Optional[str], s3_client=None) -> DeckCtx:
    data   = _dict(data)
    totals = _dict(data.get("totals"))
    val    = _dict(data.get("valuation"))
    rates  = _dict(val.get("rates"))
    cg     = _dict(data.get("control_group"))
    is_auto = (str(category or "").strip().lower() == "automotive")
    conv = _dict(data.get("conversions"))
    has_conv = (
        not is_auto
        and bool(conv.get("enabled"))
        and not bool(conv.get("low_signal"))
        and _num(conv.get("post_users_projected")) > 0
    )
    yearly = data.get("yearly_breakdown") or []
    has_yearly = isinstance(yearly, list) and len(yearly) >= 2

    pre_pen  = _num(totals.get("audience_pen_pre_pct"))
    post_pen = _num(totals.get("audience_pen_post_pct"))
    delta_pp = round(post_pen - pre_pen, 2)
    delta_rel = ((post_pen - pre_pen) / pre_pen * 100) if pre_pen else 0.0

    # Cover image is placed in a 7.6"×7.5" slot - center-crop to the
    # matching aspect (≈1.013) so faces stay framed and the slot fills
    # cleanly with no letterboxing.
    cover = _download_image(image_url, crop_aspect=7.6 / 7.5, s3_client=s3_client) if image_url else None
    if cover is None:
        cover = _generate_typographic_hero(str(data.get("brand_partner")
                                               or data.get("project_name") or "BPIQ"))

    logo = _render_crosswalk_logo_png(on_dark=True)

    project = str(data.get("project_name") or "Brand Partnership Valuation")
    brand   = str(data.get("brand_partner") or "Brand")
    cat_lbl = (category or "").strip().title() or "Brand Partnership"
    start   = str(data.get("start_date") or "")
    end     = str(data.get("end_date")   or "")
    win     = f"{start} → {end}" if start and end else ""

    # Always project up (Jenna 2026-09-23): the raw observed n never
    # prints on a client-facing surface; the projected universe is the
    # only sample figure a reader sees.
    src = (
        f"Source: Crosswalk BehaviorGraph · 30M+ opted-in U.S. consumer panel · "
        f"projected universe n={_count(data.get('projected_audience_size')):,} · "
        f"window {win} · "
        f"attribution {_count(data.get('attribution_window_days'))}d"
    )

    return DeckCtx(
        project=project,
        brand=brand,
        category_label=cat_lbl,
        is_auto=is_auto,
        cover_image=cover,
        logo=logo,
        total_value=_num(val.get("total_brand_value")),
        emv=_num(val.get("earned_media_value")),
        bev=_num(val.get("brand_engagement_value")),
        blv=_num(val.get("brand_lift_value")),
        cv=_num(val.get("conversion_value")) if has_conv else 0.0,
        incr_users=_count(val.get("incremental_users")),
        target_size=_count(data.get("audience_size")),
        audience_proj=_count(data.get("projected_audience_size")),
        pre_users=_count(totals.get("pre_users")),
        post_users=_count(totals.get("post_users")),
        pre_users_proj=_count(totals.get("pre_users_projected")),
        post_users_proj=_count(totals.get("post_users_projected")),
        pre_pen=pre_pen,
        post_pen=post_pen,
        delta_pp=delta_pp,
        delta_rel=delta_rel,
        incr_lift_pp=_num(cg.get("incremental_lift_pp")),
        # the adjusted lift the deck argues from is the matched lift as a
        # share of the exposed group's own baseline (the ratio the house
        # attributable formula uses); against the comparison group's tiny
        # drift it read as thousands of percent (2026-10-28)
        incr_lift_rel=_adjusted_lift_rel(cg),
        campaign_window=win,
        attribution_d=_count(data.get("attribution_window_days")),
        panel_size=_count(data.get("audience_size")),
        sample_pre=_count(totals.get("pre_users")),
        sample_post=_count(totals.get("post_users")),
        has_conversions=has_conv,
        has_yearly=has_yearly,
        sources_line=src,
        data=data,
    )


_MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _fmt_date(iso: str) -> str:
    """YYYY-MM-DD → 'DD Mon YYYY' for slide copy. Returns the input
    unchanged if parsing fails."""
    if not iso or not isinstance(iso, str):
        return ""
    parts = iso.split("-")
    if len(parts) != 3:
        return iso
    try:
        yyyy, mm, dd = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{dd:02d} {_MONTHS_SHORT[mm-1]} {yyyy}"
    except Exception:
        return iso


def _period_label(period: dict, fallback: str = "") -> str:
    """Human-readable window label from a {start, end, days} block.
    'from DD Mon YYYY to DD Mon YYYY (N days)'. Used on the
    engagement-lift slide to avoid the C2 mislabel (calling a 15-day
    window 'the year before campaign launch')."""
    if not isinstance(period, dict):
        return fallback
    start = _fmt_date(str(period.get("start") or ""))
    end   = _fmt_date(str(period.get("end")   or ""))
    days  = period.get("days")
    if start and end:
        # the window length leads and the dates close the sentence, so the
        # last line never ends on a lone "window)."
        if isinstance(days, (int, float)) and days > 0:
            return f"across the {int(days)} days from {start} to {end}"
        return f"from {start} to {end}"
    return fallback


def _engagement_headline(ctx: DeckCtx) -> str:
    """MIDG-style declarative headline that names the lift, the
    consumer count, and the brand.

    Uses adaptive precision on the pp delta (C9): sub-percent deltas
    display with 2 decimals so a +0.15pp lift never renders as
    "0.1pt" while the pre/post percentages show 0.2pp apart. Two-line
    sentence with terminal punctuation on both lines."""
    if ctx.delta_pp <= 0:
        return (
            f"The campaign held {ctx.brand}'s baseline.\n"
            f"{fmt_num(ctx.post_users_proj)} consumers engaged with the brand "
            f"in the post-window."
        )
    # Adaptive precision: below 1pp shows 2 decimals; at/above 1pp
    # shows 1 decimal. Guarantees the headline delta never contradicts
    # the pre/post display precision (fixes C9).
    if abs(ctx.delta_pp) < 1.0:
        delta_str = f"{ctx.delta_pp:.2f}pt"
    else:
        delta_str = f"{ctx.delta_pp:.1f}pt"
    moved = max(0, ctx.post_users_proj - ctx.pre_users_proj)
    return (
        f"A {delta_str} lift in brand engagement,\n"
        f"moving {fmt_num(moved)} more consumers toward {ctx.brand}."
    )


def _cover_subtitle(ctx: DeckCtx) -> str:
    """Purpose-written cover subtitle. Two lines: category + a
    declarative summary describing what this deck measures. Always
    ends with terminal punctuation so it never renders as a truncated
    fragment (fixes C7)."""
    diag = ctx.data.get("diagnostics") or {}
    is_counterfactual = "counterfactual" in (ctx.project or "").lower()
    label = ctx.category_label or "Brand Partnership"
    if is_counterfactual:
        line1 = f"{label} · Counterfactual read."
        line2 = (
            f"Isolates the sponsorship signal by measuring {ctx.brand} "
            f"against the same audience and window as the treated brand."
        )
    else:
        # Standard partnership read - lead with the total value we
        # observed so the cover has a hook, without leaning on the
        # slide-4 headline which uses a comma-ended fragment.
        line1 = f"{label} · Total observed value: {fmt_money(ctx.total_value)}."
        line2 = (
            f"An analysis of {ctx.brand}'s consumer engagement across "
            f"the campaign window."
        )
    return f"{line1}\n{line2}"


def _final_insight_lines(ctx: DeckCtx) -> tuple[str, str]:
    """Two-line editorial close - mirrors LISA's 'LISA wears it, it
    sells out.' format. We synthesize from the data so every partnership
    gets a tuned message.

    Counterfactual brands (project name contains 'counterfactual')
    always route to the control-brand copy regardless of adjusted-
    lift sign. Small positive drift on a control brand is category
    noise, not a sponsorship effect (Liz's July 27 v2 inversion
    callout: Pepsi F1 was mislabeled 'consistent with a genuine
    but small sponsorship effect' when Pepsi was not sponsored).

    Guardrail: never claim compounding lift over time on a single-
    window run."""
    if ctx.total_value <= 0:
        return ("The partnership ran.", "The audience didn't move.")
    lift_rel = ctx.incr_lift_rel if ctx.incr_lift_rel is not None else ctx.delta_rel
    sig      = (ctx.data.get("diagnostics") or {}).get("significance") or {}
    is_sig   = bool(sig.get("significant"))
    is_counterfactual = "counterfactual" in (ctx.project or "").lower()
    # Counterfactual brands always read as controls, regardless of
    # sign or significance of the tiny residual drift.
    if is_counterfactual:
        return (
            "The campaign held baseline.",
            f"{ctx.brand} was not in the show, and moved with control "
            "drift. Any residual is category-wide noise, not "
            "sponsorship-attributable.",
        )
    if lift_rel <= 0:
        return (
            "The campaign held baseline.",
            f"{ctx.brand}'s adjusted lift falls at or below control "
            "drift.",
        )
    if not is_sig:
        return (
            f"A directional {lift_rel:.1f}% adjusted lift,",
            "not statistically distinguishable from zero at this sample "
            "size. The signal is consistent with a small but genuine "
            "effect from this sponsorship.",
        )
    # Extract the talent / qualifier name from the project title
    # (split on the LISA-style '×' character; fall back to 'the
    # talent' if the project isn't shaped as 'Talent × Brand').
    talent = ctx.project.split("×")[0].strip() if "×" in ctx.project else ""
    if lift_rel >= 200:
        line1 = f"With {ctx.brand} in play,"
        if talent:
            line2 = f"the audience more than tripled around {talent}: a {lift_rel:.0f}% adjusted lift."
        else:
            line2 = f"the audience more than tripled: a {lift_rel:.0f}% adjusted lift."
    elif lift_rel >= 100:
        line1 = f"{ctx.brand} more than doubled"
        line2 = "share of voice across the audience."
    elif lift_rel >= 25:
        line1 = f"{ctx.brand} won meaningful"
        line2 = f"new ground: {lift_rel:.0f}% adjusted lift, post-campaign."
    else:
        if ctx.has_yearly:
            line1 = "Steady adjusted lift,"
            line2 = "compounding reach year over year."
        else:
            line1 = f"A {lift_rel:.1f}% adjusted lift,"
            line2 = "above a matched group that started where they did."
    return (line1, line2)


# ─────────────────────────────────────────────────────────────────────
#  Slide builders. Each takes (prs, ctx, idx, total) and returns the
#  new slide. Indexed manually because we need explicit `idx / total`
#  in every footer.
# ─────────────────────────────────────────────────────────────────────
def _blank(prs):
    """python-pptx ships several layouts; the 'blank' one (typically
    index 6) is the cleanest base because there are no placeholder
    text boxes to fight."""
    layout = prs.slide_layouts[6]
    return prs.slides.add_slide(layout)


def _slide_cover(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    # Right-hand hero image fills a 7.6×7.5 slot (cropped to that
    # aspect during download so the photo isn't deformed here).
    if ctx.cover_image:
        try:
            pic = s.shapes.add_picture(
                ctx.cover_image,
                Inches(5.8), Inches(0),
                width=Inches(7.6), height=Inches(7.5),
            )
            _crop_to_fill(pic)
        except Exception:
            pass
    # Left-hand title stack. Title font size is scaled to length so
    # long partnerships (e.g. "Penélope Cruz x CHANEL Endorsement")
    # still fit inside the available block without crashing into the
    # subtitle below.
    _add_text(s, Inches(0.6), Inches(0.6), Inches(5.2), Inches(0.4),
              "A BRAND PARTNERSHIP VALUATION · 2026",
              size=10, bold=True, color=MUTED_DK, letter_spacing=0.2)
    title_text = _normalize_project_label(ctx.project) + "."
    title_len  = len(title_text)
    if   title_len <= 20: title_size = 64
    elif title_len <= 30: title_size = 52
    elif title_len <= 42: title_size = 42
    else:                 title_size = 34
    # the length tier is the ceiling; the title steps down from it until
    # it fits its block and ends on a line of two words (a long
    # partnership name left one word alone on its last line)
    title_size = _fit_title(title_text, tuple(range(title_size, 25, -2)), 5.0, 3.6)
    title = _add_text(s, Inches(0.6), Inches(1.7), Inches(5.0), Inches(3.6),
                      title_text,
                      size=title_size, bold=True, color=FG_LIGHT,
                      spacing=0.95, font=FONT_DISPLAY)
    title_bottom = Inches(1.7) + Inches(round(
        _measure(title_text, title_size, Emu(title.width).inches, bold=True, spacing=0.95).height_in, 3))
    # Subtitle - purpose-written, not derived from slide 4's split
    # headline (fixes C7: previously the subtitle pulled the first
    # line of the two-line engagement headline which ended on a
    # comma, so the cover subtitle rendered as a truncated fragment).
    # Two-line format: category + declarative one-line summary; the
    # summary carries terminal punctuation so it reads as a complete
    # thought at any font size.
    #
    # The block sits on the DRAFT stamp's top edge and rises from there
    # (2026-10-29: a long category or total made it three lines, and the
    # third landed on the stamp); it steps down to 12pt and then drops
    # its reading before it would reach the title.
    subtitle = _cover_subtitle(ctx)
    stamp_top = Inches(6.5)
    _add_body(s, Inches(0.6), title_bottom + Inches(0.15), Inches(5.0), subtitle,
              max_height=stamp_top - Inches(0.08) - (title_bottom + Inches(0.15)),
              bottom=stamp_top - Inches(0.08),
              size=15, floor=12, spacing=1.25, min_spacing=1.1,
              color=FG_LIGHT, font=FONT_BODY, anchor="bottom")
    # DRAFT date stamp - explicitly names the analysis build date so
    # a reader can tell whether they're holding an old draft or the
    # July 27 v2 rebuild (C7 mentions the DRAFT date line went
    # missing from the rerun cover; restored here).
    created = str(ctx.data.get("created_at") or "")[:10]
    if created:
        _add_text(s, Inches(0.6), stamp_top, Inches(5.0), Inches(0.25),
                  f"DRAFT · {created}",
                  size=9, bold=True, color=MUTED_DK, letter_spacing=0.2)
    # CONFIDENTIAL stamp top right
    _add_text(s, Inches(11.4), Inches(0.6), Inches(1.4), Inches(0.3),
              "CONFIDENTIAL",
              size=9, bold=True, color=MUTED_DK, align="right",
              letter_spacing=0.2)
    # Bottom-left logo (rendered PNG) + brand mark
    try:
        s.shapes.add_picture(ctx.logo, Inches(0.6), Inches(6.7),
                             height=Inches(0.45))
    except Exception:
        _add_text(s, Inches(0.6), Inches(6.95), Inches(5), Inches(0.3),
                  "CROSSWALK · BehaviorGraph",
                  size=11, bold=True, color=FG_LIGHT, letter_spacing=0.18)
    return s


def _section_label(idx: int, title: str) -> str:
    """Consistent 'NN · Section Title' eyebrow, driven off idx so we
    never desynchronize the section counter from the actual slide
    position (fixes the classic 10→12 jump when an optional slide is
    skipped)."""
    return f"{idx:02d} · {title}"


def _slide_methodology(prs, ctx: DeckCtx, idx: int, total: int):
    """MIDG 'Direct Observational Data' slide - credibility / what
    makes BehaviorGraph trustworthy."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "The Data Foundation"), on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.4),
              "Direct observational data.\nNo surveys, no self-report.",
              size=44, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    # Dashes in the body copy get normalized to commas so they don't
    # regress the item-20 dash cleanup (C11).
    # Generic across every dataset (a Wheel of Fortune cohort name was
    # hardcoded here until 2026-09-23 and leaked onto other brands'
    # decks), and projected up: no raw panelist count in client copy.
    body = (
        "Crosswalk's BehaviorGraph draws on a 30M+ opted-in U.S. consumer "
        "panel. For this partnership we measured the audience across the "
        "pre, event, and post windows and projected every count in this "
        f"deck to its {fmt_num(ctx.audience_proj)}-consumer U.S. audience "
        "universe. Every observation reflects real behavior: which sites "
        "they visited, which platforms they spent time on, and which "
        "brand touchpoints appeared in their sessions before, during, "
        "and after the partnership ran."
    )
    _body, body_h = _add_body(s, Inches(0.6), Inches(3.0), Inches(7), body,
                              max_height=Inches(2.2), size=13, floor=11,
                              spacing=1.32, min_spacing=1.2, color=FG_DARK)
    # Study-context clarifier line - C3 fix. When the payload
    # carries a diagnostics.study_context field (rerun analyses,
    # counterfactuals, etc.), print it as an italic footnote so no
    # reader can misread this as the first-flight deck.
    study_ctx = ((ctx.data.get("diagnostics") or {})
                 .get("study_context"))
    # The campaign in plain words (2026-10-28): the collection's price
    # band, the sell-out and what one order carried, as the payload's
    # own read. No source is named.
    camp_line = str(((ctx.data.get("campaign") or {}).get("read_line")) or "").strip()
    reach_line = str(((ctx.data.get("campaign") or {}).get("reach_line")) or "").strip()
    foot = " ".join(x for x in (reach_line, camp_line, str(study_ctx or "").strip()) if x)
    if foot:
        # below the body, above the footer; a long campaign read steps
        # down to 9pt and then loses its trailing sentences
        foot_top = max(Inches(5.35), Inches(3.0) + body_h + Inches(0.15))
        _add_body(s, Inches(0.6), foot_top, Inches(7), foot,
                  max_height=Inches(CONTENT_BOTTOM) - foot_top,
                  size=10, floor=NOTE_FLOOR, spacing=1.3, min_spacing=1.15,
                  italic=True, color=MUTED_LT)
    # Right column - 3 stat tiles. Window tile uses the actual pre /
    # event / post dates so slide 2 never claims a window it doesn't
    # cover (C2 + C3 defensive coverage).
    pre_p  = ctx.data.get("pre_period")   or {}
    post_p = ctx.data.get("post_period")  or {}
    window_val = (
        f"{_fmt_date(pre_p.get('start') or '')} to "
        f"{_fmt_date(post_p.get('end')   or '')}"
    ) if pre_p and post_p else (ctx.campaign_window or "0")
    stats = [
        ("Projected sample", f"{fmt_num(ctx.audience_proj)}",
         "U.S. audience universe"),
        ("Window",       window_val,                    "Pre, event, post windows"),
        ("Attribution",  f"{ctx.attribution_d}d",       "Post-event window"),
    ]
    col_x = Inches(8.2)
    col_w = Inches(4.6)
    for i, (label, value, sub) in enumerate(stats):
        y = Inches(3.2 + i * 1.15)
        _add_rect(s, col_x, y, col_w, Inches(1.0),
                  fill=None, line=FG_DARK, line_w=0.75)
        _add_text(s, col_x + Inches(0.25), y + Inches(0.1),
                  Inches(2.0), Inches(0.3),
                  label.upper(), size=9, bold=True, color=MUTED_LT,
                  letter_spacing=0.16)
        _add_text(s, col_x + Inches(0.25), y + Inches(0.32),
                  Inches(col_w.inches - 0.5), Inches(0.4),
                  value, size=_fit_stat(value, col_w.inches - 0.5, 22, floor=14),
                  bold=True, color=FG_DARK, font=FONT_NUMERIC)
        _add_text(s, col_x + Inches(0.25), y + Inches(0.72),
                  Inches(col_w.inches - 0.5), Inches(0.3),
                  sub, size=9, color=MUTED_LT, italic=True)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _brand_in_title(brand: str) -> str:
    """Format a brand name for use in a LISA-style slide title.
    Skips the leading 'The' article when the brand label already
    reads as a possessive ('Lainey Wilson's...') or a plural / group
    noun ('Lainey's Brands', 'Portfolio', 'Endorsements') where 'The
    Foo' would read awkwardly. Single-brand labels ('Coca-Cola',
    'CHANEL') keep the LISA 'The X Audience' phrasing."""
    b = str(brand or "").strip()
    if not b:
        return "The Audience"
    starts_with_article = b.lower().startswith(("the ", "a ", "an "))
    has_possessive = "'s " in b or b.endswith("'s")
    portfolio_words = ("portfolio", "brands", "endorsements", "roster")
    is_portfolio = any(w in b.lower() for w in portfolio_words)
    if starts_with_article or has_possessive or is_portfolio:
        return f"{b} Audience · Scale"
    return f"The {b} Audience · Scale"


def _slide_audience_scale(prs, ctx: DeckCtx, idx: int, total: int):
    """LISA-style 'THE LISA AUDIENCE · SCALE' big stat slide."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, _brand_in_title(ctx.brand)),
             on_dark=True)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"Reach and resonance,\nside by side.",
              size=44, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    # Three giant stat tiles
    tiles = [
        (fmt_num(ctx.audience_proj),
         "U.S. CONSUMER AUDIENCE",
         "The partnership's audience universe, projected from the "
         "30M+ Crosswalk panel."),
        (fmt_num(ctx.post_users_proj),
         f"POST-CAMPAIGN {ctx.brand.upper()} CONSUMERS",
         f"{fmt_pct(ctx.post_pen)} of the observed cohort had a brand "
         f"touchpoint in the post-window."),
        (fmt_money(ctx.total_value),
         "TOTAL BRAND VALUE DELIVERED",
         f"Sum of Earned Media, Brand Engagement, "
         f"{'Brand Lift, and Conversion' if ctx.has_conversions else 'and Brand Lift'} value."),
    ]
    tile_w = Inches(4.0)
    gutter = Inches(0.15)
    base_x = Inches(0.6)
    tile_h = Inches(3.1)
    for i, (num, label, sub) in enumerate(tiles):
        x = base_x + (tile_w + gutter) * i
        y = Inches(3.4)
        _add_rect(s, x, y, tile_w, tile_h,
                  fill=None, line=MUTED_DK, line_w=0.5)
        # the stat holds one line at the largest size the tile allows
        # (2026-10-29: a 14-character total at the old length tier broke
        # its last digit onto a second line)
        _add_text(s, x + Inches(0.25), y + Inches(0.25),
                  tile_w - Inches(0.5), Inches(1.4),
                  num, size=_fit_stat(num, tile_w.inches - 0.5, 54, floor=20),
                  bold=True, color=ACCENT, font=FONT_NUMERIC, spacing=0.95)
        _add_text(s, x + Inches(0.25), y + Inches(1.7),
                  tile_w - Inches(0.5), Inches(0.4),
                  label, size=10, bold=True, color=FG_LIGHT,
                  letter_spacing=0.14)
        _add_body(s, x + Inches(0.25), y + Inches(2.1),
                  tile_w - Inches(0.5), sub,
                  max_height=tile_h - Inches(2.1 + 0.15),
                  size=10, floor=NOTE_FLOOR, spacing=1.3, min_spacing=1.15,
                  color=MUTED_DK)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_engagement_lift(prs, ctx: DeckCtx, idx: int, total: int):
    """MIDG centerpiece - 'A 3.2pt Lift That Moved $19M' headline +
    Pre vs Post stat blocks + Read As callout."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, f"{ctx.brand} · Engagement Lift"),
             on_dark=False)
    headline_lines = _engagement_headline(ctx).split("\n")
    # the claim holds one line where it can (a long brand name steps it
    # down); its reading follows the measured claim, never a fixed row
    size1 = _fit_lines(headline_lines[0], (42, 40, 38, 36, 34, 32), 11, bold=True, max_lines=1)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.0),
              headline_lines[0],
              size=size1, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    h1 = _measure(headline_lines[0], size1, 11, bold=True, spacing=0.95).height_in
    if len(headline_lines) > 1:
        y2 = max(Inches(2.05), Inches(1.0 + h1 + 0.25))
        _add_body(s, Inches(0.6), y2, Inches(11), headline_lines[1],
                  max_height=Inches(3.3) - y2,
                  size=22, floor=16, color=FG_DARK, font=FONT_DISPLAY)
    # Pre / Post visual comparison - two big stat plates side by side
    plate_y = Inches(3.4)
    plate_w = Inches(5.2)
    plate_h = Inches(2.8)
    # Pre plate
    _add_rect(s, Inches(0.6), plate_y, plate_w, plate_h,
              fill=FG_DARK, line=None)
    _add_text(s, Inches(0.9), plate_y + Inches(0.25), Inches(2), Inches(0.3),
              "PRE-CAMPAIGN", size=10, bold=True, color=ACCENT,
              letter_spacing=0.2)
    _add_text(s, Inches(0.9), plate_y + Inches(0.6), plate_w - Inches(0.5),
              Inches(1.3),
              fmt_pct(ctx.pre_pen), size=70, bold=True, color=FG_LIGHT,
              font=FONT_NUMERIC, spacing=0.95)
    # Pre-plate sub-label = the actual pre-window from the payload
    # (fixes C2: prior copy said "in the year before campaign launch"
    # which mislabels the trailing pre-period as a 12-month window).
    #
    # The numerator-and-denominator disclosure line (Liz July 27 v2
    # C1) stays, but PROJECTED UP per Jenna 2026-09-23 ("make sure it
    # is always projecting up"): raw panelist counts never print on a
    # client-facing slide. Both sides of the ratio are stated at the
    # U.S. audience universe, so the percentage still audits directly.
    pre_p  = ctx.data.get("pre_period")  or {}
    post_p = ctx.data.get("post_period") or {}
    pre_label = _period_label(pre_p, fallback="in the pre-window")
    post_label = _period_label(post_p, fallback=f"in the {ctx.attribution_d}-day post-window")
    pre_incidence  = (f"of the {fmt_num(ctx.audience_proj)}-consumer "
                      "audience universe (incidence rate)")
    post_incidence = (f"of the {fmt_num(ctx.audience_proj)}-consumer "
                      "audience universe (incidence rate)")
    _add_text(s, Inches(0.9), plate_y + Inches(1.85), plate_w - Inches(0.5),
              Inches(0.35),
              f"{fmt_num(ctx.pre_users_proj)} U.S. consumers projected",
              size=13, color=FG_LIGHT)
    _add_text(s, Inches(0.9), plate_y + Inches(2.15), plate_w - Inches(0.5),
              Inches(0.30),
              pre_incidence,
              size=10, italic=True, color=MUTED_DK)
    _add_body(s, Inches(0.9), plate_y + Inches(2.42), plate_w - Inches(0.5),
              f"engaged with the brand {pre_label}.",
              max_height=plate_h - Inches(2.42 + 0.02),
              size=10, floor=NOTE_FLOOR, italic=True, color=MUTED_DK)
    # Post plate
    post_x = Inches(7.2)
    _add_rect(s, post_x, plate_y, plate_w, plate_h,
              fill=ACCENT, line=None)
    _add_text(s, post_x + Inches(0.3), plate_y + Inches(0.25),
              Inches(2), Inches(0.3),
              "POST-CAMPAIGN", size=10, bold=True, color=FG_DARK,
              letter_spacing=0.2)
    _add_text(s, post_x + Inches(0.3), plate_y + Inches(0.6),
              plate_w - Inches(0.5), Inches(1.3),
              fmt_pct(ctx.post_pen), size=70, bold=True, color=FG_DARK,
              font=FONT_NUMERIC, spacing=0.95)
    _add_text(s, post_x + Inches(0.3), plate_y + Inches(1.85),
              plate_w - Inches(0.5), Inches(0.35),
              f"{fmt_num(ctx.post_users_proj)} U.S. consumers projected",
              size=13, color=FG_DARK)
    _add_text(s, post_x + Inches(0.3), plate_y + Inches(2.15),
              plate_w - Inches(0.5), Inches(0.30),
              post_incidence,
              size=10, italic=True, color=FG_DARK)
    _add_body(s, post_x + Inches(0.3), plate_y + Inches(2.42),
              plate_w - Inches(0.5),
              f"engaged with the brand {post_label}.",
              max_height=plate_h - Inches(2.42 + 0.02),
              size=10, floor=NOTE_FLOOR, italic=True, color=FG_DARK)
    # Read As callout + significance line, MIDG-style.
    moved = max(0, ctx.post_users_proj - ctx.pre_users_proj)
    read_as = (
        f"Read As: {fmt_num(moved)} more U.S. consumers were observed "
        f"engaging with {ctx.brand} after the campaign, a "
        f"+{ctx.delta_rel:.1f}% relative lift over baseline."
    )
    _read, read_h = _add_body(s, Inches(0.6), Inches(6.25), Inches(12.1), read_as,
                              max_height=Inches(0.42), size=12, floor=NOTE_FLOOR,
                              italic=True, color=MUTED_LT, align="center")
    # Statistical significance line - pooled two-sample z on paired
    # marginals (same-panel pre/post design). We display n, delta,
    # z, and a 95% CI on the pp delta. See methodology slide.
    sig = _mcnemar_summary(ctx)
    if sig:
        sig_top = max(Inches(6.62), Inches(6.25) + read_h + Inches(0.08))
        _add_body(s, Inches(0.6), sig_top, Inches(12.1), sig,
                  max_height=Inches(CONTENT_BOTTOM) - sig_top,
                  size=10, floor=NOTE_FLOOR, italic=True, color=MUTED_LT, align="center")
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _mcnemar_summary(ctx: DeckCtx) -> str:
    """Compose the on-slide significance strip from the payload's
    diagnostics.significance block.

    Per July 27 QC memo (C6): at large panel n the p-value carries
    no information (any Δ above ~0.04pp clears p=0.05), so on
    n>=1e6 panels we drop the p-value entirely and report point
    estimate, z, and the 95% CI. On smaller panels we keep p for
    readers who want it.

    Recomputes z and CI from panel size + observed pre/post counts
    when possible, so a stale-denominator payload still renders the
    right numbers on the corrected panel base."""
    from math import erf, sqrt

    diag  = (ctx.data.get("diagnostics") or {}).get("significance") or {}
    n_pre  = ctx.sample_pre
    n_post = ctx.sample_post
    n_panel = ctx.panel_size

    # Recompute on the panel base actually declared in the payload,
    # rather than trusting a possibly-stale precomputed z/CI written
    # against a different denominator.
    z_calc, se_pp_calc, delta_pp_calc = None, None, None
    if n_pre and n_post and n_panel:
        p_pool = (n_pre + n_post) / (2 * n_panel)
        se = sqrt(2 * p_pool * (1 - p_pool) / n_panel)
        delta = (n_post - n_pre) / n_panel
        z_calc = delta / se if se else 0
        se_pp_calc = se * 100
        delta_pp_calc = delta * 100

    # Prefer recomputed z/CI/Δ. Fall back to payload numbers only if
    # the recompute fails.
    if delta_pp_calc is not None:
        delta_pp = delta_pp_calc
        z        = z_calc
        ci       = [delta_pp - 1.96 * se_pp_calc, delta_pp + 1.96 * se_pp_calc]
    else:
        delta_pp = diag.get("delta_pp_point")
        z        = diag.get("primary_test_z")
        ci       = diag.get("delta_ci_95_pp") or diag.get("ci_95_pp")

    # Significance flag: recomputed z if available, else the payload
    # flag (which is only correct on the payload's declared base).
    if z is not None:
        sig = abs(z) >= 1.96
    else:
        sig = bool(diag.get("significant"))

    # No raw panel n on the strip (always project up, 2026-09-23);
    # the z/CI math still runs on the observed base internally.
    parts = []
    if delta_pp is not None:
        parts.append(f"Δ={float(delta_pp):+.2f}pp")
    if z is not None:
        parts.append(f"z={float(z):+.2f}")
    # Drop p when n is large enough that p becomes uninformative
    # (item C6: detection floor at n=1e7 is ~0.04pp; p is not a
    # useful column on a client-facing slide at this scale).
    if n_panel < 1_000_000:
        p = diag.get("primary_test_p_value")
        if p is None and z is not None:
            p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
        if p is not None:
            # "p=0.000" reads as a bug; print the conventional floor.
            parts.append("p<0.001" if float(p) < 0.0005
                         else f"p={float(p):.3f}")
    if ci:
        try:
            lo, hi = float(ci[0]), float(ci[1])
            parts.append(f"95% CI on Δ: [{lo:+.2f}pp, {hi:+.2f}pp]")
        except Exception:
            pass
    parts.append("SIGNIFICANT" if sig else "not distinguishable from zero")
    return " · ".join(parts)


def _slide_total_value_hero(prs, ctx: DeckCtx, idx: int, total: int):
    """Hero $ slide - LISA's 'ONE INSIGHT' moment, repurposed for the
    total brand value. Solid pink so it explodes off the page after the
    cream lift slide."""
    s = _blank(prs)
    _add_bg(s, BG_PINK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "The Headline"), on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.4), Inches(12), Inches(1.3),
              "Total Brand Value Observed.",
              size=44, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    # The big number - exact dollars, size stepping down for long values
    hero_txt = fmt_money(ctx.total_value)
    _add_text(s, Inches(0.6), Inches(2.5), Inches(12), Inches(2.0),
              hero_txt,
              size=_fit_stat(hero_txt, 12.0, 150, floor=56), bold=True, color=FG_DARK,
              font=FONT_NUMERIC, spacing=0.85, align="left")
    # "Attributable to Partnership" subtotal.
    # 2026-09-01 house standard: BLV + CV × min(1.0, max(0.0,
    # adj_lift_pp / pre_baseline_pct)). See
    # bg-webapp/migration/bpiq_attributable.py and
    # .cursor/rules/bpiq-attributable-formula.mdc.
    #
    # Priority: read the payload's precomputed value first (keeps
    # every shipped payload byte-stable when rebuilt); recompute via
    # the helper when the payload carries the ingredients but not
    # the field; fall back to BLV + full CV only for legacy payloads
    # that pre-date the strict-attributable field entirely.
    from .bpiq_attributable import resolve_attributable_from_payload
    attributable = float(resolve_attributable_from_payload(
        ctx.data,
        fallback_blv=ctx.blv,
        fallback_cv=ctx.cv,
        fallback_has_conversions=ctx.has_conversions,
    ))
    # the two figures hold one line each; their readings fit the room
    # above the four tiles (6.05in) or lose a trailing sentence
    tiles_top = Inches(6.05)
    note_top = Inches(5.55)
    attributable_txt = fmt_money(attributable)
    _add_text(s, Inches(0.6), Inches(4.75), Inches(6.5), Inches(0.35),
              "OF WHICH ATTRIBUTABLE TO PARTNERSHIP",
              size=10, bold=True, color=FG_DARK, letter_spacing=0.16)
    _add_text(s, Inches(0.6), Inches(5.05), Inches(6.5), Inches(0.5),
              attributable_txt,
              size=_fit_stat(attributable_txt, 6.5, 32, floor=18), bold=True, color=FG_DARK,
              font=FONT_NUMERIC)
    _add_body(s, Inches(0.6), note_top, Inches(6.5),
              "Brand Lift plus the adjusted-lift share of Conversion "
              "Value (strictly incremental). Brand Engagement and "
              "Earned Media price the observed audience footprint, "
              "which includes pre-existing brand interest.",
              max_height=tiles_top - note_top - Inches(0.05),
              size=9, floor=NOTE_FLOOR, spacing=1.3, min_spacing=1.1,
              italic=True, color=FG_DARK)
    # Her share (2026-10-28): the talent's share of the campaign's value
    # from the assets that carried her and the matched group.
    _val = ctx.data.get("valuation") or {}
    _ts = _val.get("talent_share")
    _tv = _val.get("attributable_to_talent")
    if _ts is not None and _tv:
        _who = str(((ctx.data.get("qualifier_value") or [""]) or [""])[0] or "the talent")
        share_txt = f"{fmt_money(float(_tv))}  ({float(_ts) * 100:.0f}%)"
        _add_text(s, Inches(7.3), Inches(4.75), Inches(5.3), Inches(0.35),
                  f"OF WHICH {_who.upper()}'S SHARE",
                  size=10, bold=True, color=FG_DARK, letter_spacing=0.16)
        _add_text(s, Inches(7.3), Inches(5.05), Inches(5.3), Inches(0.5),
                  share_txt,
                  size=_fit_stat(share_txt, 5.3, 32, floor=18), bold=True, color=FG_DARK,
                  font=FONT_NUMERIC)
        _add_body(s, Inches(7.3), note_top, Inches(5.3),
                  str(_val.get("talent_share_basis") or "").strip().capitalize() + ".",
                  max_height=tiles_top - note_top - Inches(0.05),
                  size=9, floor=NOTE_FLOOR, spacing=1.3, min_spacing=1.1,
                  italic=True, color=FG_DARK)
    # 4-up breakdown bar across the bottom
    parts = [
        ("Earned Media",      ctx.emv),
        ("Brand Engagement",  ctx.bev),
        ("Brand Lift",        ctx.blv),
    ]
    if ctx.has_conversions:
        parts.append(("Conversion", ctx.cv))
    tile_w = Inches(12.0 / len(parts))
    base_x = Inches(0.6)
    base_y = tiles_top
    for i, (label, value) in enumerate(parts):
        x = base_x + tile_w * i + Inches(0.05 if i > 0 else 0)
        _add_rect(s, x, base_y, tile_w - Inches(0.1), Inches(1.0),
                  fill=FG_DARK, line=None)
        _add_text(s, x + Inches(0.25), base_y + Inches(0.1),
                  tile_w - Inches(0.4), Inches(0.3),
                  label.upper(), size=9, bold=True, color=ACCENT,
                  letter_spacing=0.16)
        _add_text(s, x + Inches(0.25), base_y + Inches(0.4),
                  tile_w - Inches(0.4), Inches(0.5),
                  fmt_money(value),
                  size=_fit_stat(fmt_money(value), tile_w.inches - 0.4, 20, floor=12),
                  bold=True, color=FG_LIGHT, font=FONT_NUMERIC)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


#: Fixed platform roster used across ALL decks. Any brand run that
#: doesn't register a given platform is shown as a zero row rather
#: than being dropped, so two brands measured against the same panel
#: cannot appear to have different rosters (item 15).
#:
#: LinkedIn is intentionally excluded from the display roster - it is
#: a B2B platform with negligible signal in consumer brand
#: partnerships. It stays in the underlying rate table for future
#: B2B runs but is not shown by default.
FIXED_PLATFORM_ROSTER = [
    "Instagram", "TikTok", "YouTube", "Facebook", "X (Twitter)",
    "Reddit", "Pinterest", "Snapchat", "Threads", "Twitch",
    "Direct (Brand Site)",
]

#: Per-platform source attribution for the 2026 CPM rate card. This
#: is printed on the EMV slide so procurement can trace every rate
#: back to a specific, credible benchmark instead of a generic
#: "Hootsuite / Sprout / LinkedIn" boilerplate line (item 14).
CPM_SOURCE_ATTRIBUTION = {
    "Instagram":  "Hootsuite Social Media Benchmark Report 2026",
    "Facebook":   "Hootsuite Social Media Benchmark Report 2026",
    "TikTok":     "Sprout Social 2026 Q1 Benchmarks",
    "YouTube":    "IAB / SMI 2026 CTV+Video Benchmarks",
    "X (Twitter)":"eMarketer 2026 X Ad Rate Guide",
    "Reddit":     "Reddit for Business 2026 Ratecard",
    "Pinterest":  "Hootsuite Social Media Benchmark Report 2026",
    "Snapchat":   "Sprout Social 2026 Q1 Benchmarks",
    "Threads":    "eMarketer 2026 Threads Ad Rate Guide",
    "Twitch":     "Amazon Ads Streaming Benchmarks 2026",
    "LinkedIn":   "LinkedIn Marketing Solutions 2026 Ratecard",
    "Direct (Brand Site)":
        "IAB Display / owned-property engagement equivalent (not paid social)",
    "Press": "Editorial earned-media equivalent, per reader reached",
    "Launch event": "Experiential earned-media equivalent, per person reached",
}


def _slide_emv(prs, ctx: DeckCtx, idx: int, total: int):
    """Per-platform earned media breakdown - LISA index-list pattern.
    Each row: PLATFORM · incremental consumers · $/Consumer · EMV.

    Column labels: we use "$/Consumer" rather than "CPM" because the
    calculation multiplies incremental engaged consumers by a
    per-consumer valuation rate calibrated against 2026 platform
    CPMs at typical exposure frequency. It is a CPM-equivalent
    open-market value per attributable consumer, not a per-mille
    impression rate."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "Earned Media Value"), on_dark=True)
    emv_head = f"{fmt_money(ctx.emv)} of earned media, platform by platform."
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              emv_head,
              size=_fit_lines(emv_head, (40, 38, 36, 34, 32, 30), 11, bold=True, max_lines=2),
              bold=True, color=FG_LIGHT, spacing=0.95, font=FONT_DISPLAY)
    # Build a fixed-roster row table so both decks always show the
    # same 8 platforms in the same order, with zeros where a
    # platform did not register (fixes item 15's "the roster was
    # chosen after seeing the results" read).
    all_rows = (ctx.data.get("valuation") or {}).get("emv_breakdown") or []
    by_platform = {(r.get("platform") or "").strip(): r for r in all_rows}
    # Show the full fixed roster (11 platforms). Row height is
    # tightened to keep the whole table within the slide grid.
    # the press and the launch event join the roster when the campaign
    # earned them (2026-10-28); other decks keep the fixed 11 rows
    display_roster = list(FIXED_PLATFORM_ROSTER) + [
        p for p in ("Press", "Launch event") if float((by_platform.get(p) or {}).get("emv_value") or 0) > 0]
    rows = []
    for plat in display_roster:
        r = by_platform.get(plat, {})
        rows.append({
            "platform": plat,
            "incremental_users_projected":
                int(r.get("incremental_users_projected") or 0),
            "emv_per_user_rate":
                float(r.get("emv_per_user_rate") or 0),
            "emv_value":
                float(r.get("emv_value") or 0),
        })
    if not any(r["emv_value"] for r in rows):
        _add_text(s, Inches(0.6), Inches(4), Inches(11), Inches(0.6),
                  "No platform-level signal in this window.",
                  size=14, italic=True, color=MUTED_DK)
        _page_footer(s, idx, total, ctx.project, on_dark=True)
        return s
    # Grid: PLATFORM at 0.6, INCR CONSUMERS at 3.4, $/CONSUMER at
    # 5.6, EMV at 7.4. The rightmost 2.4in (10.0 → 12.4) hosts the
    # WHY IT MATTERS callout. Column widths and starts are computed
    # up front so no header can overrun another (fixes item 18).
    table_top = Inches(2.6)
    _row_size = 11 if len(rows) <= 11 else 10
    # The two footnotes under the table are composed first (they grow
    # with the roster: every platform with a rate is sourced), so the
    # rows and the notes share the band between the table head and the
    # page footer. The notes keep 9pt; when the band is short the rows
    # tighten first (down to 0.21in at 10pt), then the notes tighten
    # their leading, then the longest note loses a clause.
    sources_txt, overlap = _emv_footnotes(ctx, display_roster)
    rows_top = table_top + Inches(0.38)
    rows_top_in = Emu(rows_top).inches
    row_h_in = 0.30 if len(rows) <= 11 else 0.245
    notes_gap = 0.06
    note_blocks = [
        dict(text=sources_txt, width_in=12.1, size=9, floor=NOTE_FLOOR, spacing=1.15, min_spacing=1.0),
        dict(text=overlap, width_in=12.1, size=9, floor=NOTE_FLOOR, spacing=1.15, min_spacing=1.0),
    ]

    def _notes_need(fitted):
        return sum(h + 0.02 for _sz, _sp, _t, h in fitted) + notes_gap * (len(fitted) - 1)

    fitted_notes = _fit_stack(note_blocks, 99.0, notes_gap)   # the notes as written
    while (rows_top_in + len(rows) * row_h_in + notes_gap + _notes_need(fitted_notes)
           > CONTENT_BOTTOM and row_h_in > 0.21):
        row_h_in = round(row_h_in - 0.005, 3)
    notes_room = CONTENT_BOTTOM - (rows_top_in + len(rows) * row_h_in + notes_gap)
    fitted_notes = _fit_stack(note_blocks, notes_room, notes_gap)
    row_h = Inches(row_h_in)
    C_PLAT     = (Inches(0.60), Inches(2.60))
    C_INCR     = (Inches(3.30), Inches(2.10))
    C_RATE     = (Inches(5.55), Inches(1.70))
    C_EMV      = (Inches(7.40), Inches(2.30))
    headers = [
        ("PLATFORM",        C_PLAT, "left"),
        ("INCR. CONSUMERS", C_INCR, "right"),
        ("$ / CONSUMER",    C_RATE, "right"),
        ("EMV",             C_EMV,  "right"),
    ]
    for label, (x, w), align in headers:
        _add_text(s, x, table_top, w, Inches(0.3),
                  label, size=9, bold=True, color=MUTED_DK,
                  letter_spacing=0.16, align=align)
    # Underline below header
    line = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6),
                              table_top + Inches(0.32),
                              C_EMV[0] + C_EMV[1] - Inches(0.6),
                              Emu(6000))
    line.line.fill.background()
    line.fill.solid()
    line.fill.fore_color.rgb = MUTED_DK
    # Rows
    for i, r in enumerate(rows):
        y = table_top + Inches(0.38) + row_h * i
        is_zero = r["emv_value"] <= 0
        row_color = MUTED_DK if is_zero else FG_LIGHT
        _add_text(s, C_PLAT[0], y, C_PLAT[1], row_h,
                  r["platform"], size=_row_size, bold=True, color=row_color)
        # C11: use literal 0 (not em-dash) for platforms with no
        # observed signal, matching the slide's own note that zero
        # rows indicate absence of signal.
        _add_text(s, C_INCR[0], y, C_INCR[1], row_h,
                  fmt_num(r["incremental_users_projected"]) if r["incremental_users_projected"] else "0",
                  size=_row_size, color=row_color, font=FONT_NUMERIC, align="right")
        _add_text(s, C_RATE[0], y, C_RATE[1], row_h,
                  f"${r['emv_per_user_rate']:.2f}",
                  size=_row_size, color=MUTED_DK, font=FONT_NUMERIC, align="right")
        _add_text(s, C_EMV[0], y, C_EMV[1], row_h,
                  fmt_money(r["emv_value"]) if not is_zero else "$0",
                  size=_row_size, bold=(not is_zero),
                  color=(ACCENT if not is_zero else MUTED_DK),
                  font=FONT_NUMERIC, align="right")
    table_bottom = rows_top + row_h * len(rows)
    # Right-side "WHY IT MATTERS" callout, LISA-style: the card runs
    # the height of the table (it used to be a fixed 3.5in, which on a
    # long roster crossed the notes below), and its body fits inside
    callout_x = Inches(10.0)
    callout_top = Inches(2.6)
    _callout(s, callout_x, callout_top, Inches(2.85),
             "WHY IT MATTERS",
             f"Each incremental brand consumer on a platform is priced at "
             f"a CPM-equivalent per-consumer rate calibrated against 2026 "
             f"platform benchmarks (see sourcing below). This is the "
             f"open-market dollar value of the social audience the "
             f"partnership earned for {ctx.brand}, not a per-mille "
             f"impression cost. Rows are the fixed roster used across "
             f"all decks; zeros indicate no observed signal.",
             height=table_bottom - callout_top, max_height=table_bottom - callout_top,
             line=ACCENT, line_w=1.0, label_color=ACCENT, body_color=FG_LIGHT,
             body_size=9, spacing=1.32, min_spacing=1.15, pad=0.2, body_top=0.5)
    # The sourcing line and the overlap note, stacked under the table
    # at the sizes and lengths the band allows (see the budget above).
    y = table_bottom + Inches(notes_gap)
    for sz, sp, txt, h_in in fitted_notes:
        _add_text(s, Inches(0.6), y, Inches(12.1), Inches(round(h_in + 0.02, 3)),
                  txt, size=sz, italic=True, color=MUTED_DK, spacing=sp)
        y = y + Inches(round(h_in + 0.02, 3)) + Inches(notes_gap)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _emv_footnotes(ctx: DeckCtx, display_roster: list) -> tuple:
    """The two notes under the earned-media table: the per-platform rate
    sourcing (C13: read from the payload's rate_sources so the text can
    change without a code push; every platform with a rate is sourced)
    and the overlap note."""
    _raw_srcs = ((ctx.data.get("valuation") or {})
                 .get("rate_sources", {})
                 .get("emv_per_user"))
    if isinstance(_raw_srcs, str):
        payload_srcs = {p: _raw_srcs for p in display_roster}
    elif isinstance(_raw_srcs, dict):
        payload_srcs = _raw_srcs
    else:
        payload_srcs = {}
    rates = ((ctx.data.get("valuation") or {}).get("rates", {}) or {}).get("emv_per_user") or {}
    unique_srcs, bits = set(), []
    for plat in display_roster:
        rate = _num(rates.get(plat, 0))
        if rate:
            src = payload_srcs.get(plat) or CPM_SOURCE_ATTRIBUTION.get(plat, "")
            if src:
                unique_srcs.add(src)
                bits.append(f"{plat} ${rate:.2f}: {src}")
    if len(unique_srcs) == 1:
        single = unique_srcs.pop().rstrip(". ")
        sources_txt = f"All platform rates: {single}. Editable in the live dashboard."
    else:
        sources_txt = "Rates and sources: " + "; ".join(bits)
    overlap = (
        "Note: platform-level incremental consumers can sum above "
        "the brand-level unique-consumer total because consumers can "
        "be counted on multiple platforms. EMV prices per-platform "
        "earned reach; the unique-consumer total is used for Brand "
        "Lift only."
    )
    return sources_txt, overlap


def _slide_brand_engagement_value(prs, ctx: DeckCtx, idx: int, total: int):
    """BEV calculator slide. Cream bg, LISA stat-tile rhythm.

    Calculator starts higher on the slide (y=3.0) than the earlier
    build so the total bar clears the source-line row at y=6.05 and
    the source footer at y=6.65 without collision (fixes C8)."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "Brand Engagement Value"),
             on_dark=False)
    _add_headline(s, f"{fmt_money(ctx.bev)} of observed\npost-campaign engagement.",
                  color=FG_DARK)
    # Math row - raised to y=3.0 to leave clean margin below.
    rate = float((ctx.data.get("valuation") or {})
                 .get("rates", {}).get("bev_per_user") or 0)
    _math_calculator(
        s, Inches(0.6), Inches(3.0), Inches(8.0),
        [
            ("Post-Campaign Engaged Consumers", fmt_num(ctx.post_users_proj)),
            ("× $/consumer rate",               f"$ {rate:.2f}"),
        ],
        total_label="Brand Engagement Value",
        total_value=fmt_money(ctx.bev),
        on_dark=False,
        accent=RGBColor(0x63, 0x66, 0xF1),
    )
    # Why-it-matters, matched to calculator's new top; the card grows
    # with its body down to the rate line at 6.05in
    _callout(s, Inches(9.0), Inches(3.0), Inches(3.8),
             "WHY IT MATTERS",
             "Every consumer who engaged with the brand in the post-window "
             "carries open-market value. This prices the total observed "
             f"post-campaign audience for {ctx.brand}, most of whom "
             "carried pre-existing brand interest that the campaign did "
             "not create. See Brand Lift Value for the net-new share.",
             height=Inches(2.8), max_height=Inches(2.95),
             line=FG_DARK, line_w=0.75, label_color=FG_DARK, body_color=FG_DARK,
             body_size=10, floor=BODY_FLOOR, spacing=1.35, min_spacing=1.15,
             pad=0.25, body_top=0.5)
    # Rate sourcing footnote. Reads from the payload's
    # valuation.rate_sources.bev_per_user rather than a hardcoded
    # citation, so relabeling the source (C4) doesn't require a code
    # push. It fits the band above the source footer (6.65in).
    src_line = (
        (ctx.data.get("valuation") or {})
        .get("rate_sources", {})
        .get("bev_per_user")
        or "Rate: Crosswalk internal valuation convention (2026). "
           "Editable in the live dashboard."
    )
    _add_body(s, Inches(0.6), Inches(6.05), Inches(12.1), src_line,
              max_height=Inches(0.55), size=9, floor=NOTE_FLOOR, spacing=1.25,
              min_spacing=1.05, italic=True, color=MUTED_LT)
    _add_text(s, Inches(0.6), Inches(6.65), Inches(12), Inches(0.25),
              ctx.sources_line, size=7, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _matched_group_lines(cg: dict) -> tuple:
    """The reader-facing account of the comparison group (Jenna
    2026-10-20: same size, roughly the same brand engagement before the
    partnership). Returns (one_line, side_by_side) where side_by_side
    is '' when the payload carries no match figures."""
    try:
        from migration.bpiq_matched_control import READER_NOTE as _note
    except ImportError:  # pragma: no cover - twin layout
        _note = ("Compared against a matched group of the same size with the "
                 "same brand engagement before the partnership.")
    m = (cg or {}).get("match") or {}
    if not m:
        return _note, ""
    side = (f"Exposed group {fmt_num(int(m.get('exposed_size') or 0))} people, "
            f"{float(m.get('exposed_pre_rate_pct') or 0):.2f}% engaged with the brand before; "
            f"matched group {fmt_num(int(m.get('control_size') or 0))} people, "
            f"{float(m.get('control_pre_rate_pct') or 0):.2f}% before.")
    return _note, side


def _slide_brand_lift_value(prs, ctx: DeckCtx, idx: int, total: int):
    """BLV slide - the net-new engagement attributable to the
    partnership: the exposed group's change minus the matched group's
    change over the same windows (Jenna 2026-10-20: the matched group
    carries the exposed group's size and its brand engagement before
    the partnership, so the lift is the partnership and not a baseline
    difference)."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "Brand Lift Value"), on_dark=True)
    _add_headline(s, f"{fmt_money(ctx.blv)} of net-new engagement\nabove a matched group.",
                  color=FG_LIGHT)
    rate = float((ctx.data.get("valuation") or {})
                 .get("rates", {}).get("blv_per_incr_user") or 0)
    cg = ctx.data.get("control_group") or {}
    treat_dp   = float(cg.get("treat_delta_pp")   or 0)
    control_dp = float(cg.get("control_delta_pp") or 0)
    # Honor an EXPLICIT incremental_lift_pp from the payload, including
    # an explicit 0.0 (control-brand runs suppress the adjusted lift at
    # zero by design). Only derive treat-minus-control when the field
    # is genuinely absent. The old `or` fallback treated 0.0 as falsy
    # and printed a nonzero (negative) lift on suppressed control
    # decks, contradicting both the payload and the live dashboard
    # (which renders `incremental_lift_pp || 0`).
    _raw_incr = cg.get("incremental_lift_pp")
    if _raw_incr is None:
        incr_pp = treat_dp - control_dp
    else:
        incr_pp = float(_raw_incr or 0)
    # Label: the standard case shows the subtraction. When the payload
    # suppresses the adjusted lift at zero while control drift exceeds
    # the treated delta, the subtraction label would contradict the
    # printed +0.00pp, so state the reason instead.
    if incr_pp == 0.0 and (treat_dp - control_dp) < -0.005:
        lift_label = (f"Incremental lift  "
                      f"(matched group {control_dp:+.2f}pp exceeded "
                      f"exposed {treat_dp:+.2f}pp)")
    else:
        lift_label = (f"Incremental lift  "
                      f"(exposed {treat_dp:+.2f}pp minus matched group "
                      f"{control_dp:+.2f}pp)")
    # Calculator raised to y=2.85 AND row height tightened to 0.60"
    # so the 3-row math block + total bar clears the source line
    # anchored at y=6.30 (fixes C8). row_h=0.65 uses the calculator's
    # smaller label/value fonts, which still read cleanly.
    _math_calculator(
        s, Inches(0.6), Inches(2.85), Inches(8.0),
        [
            (lift_label,
             f"{incr_pp:+.2f}pp"),
            (f"x U.S. consumer audience ({fmt_num(ctx.audience_proj)})",
             fmt_num(ctx.incr_users)),
            ("x $/incremental-consumer rate", f"$ {rate:.2f}"),
        ],
        total_label="Brand Lift Value",
        total_value=fmt_money(ctx.blv),
        on_dark=True,
        accent=RGBColor(0xF5, 0x9E, 0x0B),
        row_h=Inches(0.65),
    )
    _note_line, _side = _matched_group_lines(cg)
    detail = (
        "Brand Lift Value is the engagement the partnership added. "
        + _note_line
        + " The matched group's own movement over the same windows is "
          "taken out of the exposed group's gain, so brand interest "
          "that would have grown anyway is not counted."
        + (("\n\n" + _side) if _side else "")
    )
    # the card grows with the matched-group account down to the rate
    # line at 6.30in
    _callout(s, Inches(9.0), Inches(2.85), Inches(3.85),
             "WHY IT MATTERS", detail,
             height=Inches(3.0), max_height=Inches(3.35),
             line=ACCENT, line_w=1.0, label_color=ACCENT, body_color=FG_LIGHT,
             body_size=9, floor=NOTE_FLOOR, spacing=1.35, min_spacing=1.15,
             pad=0.2, body_top=0.5)
    # C4: read the rate source from the payload rather than hardcoding.
    # The line fits the band above the source footer (6.75in).
    src_line = (
        (ctx.data.get("valuation") or {})
        .get("rate_sources", {})
        .get("blv_per_incr_user")
        or "Rate: Crosswalk internal valuation convention (2026). "
           "Editable in the live dashboard."
    )
    _add_body(s, Inches(0.6), Inches(6.30), Inches(12.1), src_line,
              max_height=Inches(0.42), size=9, floor=NOTE_FLOOR, spacing=1.25,
              min_spacing=1.05, italic=True, color=MUTED_DK)
    # C8: source footer was missing on slide 8 (only BLV slide). Add
    # the Source: Crosswalk BehaviorGraph line explicitly.
    _add_text(s, Inches(0.6), Inches(6.75), Inches(12), Inches(0.25),
              ctx.sources_line, size=7, italic=True, color=MUTED_DK)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_conversion_value(prs, ctx: DeckCtx, idx: int, total: int):
    """CV slide - only shown when conversions are enabled and signal
    is good (skipped for automotive partnerships per .cursor/rules).

    Calculator raised to y=3.0 to match BEV slide 7 and clear the
    source line at the bottom (fixes C8)."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "Conversion Value"), on_dark=False)
    _add_headline(s, f"{fmt_money(ctx.cv)} of observed\npost-campaign conversions.",
                  color=FG_DARK)
    conv = ctx.data.get("conversions") or {}
    rate = float((ctx.data.get("valuation") or {})
                 .get("rates", {}).get("conv_value_per_user") or 0)
    _math_calculator(
        s, Inches(0.6), Inches(3.0), Inches(8.0),
        [
            ("Post-Campaign Conversions",
             fmt_num(conv.get("post_users_projected") or 0)),
            ("x Average Order Value (AOV)", f"$ {rate:.2f}"),
        ],
        total_label="Conversion Value",
        total_value=fmt_money(ctx.cv),
        on_dark=False,
        accent=RGBColor(0xEF, 0x44, 0x44),
    )
    conv_note = (str(conv.get("note") or "")).strip()
    drop_line = str(((ctx.data.get("campaign") or {}).get("drop_line")) or "").strip()
    detail = (
        "Direct purchase-funnel signal: brand.com order confirmations "
        "and geo-panel retail visits observed for brand-owned storefronts "
        "and brand-tagged SKU landing pages within the attribution "
        "window. AOV is editable in the live dashboard."
    )
    if conv_note or drop_line:
        detail = " ".join(x for x in (drop_line, conv_note) if x)
    # the card grows with the campaign's own account of the drop down
    # to the rate line at 6.05in; a long note steps down, then shortens
    _callout(s, Inches(9.0), Inches(3.0), Inches(3.8),
             "WHY IT MATTERS", detail,
             height=Inches(2.8), max_height=Inches(2.95),
             line=FG_DARK, line_w=0.75, label_color=FG_DARK, body_color=FG_DARK,
             body_size=10, floor=BODY_FLOOR, spacing=1.35, min_spacing=1.15,
             pad=0.25, body_top=0.5)
    # C4: read the AOV source from the payload so the internal-
    # convention relabel takes effect without code changes.
    # C8: vertical positioning aligned with slides 7 and 8 so the
    # three value slides read as a consistent set.
    src_line = (
        (ctx.data.get("valuation") or {})
        .get("rate_sources", {})
        .get("conv_value_per_user")
        or f"AOV: ${rate:.2f} = Crosswalk internal valuation convention "
           "(2026). Editable in the live dashboard."
    )
    _add_body(s, Inches(0.6), Inches(6.05), Inches(12.1), src_line,
              max_height=Inches(0.55), size=9, floor=NOTE_FLOOR, spacing=1.25,
              min_spacing=1.05, italic=True, color=MUTED_LT)
    _add_text(s, Inches(0.6), Inches(6.65), Inches(12), Inches(0.25),
              ctx.sources_line, size=7, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _math_calculator(slide, left, top, width, rows, *,
                     total_label: str, total_value: str,
                     on_dark: bool, accent: RGBColor,
                     row_h: Optional[Any] = None):
    """Render a calculator-style math block. Matches the dashboard's
    in-card calculator (label · value, then a single total row).

    row_h is auto-tuned to fit within a 2.6" tall envelope when there
    are more than 3 rows, so slides with a full DiD walk-through
    (Brand Lift Value) don't overflow into the footer."""
    body_color = FG_LIGHT if on_dark else FG_DARK
    muted = MUTED_DK if on_dark else MUTED_LT
    if row_h is None:
        # Auto-tune: 3 or fewer rows → 0.85"; 4-5 rows → 0.50";
        # anything above that gets tighter still.
        if len(rows) <= 3:
            row_h = Inches(0.85)
        elif len(rows) <= 5:
            row_h = Inches(0.50)
        else:
            row_h = Inches(0.40)
    label_size = 14 if row_h.inches >= 0.7 else 11
    value_size = 24 if row_h.inches >= 0.7 else 18
    for i, (label, value) in enumerate(rows):
        y = top + row_h * i
        # a long label holds two lines inside its row; a long figure
        # holds one line at the size its column allows
        lbl_size = _fit_lines(label, tuple(range(label_size, BODY_FLOOR - 1, -1)),
                              width.inches * 0.65, max_lines=2)
        _add_text(slide, left, y + Inches(0.08),
                  Inches(width.inches * 0.65), Inches(row_h.inches),
                  label, size=lbl_size, color=muted, anchor="middle")
        _add_text(slide, left + Inches(width.inches * 0.65),
                  y + Inches(0.02),
                  Inches(width.inches * 0.35), Inches(row_h.inches),
                  value, size=_fit_stat(value, width.inches * 0.35, value_size, floor=12),
                  bold=True, color=body_color,
                  font=FONT_NUMERIC, align="right", anchor="middle")
        if i < len(rows) - 1:
            sep = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, left, y + row_h - Emu(3500),
                width, Emu(3500))
            sep.line.fill.background()
            sep.fill.solid()
            sep.fill.fore_color.rgb = muted
    # Total bar
    total_y = top + row_h * len(rows) + Inches(0.15)
    _add_rect(slide, left, total_y, width, Inches(0.05),
              fill=accent, line=None)
    _add_text(slide, left, total_y + Inches(0.2),
              Inches(width.inches * 0.65), Inches(0.5),
              total_label.upper(), size=11, bold=True, color=muted,
              letter_spacing=0.16)
    _add_text(slide, left + Inches(width.inches * 0.55),
              total_y + Inches(0.1), Inches(width.inches * 0.45),
              Inches(0.9),
              total_value, size=_fit_stat(total_value, width.inches * 0.45, 36, floor=20),
              bold=True, color=accent, font=FONT_NUMERIC, align="right")


def _slide_demographics(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    # Choose an eyebrow + headline that fit the qualifier type.
    # 'tv' / 'movie' / 'sporting event' -> "Who Watched", others
    # (artist / social / campaign) -> "Who Engaged with the
    # Qualifier". The WoF-specific hardcoded copy has been generalized.
    qualifier_type = str(ctx.data.get("qualifier_type") or "").lower()
    qualifier_val = ctx.data.get("qualifier_value")
    if isinstance(qualifier_val, list):
        # Use only the first (primary) qualifier for display; the full
        # variant list is a clickstream-slug convenience for the pipeline,
        # not intended for reader-facing copy.
        _clean = [str(v).strip() for v in qualifier_val if str(v).strip()]
        qualifier_name = _clean[0] if _clean else ""
    else:
        qualifier_name = str(qualifier_val or "").strip()
    if qualifier_type in ("tv", "movie", "sporting event", "event", "show"):
        eyebrow_label = "Who Watched the Integration"
        headline = (f"{qualifier_name} viewer profile."
                    if qualifier_name else "Viewer profile.")
    else:
        eyebrow_label = f"Who Engaged with {qualifier_name}" if qualifier_name else "Who Engaged"
        headline = (f"{qualifier_name} audience profile."
                    if qualifier_name else "Audience profile.")
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, eyebrow_label),
             on_dark=False)
    head = _add_headline(s, headline, color=FG_DARK)
    head_size = head.text_frame.paragraphs[0].runs[0].font.size.pt
    head_h = _measure(headline, head_size, Emu(head.width).inches, bold=True, spacing=0.95).height_in
    # Source line: prefer the payload's own source_note (which
    # explains what cohort this profile represents). Fall back to a
    # neutral generic if none is provided. It follows the measured
    # headline (a one-line headline leaves the table more room).
    demo_src = (
        (ctx.data.get("demographics") or {}).get("source_note")
        or "Source: Crosswalk BehaviorGraph. This is the audience "
        "that engaged with the qualifier, not the brand's engager "
        "base."
    )
    note_top = Inches(round(1.0 + head_h + 0.25, 3))
    _note, note_h = _add_body(s, Inches(0.6), note_top, Inches(12), demo_src,
                              max_height=Inches(0.8), size=10, floor=NOTE_FLOOR,
                              spacing=1.35, min_spacing=1.15, italic=True, color=MUTED_LT)
    demos = (ctx.data.get("demographics") or {}).get("post") or {}
    cats = [
        ("AGE",       demos.get("age")       or []),
        ("GENDER",    demos.get("gender")    or []),
        ("ETHNICITY", demos.get("ethnicity") or []),
        ("INCOME",    demos.get("income")    or []),
    ]
    # Drop any category with no data so the slide keeps a tight grid.
    cats = [(lab, items) for lab, items in cats if items]
    n = max(1, len(cats))
    base_x = Inches(0.6)
    gap = 0.15
    avail_w = 13.333 - 2 * base_x.inches   # 12.13"
    col_w = Inches((avail_w - gap * (n - 1)) / n)
    # Every bucket is shown - Liz's "we didn't have any $150K+
    # viewers?" callout was caused by a top-5 truncation that hid the
    # two upper income buckets ($150K-$249K and $250K+). Showing every
    # bucket also closes the "not summing to 100%" observation.
    #
    # The table keeps its house position (3.05in) and row pitch
    # (0.46in) when the tallest column fits above the source footer at
    # 6.75in. When it does not (2026-10-29: an eight-bucket AGE column
    # landed on the footer), the table first rises toward the source
    # note, then the pitch tightens; a two-line bucket label needs
    # 0.40in at 12pt, so the type steps to 11pt and then 10pt as the
    # pitch closes.
    footer_top = 6.75
    max_rows = max([len(items) for _lab, items in cats] or [1])
    flow_min = note_top + note_h + Inches(0.3)
    base_y = max(Inches(3.05), flow_min)
    need = 0.42 + max_rows * 0.46 + 0.05
    if need > footer_top - Emu(base_y).inches:
        base_y = max(flow_min, Inches(round(footer_top - need, 3)))
    rows_top_in = Emu(base_y).inches + 0.42
    pitch_fit = (footer_top - 0.05 - rows_top_in) / max_rows
    # the bucket labels take the column less what the widest share
    # needs; the type size is the largest at which the tallest label
    # (two lines when the width wraps it) fits the pitch the room allows
    buckets = [str(r.get("value") or "n/a") for _lab, items in cats for r in items]
    shares = [fmt_pct(r.get("percentage") or 0) for _lab, items in cats for r in items]
    pct_w = max([_measure(sh_, 12, 2.0, bold=True).max_w_in for sh_ in shares] or [0.6]) + 0.12
    label_w = max(1.0, col_w.inches - pct_w - 0.1)
    row_size, row_lines, trimmed = NOTE_FLOOR, 1, False
    for size in (12, 11, 10, 9):
        lines = max([_measure(b, size, label_w).lines for b in buckets] or [1])
        if lines * _line_h_in(size, 1.05) + 0.03 <= pitch_fit:
            row_size, row_lines = size, lines
            break
    else:
        # even 9pt labels wrap past the pitch the room allows: every
        # label holds the one line its row has, trimmed if it must be
        trimmed = True
    row_h = max(row_lines * _line_h_in(row_size, 1.05) + 0.03, min(0.46, pitch_fit))
    for i, (label, items) in enumerate(cats):
        x = base_x + (col_w + Inches(gap)) * i
        rows_full = sorted(items, key=lambda r: float(r.get("percentage") or 0),
                           reverse=True)
        _add_text(s, x, base_y, col_w, Inches(0.3),
                  label, size=10, bold=True, color=MUTED_LT,
                  letter_spacing=0.18)
        for j, r in enumerate(rows_full):
            y = base_y + Inches(0.42 + j * row_h)
            bucket = str(r.get("value") or "n/a")
            if trimmed:
                bucket = _break_long_token(bucket, label_w, row_size, max_lines=1)
            _add_text(s, x, y, Inches(label_w), Inches(row_h - 0.02),
                      bucket,
                      size=_fit_lines(bucket, tuple(range(row_size, NOTE_FLOOR - 1, -1)),
                                      label_w, max_lines=row_lines),
                      color=FG_DARK)
            _add_text(s, x + Inches(col_w.inches - pct_w), y,
                      Inches(pct_w), Inches(row_h - 0.02),
                      fmt_pct(r.get("percentage") or 0),
                      size=row_size, bold=True, color=FG_DARK,
                      font=FONT_NUMERIC, align="right")
    _add_text(s, Inches(0.6), Inches(footer_top), Inches(12), Inches(0.25),
              ctx.sources_line, size=7, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_top_touchpoints(prs, ctx: DeckCtx, idx: int, total: int):
    """Top brand touchpoints, ranked by observed hits.

    We intentionally label the columns PRE HITS / POST HITS (not
    REACH). A hit is an observation of a brand-property visit; a
    single consumer can generate multiple hits at the same
    touchpoint. This is why the sum of touchpoint hit-gains can
    exceed the total unique incremental consumer count (Liz's item
    3): those are different populations counted at different
    granularities. The methodology footnote calls this out
    directly."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, f"Most Engaged {ctx.brand} Touchpoints"),
             on_dark=True)
    _add_headline(s, f"Where {ctx.brand} showed up.", color=FG_LIGHT)
    # Column layout - grid computed up front to guarantee no
    # overlap and consistent row heights (fixes item 18).
    post = ctx.data.get("top_brand_properties") or []
    pre  = ctx.data.get("top_brand_properties_pre") or []
    pre_map = {(p.get("common_name") or "").lower():
               int(p.get("hits_projected") or 0) for p in pre}
    rows = sorted(post, key=lambda r: int(r.get("hits_projected") or 0),
                  reverse=True)[:7]
    C_NAME     = (Inches(0.60), Inches(5.20))
    C_PRE      = (Inches(5.90), Inches(1.75))
    C_POST     = (Inches(7.75), Inches(1.75))
    C_LIFT     = (Inches(9.60), Inches(3.05))
    table_top  = Inches(2.75)
    row_h      = Inches(0.42)
    header_style = dict(size=9, bold=True, color=MUTED_DK,
                        letter_spacing=0.16)
    _add_text(s, C_NAME[0], table_top, C_NAME[1], Inches(0.3),
              "BRAND TOUCHPOINT", **header_style)
    _add_text(s, C_PRE[0], table_top, C_PRE[1], Inches(0.3),
              "PRE HITS", align="right", **header_style)
    _add_text(s, C_POST[0], table_top, C_POST[1], Inches(0.3),
              "POST HITS", align="right", **header_style)
    _add_text(s, C_LIFT[0], table_top, C_LIFT[1], Inches(0.3),
              "HITS LIFT", align="right", **header_style)
    # Underline
    line = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6),
                              table_top + Inches(0.32),
                              C_LIFT[0] + C_LIFT[1] - Inches(0.6),
                              Emu(6000))
    line.line.fill.background()
    line.fill.solid()
    line.fill.fore_color.rgb = MUTED_DK
    for i, r in enumerate(rows):
        y = table_top + Inches(0.42) + row_h * i
        raw_name = str(r.get("common_name") or "n/a")
        # C12: preserve source display casing rather than force
        # title() (which capitalizes small prepositions like "Of"
        # in "World of Coca-Cola"). Payloads now provide brand-
        # property display names in the exact case they should
        # render.
        display_name = _touchpoint_display_name(raw_name)
        post_h = int(r.get("hits_projected") or 0)
        pre_h = pre_map.get(raw_name.lower(), 0)
        # the name holds one line at the largest size its column allows;
        # a URL-style name with nothing to wrap at breaks at its own
        # separators onto two lines inside the row (2026-10-29)
        name_size = _fit_stat(display_name, C_NAME[1].inches, 13, floor=10, bold=True)
        display_name = _break_long_token(display_name, C_NAME[1].inches, name_size, bold=True,
                                         max_lines=2)
        _add_text(s, C_NAME[0], y, C_NAME[1], row_h,
                  display_name, size=name_size, bold=True, color=FG_LIGHT)
        _add_text(s, C_PRE[0], y, C_PRE[1], row_h,
                  fmt_num(pre_h) if pre_h else "0",
                  size=12, color=FG_LIGHT, font=FONT_NUMERIC,
                  align="right")
        _add_text(s, C_POST[0], y, C_POST[1], row_h,
                  fmt_num(post_h),
                  size=12, color=FG_LIGHT, font=FONT_NUMERIC,
                  align="right")
        if pre_h:
            lift = (post_h - pre_h) / pre_h * 100
            color = LIFT_UP if lift >= 0 else LIFT_DOWN
            _add_text(s, C_LIFT[0], y, C_LIFT[1], row_h,
                      fmt_signed_pct(lift),
                      size=13, bold=True, color=color, font=FONT_NUMERIC,
                      align="right")
        else:
            _add_text(s, C_LIFT[0], y, C_LIFT[1], row_h,
                      "NEW",
                      size=11, bold=True, color=ACCENT,
                      letter_spacing=0.18, align="right")
    # Methodology footnote - the critical clarification that
    # touchpoint hits and unique incremental consumers are different
    # populations at different granularities (Liz items 3, 4).
    hits_note = (
        "A HIT is one observation of a brand-property visit. One "
        "consumer can generate multiple hits at the same touchpoint, "
        "so the sum of hit gains across touchpoints can exceed the "
        "unique incremental consumer count on the Brand Lift slide. "
        "The two numbers measure different things (impressions of the "
        "brand vs. unique attributable consumers) and are not "
        "expected to reconcile."
    )
    _add_body(s, Inches(0.6), Inches(6.20), Inches(12.2), hits_note,
              max_height=Inches(0.62), size=9, floor=NOTE_FLOOR, spacing=1.3,
              min_spacing=1.1, italic=True, color=MUTED_DK)
    _add_text(s, Inches(0.6), Inches(6.85), Inches(12), Inches(0.25),
              ctx.sources_line, size=7, italic=True, color=MUTED_DK)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_year_over_year(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "Year Over Year"), on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              "How the partnership compounded.",
              size=40, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    years = ctx.data.get("yearly_breakdown") or []
    if not years:
        _add_text(s, Inches(0.6), Inches(4), Inches(11), Inches(0.5),
                  "No multi-year decomposition available.",
                  size=14, italic=True, color=MUTED_LT)
        _page_footer(s, idx, total, ctx.project, on_dark=False)
        return s
    # Bar chart approximation: stat plates per year, height-proportional
    # to EMV. We don't embed Chart.js-style charts in pptx - the visual
    # density is in the stat plates themselves.
    max_emv = max((float(y.get("earned_media_value") or 0) for y in years),
                  default=1)
    plate_count = len(years)
    base_x = Inches(0.6)
    base_y = Inches(3.1)
    avail_w = Inches(12.1)
    plate_w = Inches((avail_w.inches - 0.15 * (plate_count - 1)) / plate_count)
    chart_h = Inches(3.0)
    for i, y in enumerate(years):
        emv = float(y.get("earned_media_value") or 0)
        height_frac = emv / max_emv if max_emv else 0
        bar_h = Inches(max(0.1, chart_h.inches * height_frac))
        x = base_x + (plate_w + Inches(0.15)) * i
        # Bar
        bar_top = base_y + chart_h - bar_h
        _add_rect(s, x, bar_top, plate_w, bar_h, fill=FG_DARK, line=None)
        # EMV on bar
        _add_text(s, x, bar_top - Inches(0.05), plate_w, Inches(0.4),
                  fmt_money(emv), size=14, bold=True, color=FG_DARK,
                  font=FONT_NUMERIC, align="center")
        # Year label below
        _add_text(s, x, base_y + chart_h + Inches(0.1), plate_w, Inches(0.3),
                  str(y.get("label") or y.get("year") or ""),
                  size=11, bold=True, color=FG_DARK, align="center",
                  letter_spacing=0.14)
        # Users line
        users = int(y.get("users_projected") or 0)
        _add_text(s, x, base_y + chart_h + Inches(0.4), plate_w, Inches(0.3),
                  f"{fmt_num(users)} consumers",
                  size=9, color=MUTED_LT, align="center")
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_final_insight(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_LAV)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             _section_label(idx, "The Final Insight"), on_dark=False)
    l1, l2 = _final_insight_lines(ctx)
    # the two lines are a claim and its reading: a short reading keeps the
    # display size, a sentence-length one steps down so it fits the page
    # (a 64pt sentence ran off the slide on non-significant reads)
    size1 = _fit_lines(l1, (64, 56, 48, 44, 40, 36), 12.1, bold=True, max_lines=1)
    h1 = max(1.0, _measure(l1, size1, 12.1, bold=True, spacing=0.95).height_in + 0.15)
    size2 = 64 if len(l2.split()) <= 6 else 28
    y2 = 2.2 + h1 + 0.15
    _add_text(s, Inches(0.6), Inches(2.2), Inches(12.1), Inches(h1),
              l1, size=size1, bold=True, color=FG_DARK,
              font=FONT_DISPLAY, spacing=0.95)
    # the reading stops above the closing line at 5.7in: a long one
    # steps down from 28pt and then loses its trailing sentence
    _add_body(s, Inches(0.6), Inches(y2), Inches(12.1), l2,
              max_height=Inches(round(5.7 - 0.15 - y2, 3)),
              size=size2, floor=(40 if size2 == 64 else 20), bold=(size2 == 64),
              color=FG_DARK, font=FONT_DISPLAY,
              spacing=(0.95 if size2 == 64 else 1.15), min_spacing=(0.95 if size2 == 64 else 1.05))
    # C10: previously the strip showed both a relative-adjusted-lift
    # figure (from _final_insight_lines) and a raw-relative-lift
    # figure (delta_rel) on the same slide with no labels, so a
    # reader saw two different % figures and couldn't tell which was
    # which. Drop delta_rel and lead with the adjusted-lift number
    # since that is the one the deck argues from.
    lift_rel = (ctx.incr_lift_rel
                if ctx.incr_lift_rel is not None
                else ctx.delta_rel)
    lift_label = (
        f"+{lift_rel:.1f}% adjusted engagement lift"
        if lift_rel > 0 else "lift at or below the matched group's own movement"
    )
    closing = (
        f"{fmt_money(ctx.total_value)} in total brand value observed, "
        f"{fmt_num(ctx.post_users_proj)} U.S. consumers reached, "
        f"{lift_label}"
    )
    _add_text(s, Inches(0.6), Inches(5.7), Inches(12.1), Inches(0.5),
              closing, size=16, italic=True, color=FG_DARK)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _sig_floor_line(ctx: DeckCtx) -> str:
    """Compose the panel-size-aware detection-floor line for slide 13.

    Replaces a legacy hardcoded 'At n=10M the detection floor is roughly
    a tenth of a percentage point' string that misread whenever a BPIQ
    payload was scoped to a cohort smaller than the 10M panel construct
    (e.g. Boomer / age cuts, DMA cuts). Reads panel_size + payload's own
    detection_floor_pp so every rebuild speaks the payload's actual base.
    """
    n_panel = int(ctx.panel_size or 0)
    diag_sig = ((ctx.data.get("diagnostics") or {})
                .get("significance") or {})
    floor = diag_sig.get("detection_floor_pp")
    try:
        floor_val = float(floor) if floor is not None else None
    except (TypeError, ValueError):
        floor_val = None
    floor_txt = (f"approximately {floor_val:.2f}pp"
                 if floor_val is not None else "computed per payload")
    # Display projects up (2026-09-23): the cohort is named by its
    # projected universe; the large/small branch still keys on the
    # observed base because that is the statistical property.
    proj = int(ctx.audience_proj or 0)
    n_txt = (f"{proj:,} projected U.S. consumers" if proj
             else "this audience universe")
    if n_panel and n_panel >= 1_000_000:
        # Large-panel language: p-value collapses, drop from slide.
        return (
            f"On the cohort behind this read ({n_txt}), the detection "
            f"floor is {floor_txt}; p-values are omitted on client-facing "
            "slides because they collapse to zero for anything above the "
            "floor and add no decision-relevant information."
        )
    # Smaller-panel language: p-value stays on slide 4.
    return (
        f"On the cohort behind this read ({n_txt}), the detection "
        f"floor is {floor_txt}; p-values are reported on slide 4 alongside "
        "the point estimate and 95% CI because they remain decision "
        "relevant at this cohort size."
    )


def _slide_source(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "Source & Methodology", on_dark=True)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              "How we got here.",
              size=40, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    notes = [
        ("DATA SOURCE",
         "Crosswalk BehaviorGraph: a 30M+ opted-in U.S. consumer panel. "
         "Every observation in this deck reflects real behavioral signal "
         "(visits, engagements, brand touchpoints); no surveys, no "
         "self-report. The read covers the partnership's audience cohort "
         "across the pre, event, and post windows."),
        ("INCIDENCE RATES",
         "All pre and post penetration percentages are incidence rates: "
         "unique brand engagers in the window divided by the audience "
         "base, with both sides stated as projected U.S. consumers on "
         "slide 4 so each percentage can be audited directly."),
        ("PROJECTION",
         f"Every count in this deck is stated at the partnership's "
         f"{fmt_num(ctx.audience_proj)}-consumer U.S. audience universe, "
         "scaled with a calibrated cohort weight. Numbers labeled "
         "'projected' or 'U.S. consumers' are that universe."),
        ("ATTRIBUTION",
         f"Brand touchpoints are counted within {ctx.attribution_d} days "
         "of campaign end: long enough to capture delayed brand "
         "behavior, tight enough to keep the causal read clean."),
        ("BRAND LIFT",
         _matched_group_lines(ctx.data.get("control_group") or {})[0]
         + " The lift is the exposed group's change in brand engagement "
           "minus the matched group's change over the same windows. A "
           "difference that was already there before the partnership "
           "never reads as lift."
         + ((" " + _matched_group_lines(ctx.data.get("control_group") or {})[1])
            if _matched_group_lines(ctx.data.get("control_group") or {})[1] else "")),
        ("SIGNIFICANCE",
         "Same-panel pre/post design. Primary test: pooled two-sample "
         "z on the paired marginals (conservative for a within-panel "
         "read). We report the point estimate, the z-score, and a 95% "
         "confidence interval derived from the same variance. "
         + _sig_floor_line(ctx)),
    ]
    # The rows flow from under the headline to the logo at 6.6in, each
    # as tall as its paragraph (2026-10-29: on a fixed 0.75in pitch a
    # four-line BRAND LIFT account landed on SIGNIFICANCE, which in turn
    # reached the brand line). When the six do not fit at 10pt and 1.3
    # leading, the stack tightens its leading, then the tallest
    # paragraphs lose a trailing sentence; the labels never move apart
    # from their paragraphs.
    base_y = 2.05
    logo_top = 6.6
    gap = 0.22
    fitted = _fit_stack(
        [dict(text=body, width_in=9.0, size=10, floor=BODY_FLOOR, spacing=1.3, min_spacing=1.1)
         for _label, body in notes],
        logo_top - 0.08 - base_y, gap)
    y = base_y
    for (label, _body), (sz, sp, txt, h_in) in zip(notes, fitted):
        _add_text(s, Inches(0.6), Inches(round(y, 3)), Inches(3.0), Inches(0.3),
                  label, size=10, bold=True, color=ACCENT,
                  letter_spacing=0.18)
        _add_text(s, Inches(3.8), Inches(round(y, 3)), Inches(9.0), Inches(round(h_in + 0.02, 3)),
                  txt, size=sz, color=FG_LIGHT, spacing=sp)
        y += h_in + 0.02 + gap
    # Final logo + URL line
    try:
        s.shapes.add_picture(ctx.logo, Inches(0.6), Inches(6.6),
                             height=Inches(0.4))
    except Exception:
        _add_text(s, Inches(0.6), Inches(6.75), Inches(5), Inches(0.3),
                  "CROSSWALK · BehaviorGraph",
                  size=11, bold=True, color=FG_LIGHT, letter_spacing=0.18)
    _add_text(s, Inches(8), Inches(6.75), Inches(4.8), Inches(0.3),
              "behaviorgraph.com · 2026",
              size=9, italic=True, color=MUTED_DK, align="right",
              letter_spacing=0.14)
    return s


# ─────────────────────────────────────────────────────────────────────
#  Public entry point
# ─────────────────────────────────────────────────────────────────────
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
                 (used on the cover slide). Optional - falls back to a
                 typographic plate if missing or unfetchable. A
                 `/api/profile-image-file/<key>` path is read from the
                 private bucket (2026-10-29), an http(s) URL is fetched.
    category   : admin-managed category (case-insensitive). Used to
                 suppress conversion slides for 'automotive' runs.
    s3_client  : optional boto3-style client for the bucket read (the
                 app passes its own; tests pass a fake). None builds one.
    """
    ctx = _derive_context(data, image_url, category, s3_client=s3_client)

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    # Decide which slides participate in this deck.
    builders = [
        _slide_cover,
        _slide_methodology,
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

    total = len(builders)
    for i, builder in enumerate(builders, start=1):
        try:
            builder(prs, ctx, i, total)
        except Exception as e:
            # Never let a single slide failure kill the whole deck -
            # render an apology slide and keep going so the user still
            # gets a deck rather than a 500.
            s = _blank(prs)
            _add_bg(s, BG_DARK)
            _add_text(s, Inches(0.6), Inches(3), Inches(12), Inches(1),
                      f"Slide build failed ({builder.__name__}): {e}",
                      size=14, italic=True, color=MUTED_DK)

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
    # 2026-10-21: the delivered deck carries Crosswalk Inter embedded and
    # passes the pack's checks (fixed in place; never held).
    _ds = _design()
    if _ds is not None:
        try:
            out = io.BytesIO(_ds.finish_bytes(out.getvalue(), ".pptx"))
        except Exception:
            pass

    # Best-effort cleanup of any tmp files we materialized. Only
    # delete paths that live under the system temp dir - the shipped
    # brand logo lives inside the repo's static/ dir and must never
    # be unlinked.
    tmp_root = tempfile.gettempdir()
    for p in (ctx.cover_image, ctx.logo):
        try:
            if p and os.path.exists(p) and os.path.abspath(p).startswith(tmp_root):
                os.unlink(p)
        except Exception:
            pass

    return out.getvalue()


def suggested_filename(data: dict, ext: str = "pptx") -> str:
    data = _dict(data)
    name = safe_filename(str(data.get("project_name") or "BPIQ_Deck"))
    stamp = str(data.get("created_at") or "")[:10] or "deck"
    return f"{name}_Analysis_{stamp}.{ext}"

