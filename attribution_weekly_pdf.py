"""One-page Attribution IQ weekly summary PDF.

Client (David / Sony Pictures Animation) asked for a concise,
calibrated one-page report he could forward. This is that report.

The PDF is a SNAPSHOT of what the reader is looking at on-screen.
The frontend collects the current Weekly Summary card, Asset-Ranked
Table (top 5), and Audience Response Table (top 5) and POSTs the
structured payload to ``/api/intent/<slug>/weekly-pdf``; this
module renders it. Zero risk of the PDF drifting from the on-screen
read because there is no separate server-side synthesis.

Theme dispatch
--------------
The PDF adapts to whatever theme the dashboard is in when the user
downloads it, so it reads as a continuation of the screen they were
just staring at:

  * ``theme = 'dark'`` (default; matches the dashboard's default
    Graphite Teal ground). Renders as a full-dark page with Slate
    Teal cards, Signal Green accents, Off-White text. Dashboard
    mirror.
  * ``theme = 'light'`` (user is in the dashboard's light mode).
    Renders on Off-White ground with a single Graphite header band,
    Signal Olive accent, tinted Fit chips. Portrait-doc standard,
    per ``documents.md`` in the Crosswalk brand skill.

Hero image
----------
Each variant carries a small campaign hero tile in the top-right of
the header. The image is resolved and fetched server-side by
``campaign_hero_image.resolve_and_fetch`` (currently the first
YouTube trailer's ``hqdefault`` thumbnail, with a manual
``title.hero_image_url`` override) and passed to this module inside
the payload under the key ``hero_image_bytes``. When the resolver
comes up empty, the tile silently collapses and the header text
takes back the space.

Type / palette / copy standards all come from
``.cursor/skills/crosswalk-brand-standards/``. Public surface:
``build_weekly_pdf(payload) -> bytes``. Any Python error raises;
the Flask endpoint traps and returns JSON.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


# ---------------------------------------------------------------------------
# Shared palette (from `.cursor/skills/crosswalk-brand-standards/`)
# ---------------------------------------------------------------------------
GRAPHITE_TEAL   = HexColor("#0C1618")
SLATE_TEAL      = HexColor("#15252A")
SLATE_BORDER    = HexColor("#27393D")   # dashboard's exact card border
OFF_WHITE       = HexColor("#E9E8E1")
SIGNAL_GREEN    = HexColor("#C7F23E")   # accent on dark only
SIGNAL_OLIVE    = HexColor("#5E7E12")   # Signal Green twin, safe on light
SIGNAL_OLIVE_TINT = HexColor("#E1E7CE")
ORCHID          = HexColor("#E682FF")   # second voice on dark
AMETHYST        = HexColor("#8E3FA8")   # Orchid twin, safe on light
DUSK            = HexColor("#B7B3D8")   # recessive series
PAVEMENT        = HexColor("#3B3D38")

# Text roles on Graphite (dashboard convention)
DARK_TEXT_PRIMARY = HexColor("#E9E8E1")
DARK_TEXT_BODY    = HexColor("#9AA09B")
DARK_TEXT_MUTED   = HexColor("#7C878A")
DARK_TEXT_FOOTER  = HexColor("#7C878A")

# Text roles on Off-White (documents.md)
LIGHT_TEXT_PRIMARY = HexColor("#0C1618")
LIGHT_TEXT_BODY    = HexColor("#5C6560")
LIGHT_TEXT_MUTED   = HexColor("#888C89")
LIGHT_TEXT_FOOTER  = HexColor("#5C6466")

# Card / row hairline colors
LIGHT_CARD_FILL   = HexColor("#E1E0D7")
LIGHT_CARD_STROKE = HexColor("#C9C6BA")
DARK_ROW_DIVIDER  = HexColor("#182528")
LIGHT_ROW_DIVIDER = HexColor("#D5D3C7")

# Fit chip palette on LIGHT surfaces (rounded pills)
FIT_TINTS_LIGHT = {
    "sweet":       (SIGNAL_OLIVE_TINT,          SIGNAL_OLIVE),
    "underserved": (HexColor("#F4E1F9"),        AMETHYST),
    "broad":       (HexColor("#E8E7F1"),        HexColor("#6C6A80")),
    "offtarget":   (HexColor("#E5E4DE"),        HexColor("#797F81")),
}
# Fit dot color on DARK surfaces (dot + label, like the dashboard tag)
FIT_DOTS_DARK = {
    "sweet":       SIGNAL_GREEN,
    "underserved": ORCHID,
    "broad":       DUSK,
    "offtarget":   HexColor("#5C6466"),
}

# Delta chip colors (WoW up / down)
DELTA_UP_DARK    = SIGNAL_GREEN
DELTA_DOWN_DARK  = HexColor("#f87171")
DELTA_UP_LIGHT   = SIGNAL_OLIVE
DELTA_DOWN_LIGHT = AMETHYST


# ---------------------------------------------------------------------------
# Type: Inter 18pt with Helvetica fallback
# ---------------------------------------------------------------------------
_INTER_REGISTERED = False
_INTER_FAMILY = "Inter18pt"
_HELV_FAMILY  = "Helvetica"


def _find_font_dir() -> Path | None:
    """Locate the Inter 18pt TTF bundle. Order:

    1. ``ATTRIBUTION_PDF_FONT_DIR`` env override (Render / prod).
    2. Skill checkout: ``.cursor/skills/crosswalk-brand-standards/assets/fonts``.
    3. ``bg-webapp/static/fonts`` (production copy shipped with the webapp).
    """
    candidates: list[Path] = []
    env = os.environ.get("ATTRIBUTION_PDF_FONT_DIR")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    candidates.append(
        repo_root / ".cursor" / "skills" / "crosswalk-brand-standards" / "assets" / "fonts"
    )
    candidates.append(here / "static" / "fonts")
    for c in candidates:
        if c.is_dir() and (c / "Inter_18pt-Regular.ttf").is_file():
            return c
    return None


def _register_inter() -> str:
    """Register Inter 18pt weights, idempotent. Returns family or Helvetica."""
    global _INTER_REGISTERED
    if _INTER_REGISTERED:
        return _INTER_FAMILY
    font_dir = _find_font_dir()
    if font_dir is None:
        print("[attribution_weekly_pdf] Inter 18pt fonts not found; "
              "falling back to Helvetica.")
        return _HELV_FAMILY
    weights = {
        "":           "Inter_18pt-Regular.ttf",
        "-Bold":      "Inter_18pt-Bold.ttf",
        "-Light":     "Inter_18pt-Light.ttf",
        "-Medium":    "Inter_18pt-Medium.ttf",
        "-Black":     "Inter_18pt-Black.ttf",
        "-ExtraBold": "Inter_18pt-ExtraBold.ttf",
    }
    try:
        for suffix, fname in weights.items():
            fp = font_dir / fname
            if fp.is_file():
                pdfmetrics.registerFont(TTFont(_INTER_FAMILY + suffix, str(fp)))
    except Exception as e:
        print(f"[attribution_weekly_pdf] Inter registration failed ({e}); "
              "falling back to Helvetica.")
        return _HELV_FAMILY
    _INTER_REGISTERED = True
    return _INTER_FAMILY


def _font(family: str, weight: str = "") -> str:
    """Weight-aware helper. Helvetica maps '-Bold' / '-ExtraBold' /
    '-Black' -> Helvetica-Bold, everything else -> Helvetica."""
    if family == _INTER_FAMILY:
        return family + weight
    if weight in ("-Bold", "-ExtraBold", "-Black"):
        return family + "-Bold"
    return family


# ---------------------------------------------------------------------------
# Copy sanitizers
# ---------------------------------------------------------------------------
_DASH_CHARS = "\u2014\u2013\u2015\u2012"  # em, en, horizontal, figure
_SMART_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u02bc": "'", "\u00ab": '"', "\u00bb": '"',
}


def _sanitize(s: str) -> str:
    """Strip em/en dashes (per `no-em-dashes.mdc`) and smart quotes."""
    if not s:
        return ""
    out = str(s)

    def _dash_repl(m: re.Match) -> str:
        i = m.start()
        pre  = out[max(0, i - 1): i]
        post = out[i + 1: i + 2]
        if pre == " " and post == " ":
            return "to"
        if pre == " " or post == " ":
            return " - "
        return "-"

    out = re.sub(f"[{_DASH_CHARS}]", _dash_repl, out)
    for k, v in _SMART_QUOTES.items():
        out = out.replace(k, v)
    out = out.replace("\u2026", "...")
    return out.strip()


def _fmt_int(n: Any) -> str:
    if n is None:
        return "-"
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_pct(n: Any, digits: int = 1) -> str:
    if n is None:
        return "-"
    if isinstance(n, str) and n.endswith("%"):
        return n
    try:
        return f"{float(n):.{digits}f}%"
    except (TypeError, ValueError):
        return str(n)


def _fmt_delta_pct(n: Any, theme: str = "dark") -> tuple[str, HexColor]:
    """Signed pct-point delta, e.g., `+0.5pp`. Returns (text, color)
    keyed to the theme."""
    up_col   = DELTA_UP_DARK   if theme == "dark" else DELTA_UP_LIGHT
    down_col = DELTA_DOWN_DARK if theme == "dark" else DELTA_DOWN_LIGHT
    muted    = DARK_TEXT_MUTED if theme == "dark" else LIGHT_TEXT_MUTED
    if n is None:
        return ("-", muted)
    try:
        v = float(n)
    except (TypeError, ValueError):
        return (str(n), muted)
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    color = up_col if v > 0 else (down_col if v < 0 else muted)
    return (f"{sign}{abs(v):.1f}pp", color)


def _fit_key(label: str) -> str:
    if not label:
        return "offtarget"
    k = re.sub(r"[^a-z]", "", label.lower())
    if k in ("underserved", "underserve"):
        return "underserved"
    if k in ("sweetspot", "sweet"):
        return "sweet"
    if k == "broad":
        return "broad"
    return "offtarget"


def _fmt_iso_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        return iso
    return dt.strftime("%b ") + f"{dt.day}, {dt.year}"


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float,
                  max_width: float, font: str, size: float,
                  color: HexColor, leading: float | None = None) -> float:
    """Wrap ``text`` to ``max_width``, drawing top-down from y. Returns
    the y coord of the LAST baseline drawn (so the caller can subtract
    line-height to place the next block)."""
    if leading is None:
        leading = size * 1.35
    c.setFont(font, size)
    c.setFillColor(color)
    words = _sanitize(text).split()
    line = ""
    cy = y
    for w in words:
        cand = (line + " " + w).strip()
        if pdfmetrics.stringWidth(cand, font, size) <= max_width:
            line = cand
        else:
            if line:
                c.drawString(x, cy, line)
                cy -= leading
            line = w
    if line:
        c.drawString(x, cy, line)
    return cy


def _split_label_body(text: str) -> tuple[str, str]:
    """Split "Label: body copy" on the first colon. Returns ``(label,
    body)`` with the trailing colon stripped from the label and the
    leading space stripped from the body. If there's no colon (or the
    colon is deep inside the body), the whole string comes back as
    ``("", text)`` so the caller can render it as a plain paragraph.

    The dashboard only ever uses label-prefixed bullets so the colon
    is guaranteed to be shallow. We cap the label at 60 chars to avoid
    accidentally treating a body-colon (rare, e.g. "at 12:45 PM") as
    a label."""
    if not text:
        return ("", "")
    idx = text.find(":")
    if idx < 0 or idx > 60:
        return ("", text)
    label = text[:idx].strip()
    body  = text[idx + 1:].lstrip()
    if not label:
        return ("", text)
    return (label, body)


def _draw_label_body_wrapped(c: canvas.Canvas, label: str, body: str,
                             x: float, y: float, max_width: float,
                             font_family: str, size: float,
                             label_color: HexColor, body_color: HexColor,
                             leading: float | None = None) -> float:
    """Render "<bold-label> <body-copy>" as one wrapped paragraph. The
    label sits inline in ``label_color`` at bold weight, the body in
    ``body_color`` at regular weight. Overflow wraps to the next line
    starting at ``x`` with the full ``max_width``, exactly like the
    dashboard's ``.iiq-hero-bullets li`` renders. Returns the y coord
    of the last baseline drawn."""
    if leading is None:
        leading = size * 1.45  # tracks the dashboard's line-height: 1.55

    lbl_font  = _font(font_family, "-Bold")
    body_font = _font(font_family)

    # Draw label first, tracked by pen_x. If the label alone exceeds
    # max_width (shouldn't ever happen for our 4 dashboard labels but
    # be defensive), it wraps like the body.
    pen_x = x
    cy    = y
    if label:
        label_text = _sanitize(label) + ":"
        c.setFillColor(label_color)
        c.setFont(lbl_font, size)
        # A dashboard label is short (~24-45 chars) so treat it as one
        # atom. If it wouldn't fit, drop to a new line first.
        lbl_w = pdfmetrics.stringWidth(label_text, lbl_font, size)
        if lbl_w > max_width:
            # Extreme fallback: wrap the label like normal body copy.
            cy = _draw_wrapped(c, label_text, x, cy, max_width,
                               lbl_font, size, label_color, leading)
            pen_x = x
            cy -= leading
        else:
            c.drawString(pen_x, cy, label_text)
            pen_x += lbl_w + pdfmetrics.stringWidth(" ", lbl_font, size)

    # Now the body, continuing from pen_x on the same line.
    if not body:
        return cy
    body = _sanitize(body)
    c.setFillColor(body_color)
    c.setFont(body_font, size)
    words = body.split()
    if not words:
        return cy
    space_w = pdfmetrics.stringWidth(" ", body_font, size)
    line_words: list[str] = []
    line_start_x = pen_x
    line_width_left = max_width - (pen_x - x)

    def _flush(cy_local: float) -> float:
        if not line_words:
            return cy_local
        text = " ".join(line_words)
        c.drawString(line_start_x, cy_local, text)
        return cy_local

    for w in words:
        w_width = pdfmetrics.stringWidth(w, body_font, size)
        extra = (space_w if line_words else 0) + w_width
        if extra <= line_width_left:
            line_words.append(w)
            line_width_left -= extra
        else:
            _flush(cy)
            cy -= leading
            line_words = [w]
            line_start_x = x
            line_width_left = max_width - w_width
    if line_words:
        _flush(cy)
    return cy


def _wow_chip_style(tone: str, theme: str) -> tuple[HexColor, HexColor, HexColor]:
    """Return ``(text_color, fill_color, border_color)`` for a WoW
    exposure chip, matching the on-screen chip's dark palette exactly
    for the dark PDF variant, and a Signal-Olive / Amethyst / Dusk
    twin palette for the light variant (per the twin rule in
    ``crosswalk-brand-standards``: Signal Green -> Signal Olive on
    Off-White, Orchid -> Amethyst)."""
    tone = (tone or "").lower()
    if theme == "light":
        if tone == "up":
            text, fill, border = SIGNAL_OLIVE, HexColor("#EEF3D8"), HexColor("#BDC98C")
        elif tone == "down":
            text, fill, border = AMETHYST, HexColor("#F4E1F9"), HexColor("#D5B5E0")
        elif tone == "launch":
            text, fill, border = HexColor("#6C6A80"), HexColor("#E8E7F1"), HexColor("#C0BED0")
        else:  # flat / unknown
            text, fill, border = LIGHT_TEXT_BODY, HexColor("#EDECE4"), LIGHT_CARD_STROKE
    else:  # dark (dashboard-exact)
        if tone == "up":
            text, fill, border = SIGNAL_GREEN, HexColor("#1F2E10"), HexColor("#4E6D1E")
        elif tone == "down":
            text, fill, border = HexColor("#f87171"), HexColor("#2A1414"), HexColor("#6E2626")
        elif tone == "launch":
            text, fill, border = DUSK, HexColor("#1A1826"), HexColor("#3D3757")
        else:  # flat / unknown
            text, fill, border = DARK_TEXT_BODY, HexColor("#1A2426"), SLATE_BORDER
    return text, fill, border


def _draw_wow_chip(c: canvas.Canvas, chip: dict | None, right_x: float,
                   top_y: float, font_family: str, theme: str,
                   size: float = 8.5) -> tuple[float, float]:
    """Right-anchor a rounded-pill WoW chip and return its ``(width,
    height)`` so callers can stack elements below it. A missing / empty
    chip silently returns ``(0, 0)`` so the header collapses cleanly."""
    if not chip or not chip.get("text"):
        return (0.0, 0.0)
    text = _sanitize(chip.get("text") or "")
    if not text:
        return (0.0, 0.0)
    tone = str(chip.get("tone") or "").lower()
    txt_col, fill_col, border_col = _wow_chip_style(tone, theme)
    fnt = _font(font_family, "-Bold")
    text_w = pdfmetrics.stringWidth(text, fnt, size)
    pad_x  = 0.11 * inch
    pad_y  = 0.055 * inch
    chip_w = text_w + 2 * pad_x
    chip_h = size * 1.55 / 72.0 * inch + 2 * pad_y - 0.02 * inch
    # Position: top-right anchored. Baseline offsets are tuned so the
    # cap-line sits vertically centered in the pill.
    x = right_x - chip_w
    y = top_y - chip_h
    c.setFillColor(fill_col)
    c.setStrokeColor(border_col)
    c.setLineWidth(0.5)
    c.roundRect(x, y, chip_w, chip_h, chip_h / 2.0, stroke=1, fill=1)
    c.setFillColor(txt_col)
    c.setFont(fnt, size)
    baseline_y = y + (chip_h - size * 0.72) / 2.0
    c.drawString(x + pad_x, baseline_y, text)
    return (chip_w, chip_h)


# ---------------------------------------------------------------------------
# Layout constants (US Letter portrait, per documents.md)
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = LETTER              # 8.5 x 11 in
MARGIN         = 0.65 * inch
CONTENT_W      = PAGE_W - 2 * MARGIN


# ---------------------------------------------------------------------------
# Hero image tile: shared helper for both themes
# ---------------------------------------------------------------------------
def _draw_hero_tile(c: canvas.Canvas, image_bytes: bytes | None,
                    right_x: float, top_y: float,
                    max_w: float, max_h: float,
                    border_color: HexColor) -> tuple[float, float]:
    """Draw the campaign hero image aspect-preserved inside a max-w x max-h
    box, top-right anchored at (right_x, top_y). Returns the actual drawn
    (width, height) so the caller can right-align header text to
    ``right_x - actual_w - gap``. Missing / broken bytes silently return
    (0, 0) so the header collapses back cleanly."""
    if not image_bytes:
        return (0.0, 0.0)
    try:
        reader = ImageReader(io.BytesIO(image_bytes))
        iw, ih = reader.getSize()
        if iw <= 0 or ih <= 0:
            return (0.0, 0.0)
        # Fit inside the max box, preserving aspect. Portrait fits by
        # height, landscape / square fits by width.
        aspect_h_over_w = ih / iw
        # Try width-fit first
        draw_w = max_w
        draw_h = draw_w * aspect_h_over_w
        if draw_h > max_h:
            draw_h = max_h
            draw_w = draw_h / aspect_h_over_w
        x = right_x - draw_w
        y = top_y - draw_h
        # Draw image
        c.drawImage(reader, x, y, draw_w, draw_h,
                    mask="auto", preserveAspectRatio=True)
        # Subtle 0.5pt rounded stroke gives every campaign a consistent
        # tile silhouette regardless of image aspect / background color.
        c.setStrokeColor(border_color)
        c.setLineWidth(0.5)
        c.roundRect(x, y, draw_w, draw_h, 0.05 * inch, stroke=1, fill=0)
        return (draw_w, draw_h)
    except Exception as e:
        print(f"[attribution_weekly_pdf] hero image failed: {e}")
        return (0.0, 0.0)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------
def build_weekly_pdf(payload: dict) -> bytes:
    """Render a one-page Attribution IQ weekly summary PDF.

    Payload shape (all optional; a missing block collapses)::

        {
          "theme": "dark" | "light",                    # default 'dark'
          "hero_image_bytes": b"...",                   # server-injected
          "title": {"display_name": "Goat",
                    "distributor":  "Sony Pictures Animation",
                    "opening_date": "2026-02-13"},
          "as_of":       "2026-01-30",
          "week_start":  "2026-01-24",
          "week_end":    "2026-01-30",
          "days_to_open": 14,
          "phase_label": "Bridge Campaign (T-14)",
          "subtitle":    "Sep 22, 2026 \u00b7 T+221 days",   # NEW: dashboard-exact
          "wow_chip":    {"text": "-0.6% WoW exposure",      # NEW: dashboard-exact
                          "tone": "down"},                   #   up | down | flat | launch
          "snapshot": {"exposed_viewers": 1234567,
                       "exposed_delta_pct": 0.51,
                       "response_rate_pct": 4.32,
                       "response_delta_pct": 0.14,
                       "response_metric_label": "Info-seek rate",
                       "sample_size": 47},
          "bullets":       [str, ...],   # each is "Label: body copy"
          "top_assets":    [{...}, ...],
          "top_audiences": [{...}, ...],
        }

    Returns raw PDF bytes.
    """
    theme = (payload.get("theme") or "dark").lower()
    if theme not in ("dark", "light"):
        theme = "dark"
    if theme == "light":
        return _build_light_pdf(payload)
    return _build_dark_pdf(payload)


# ---------------------------------------------------------------------------
# Shared payload extraction
# ---------------------------------------------------------------------------
def _extract(payload: dict) -> dict:
    title = payload.get("title") or {}
    snapshot = payload.get("snapshot") or {}
    # ``subtitle`` (single-line "Sep 22, 2026 · T+221 days") and
    # ``wow_chip`` are new payload keys sent by the frontend so the
    # PDF hero mirrors the on-screen Weekly Summary card exactly. Both
    # are optional: a missing subtitle falls back to a computed week
    # range + phase line, and a missing wow_chip collapses the pill.
    return {
        "display_name":   _sanitize(title.get("display_name") or "Untitled"),
        "distributor":    _sanitize(title.get("distributor") or ""),
        "as_of":          payload.get("as_of") or "",
        "week_start":     payload.get("week_start") or "",
        "week_end":       payload.get("week_end") or payload.get("as_of") or "",
        "days_to_open":   payload.get("days_to_open"),
        "phase_label":    _sanitize(payload.get("phase_label") or ""),
        "subtitle":       _sanitize(payload.get("subtitle") or ""),
        "wow_chip":       payload.get("wow_chip") or {},
        "snapshot":       snapshot,
        "bullets":        [_sanitize(b) for b in (payload.get("bullets") or []) if b],
        "top_assets":     payload.get("top_assets") or [],
        "top_audiences":  payload.get("top_audiences") or [],
        "hero_bytes":     payload.get("hero_image_bytes"),
    }


def _phase_sub_line(phase_label: str, days_to_open: Any) -> str:
    bits: list[str] = []
    if phase_label:
        bits.append(phase_label)
    if isinstance(days_to_open, (int, float)):
        n = int(days_to_open)
        if n > 0:
            bits.append(f"{n} days to opening")
        elif n == 0:
            bits.append("Opening today")
        else:
            bits.append(f"{abs(n)} days post-opening")
    return "  \u00b7  ".join(bits)


def _week_line(week_start: str, week_end: str) -> str:
    if week_start:
        return _fmt_iso_date(week_start) + " to " + _fmt_iso_date(week_end)
    return "Week ending " + _fmt_iso_date(week_end)


# ===========================================================================
# DARK VARIANT (dashboard mirror)
# ===========================================================================
def _build_dark_pdf(payload: dict) -> bytes:
    ff = _register_inter()
    d  = _extract(payload)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER, pageCompression=1)
    c.setTitle(f"Attribution IQ Weekly - {d['display_name']} - {d['week_end']}")
    c.setAuthor("Crosswalk")
    c.setSubject("Attribution IQ Weekly Summary")
    c.setCreator("Crosswalk Attribution IQ")

    # Full Graphite Teal ground
    c.setFillColor(GRAPHITE_TEAL)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # -- Header --------------------------------------------------------
    # Fixed 0.95" header zone (top margin -> HEADER_BOTTOM). Whatever
    # the hero aspect, the tile fits inside 0.85" tall x 1.15" wide so
    # a portrait movie poster and a landscape YouTube thumbnail both
    # sit inside the same visual band. This keeps the rest of the
    # page laid out identically regardless of hero shape (a 2:3
    # poster would otherwise push the audience table past the
    # footer).
    # Header zone mirrors the dashboard's ``.iiq-hero-card`` header:
    # small Signal Green eyebrow, big title, one Dusk sub-meta line
    # ("Sep 22, 2026 · T+221 days"). No distributor line, no separate
    # phase line, no week-range block. The right column carries the
    # hero image tile plus the WoW chip stacked underneath it, exactly
    # like the on-screen chip position.
    top = PAGE_H - MARGIN
    right_x = PAGE_W - MARGIN
    HEADER_ZONE_H = 0.92 * inch

    hero_w, hero_h = _draw_hero_tile(
        c, d["hero_bytes"],
        right_x=right_x, top_y=top - 0.02 * inch,
        max_w=1.05 * inch, max_h=0.68 * inch,
        border_color=SLATE_BORDER,
    )
    text_right_x = (right_x - hero_w - 0.20 * inch) if hero_w > 0 else right_x

    # Eyebrow: dot + "WEEKLY SUMMARY" (dashboard-exact, no product prefix)
    c.setFillColor(SIGNAL_GREEN)
    c.circle(MARGIN + 0.06 * inch, top - 0.05 * inch, 0.055 * inch,
             stroke=0, fill=1)
    c.setFillColor(SIGNAL_GREEN)
    c.setFont(_font(ff, "-Bold"), 8.5)
    c.drawString(MARGIN + 0.20 * inch, top - 0.09 * inch,
                 "WEEKLY SUMMARY")

    # Title
    c.setFillColor(DARK_TEXT_PRIMARY)
    c.setFont(_font(ff, "-Bold"), 26)
    c.drawString(MARGIN, top - 0.50 * inch, d["display_name"])

    # Single-line sub-meta under the title. Prefer the frontend's
    # pre-formatted ``subtitle`` ("Sep 22, 2026 · T+221 days") because
    # it uses the same _iiqFmtAsOfDate / _iiqTMinusLabel helpers as
    # the on-screen ``.iiq-hero-meta`` string, so the two are
    # byte-identical. Fall back to computed pieces if the frontend
    # is on an older payload version.
    sub_line = d["subtitle"]
    if not sub_line:
        parts: list[str] = []
        pretty = _fmt_iso_date(d["as_of"]) if d["as_of"] else ""
        if pretty:
            parts.append(pretty)
        if isinstance(d["days_to_open"], (int, float)):
            n = int(d["days_to_open"])
            if n == 0:
                parts.append("opening day")
            elif n > 0:
                parts.append(f"T-{n} days")
            else:
                parts.append(f"T+{abs(n)} days")
        sub_line = "  \u00b7  ".join(parts)
    if sub_line:
        c.setFillColor(DUSK)
        c.setFont(_font(ff), 9.5)
        c.drawString(MARGIN, top - 0.72 * inch, sub_line)

    # WoW chip: dashboard-exact rounded pill under the hero image,
    # right-aligned to the page. If the hero is missing, the chip
    # sits at the same top-right anchor and the header text takes
    # back the width naturally.
    chip_y_top = top - hero_h - 0.10 * inch if hero_h > 0 else top - 0.06 * inch
    _draw_wow_chip(c, d["wow_chip"], right_x, chip_y_top, ff, "dark")

    cursor = top - HEADER_ZONE_H - 0.15 * inch

    # -- Stat row card -------------------------------------------------
    snap = d["snapshot"]
    stat_h = 0.90 * inch
    _dashboard_card(c, MARGIN, cursor - stat_h, CONTENT_W, stat_h)
    col_w = CONTENT_W / 3.0

    def _stat_dark(idx: int, label: str, value: str,
                   dtxt: str | None, dcol: HexColor | None) -> None:
        x = MARGIN + idx * col_w + 0.20 * inch
        c.setFillColor(DARK_TEXT_BODY)
        c.setFont(_font(ff, "-Bold"), 8)
        c.drawString(x, cursor - 0.20 * inch, label.upper())
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Bold"), 26)
        c.drawString(x, cursor - 0.58 * inch, value)
        if dtxt:
            c.setFillColor(dcol or DARK_TEXT_MUTED)
            c.setFont(_font(ff, "-Medium"), 9)
            c.drawString(x, cursor - 0.78 * inch, "WoW " + dtxt)

    ed_t, ed_c = _fmt_delta_pct(snap.get("exposed_delta_pct"), "dark")
    rd_t, rd_c = _fmt_delta_pct(snap.get("response_delta_pct"), "dark")
    _stat_dark(0, "Exposed viewers",
               _fmt_int(snap.get("exposed_viewers")),
               ed_t if snap.get("exposed_delta_pct") is not None else None, ed_c)
    _stat_dark(1, _sanitize(snap.get("response_metric_label") or "Response rate"),
               _fmt_pct(snap.get("response_rate_pct")),
               rd_t if snap.get("response_delta_pct") is not None else None, rd_c)
    _stat_dark(2, "Sample this week",
               _fmt_int(snap.get("sample_size")), None, None)

    cursor -= stat_h + 0.20 * inch

    # -- Bullets card (dashboard's ".iiq-hero-card") -------------------
    # This card gets the 3px Signal Green left-stripe treatment because
    # it's the direct PDF analog of the on-screen hero card that holds
    # the eyebrow + title + WoW chip + "What changed this week" + the
    # four bullets. Each bullet mirrors the on-screen shape: small
    # Signal Olive dot + bold Off-White label + Body-grey body copy,
    # with the "Where signals are soft" bullet's label sitting in Dusk
    # so a soft finding never masquerades as an accent moment.
    if d["bullets"]:
        rows = d["bullets"][:4]
        b_title_h = 0.30 * inch
        b_row_h   = 0.46 * inch  # room for a two-line wrap on the body
        b_card_h  = b_title_h + b_row_h * len(rows) + 0.10 * inch
        card_x = MARGIN
        card_y = cursor - b_card_h
        _dashboard_card(c, card_x, card_y, CONTENT_W, b_card_h)
        # Signal Green left stripe (dashboard-exact 3pt, rounded ends
        # to match the card's 0.14in corner radius).
        c.setFillColor(SIGNAL_GREEN)
        stripe_w = 3.0 / 72.0 * inch  # 3pt
        stripe_r = 0.05 * inch
        c.roundRect(card_x, card_y, stripe_w, b_card_h, stripe_r,
                    stroke=0, fill=1)
        # Intro: "WHAT CHANGED THIS WEEK." tracked caps, muted body
        # color (matches the on-screen .iiq-hero-intro tone; the label
        # was previously "THIS WEEK." which was too terse to signal
        # what the reader is about to see.)
        c.setFillColor(DARK_TEXT_BODY)
        c.setFont(_font(ff, "-Bold"), 8.5)
        c.drawString(MARGIN + 0.22 * inch, cursor - 0.20 * inch,
                     "WHAT CHANGED THIS WEEK.")
        y = cursor - 0.44 * inch
        for b in rows:
            label, body = _split_label_body(b)
            is_soft = label.lower().startswith("where signals are soft")
            # Olive dot before each bullet, per dashboard convention
            # (Signal Olive is the safe stand-in for Signal Green on
            # anything smaller than ~4pt on a dark ground).
            c.setFillColor(SIGNAL_OLIVE if not is_soft else DUSK)
            c.circle(MARGIN + 0.28 * inch, y + 0.04 * inch, 0.040 * inch,
                     stroke=0, fill=1)
            lbl_color = DUSK if is_soft else DARK_TEXT_PRIMARY
            end_y = _draw_label_body_wrapped(
                c, label, body,
                x=MARGIN + 0.44 * inch, y=y,
                max_width=CONTENT_W - 0.60 * inch,
                font_family=ff, size=9.5,
                label_color=lbl_color, body_color=DARK_TEXT_BODY,
                leading=12.5,
            )
            y = end_y - 0.15 * inch
        cursor -= b_card_h + 0.18 * inch

    # -- Asset table ---------------------------------------------------
    if d["top_assets"]:
        rows_a = min(len(d["top_assets"]), 5)
        row_h  = 0.28 * inch
        head_h = 0.30 * inch
        title_h = 0.30 * inch
        a_card_h = title_h + head_h + row_h * rows_a + 0.10 * inch
        _dashboard_card(c, MARGIN, cursor - a_card_h, CONTENT_W, a_card_h)
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Bold"), 12)
        c.drawString(MARGIN + 0.22 * inch, cursor - 0.20 * inch,
                     "Strongest asset signals.")
        cursor -= title_h
        _asset_table_dark(c, ff, cursor, d["top_assets"][:rows_a],
                          row_h, head_h)
        cursor -= head_h + row_h * rows_a + 0.22 * inch

    # -- Audience table ------------------------------------------------
    if d["top_audiences"]:
        rows_u = min(len(d["top_audiences"]), 5)
        row_h  = 0.28 * inch
        head_h = 0.30 * inch
        title_h = 0.30 * inch
        u_card_h = title_h + head_h + row_h * rows_u + 0.10 * inch
        _dashboard_card(c, MARGIN, cursor - u_card_h, CONTENT_W, u_card_h)
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Bold"), 12)
        c.drawString(MARGIN + 0.22 * inch, cursor - 0.20 * inch,
                     "Audiences responding, and audiences responding but under-served.")
        cursor -= title_h
        _aud_table_dark(c, ff, cursor, d["top_audiences"][:rows_u],
                        row_h, head_h)
        cursor -= head_h + row_h * rows_u + 0.22 * inch

    # -- Footer --------------------------------------------------------
    _footer_dark(c, ff, d["week_end"])

    c.showPage()
    c.save()
    return buf.getvalue()


def _dashboard_card(c: canvas.Canvas, x: float, y: float,
                    w: float, h: float) -> None:
    """Slate Teal card, dashboard-exact border color."""
    c.setFillColor(SLATE_TEAL)
    c.setStrokeColor(SLATE_BORDER)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 0.14 * inch, stroke=1, fill=1)


def _asset_table_dark(c, ff, cursor, rows, row_h, head_h):
    col_asset_w = 2.50 * inch
    col_ch_w    = 1.60 * inch
    col_exp_w   = 1.15 * inch
    col_resp_w  = 0.85 * inch
    col_lift_w  = 0.50 * inch
    col_asset_x = MARGIN + 0.22 * inch
    col_ch_x    = col_asset_x + col_asset_w
    col_exp_x   = col_ch_x + col_ch_w
    col_resp_x  = col_exp_x + col_exp_w
    col_lift_x  = col_resp_x + col_resp_w

    c.setFillColor(DARK_TEXT_MUTED)
    c.setFont(_font(ff, "-Bold"), 7.5)
    head_y = cursor - 0.20 * inch
    c.drawString(col_asset_x, head_y, "ASSET")
    c.drawString(col_ch_x, head_y, "CHANNEL  \u00b7  PHASE")
    c.drawRightString(col_exp_x + col_exp_w - 0.12 * inch, head_y, "EXPOSURE")
    c.drawRightString(col_resp_x + col_resp_w - 0.12 * inch, head_y, "RESPONSE")
    c.drawRightString(col_lift_x + col_lift_w - 0.05 * inch, head_y, "LIFT")

    c.setStrokeColor(SLATE_BORDER)
    c.setLineWidth(0.4)
    c.line(MARGIN + 0.22 * inch, cursor - head_h + 0.08 * inch,
           MARGIN + CONTENT_W - 0.22 * inch, cursor - head_h + 0.08 * inch)

    ry = cursor - head_h
    for i, a in enumerate(rows):
        rm = ry - row_h / 2 + 0.03 * inch
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 10)
        lbl = _sanitize(a.get("asset") or a.get("action_label") or "-")
        if len(lbl) > 40:
            lbl = lbl[:39].rstrip() + "..."
        c.drawString(col_asset_x, rm, lbl)
        c.setFillColor(DARK_TEXT_BODY)
        c.setFont(_font(ff), 9)
        ch = _sanitize(a.get("channel") or "-")
        ph = _sanitize(a.get("phase") or "")
        chp = ch + ("  \u00b7  " + ph if ph else "")
        if len(chp) > 32:
            chp = chp[:31] + "..."
        c.drawString(col_ch_x, rm, chp)
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawRightString(col_exp_x + col_exp_w - 0.12 * inch, rm,
                          _fmt_int(a.get("exposure") or a.get("views_total")
                                   or a.get("ext_view_count")))
        c.drawRightString(col_resp_x + col_resp_w - 0.12 * inch, rm,
                          _fmt_pct(a.get("response_pct")))
        lift = a.get("lift_x")
        if lift is not None:
            try:
                lv = float(lift)
                lift_str = f"{lv:.1f}x"
                if lv >= 1.3:   c.setFillColor(SIGNAL_GREEN)
                elif lv < 0.9:  c.setFillColor(DUSK)
                else:           c.setFillColor(DARK_TEXT_PRIMARY)
            except (TypeError, ValueError):
                lift_str = "-"; c.setFillColor(DARK_TEXT_MUTED)
        else:
            lift_str = "-"; c.setFillColor(DARK_TEXT_MUTED)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawRightString(col_lift_x + col_lift_w - 0.05 * inch, rm, lift_str)

        ry -= row_h
        if i < len(rows) - 1:
            c.setStrokeColor(DARK_ROW_DIVIDER)
            c.setLineWidth(0.4)
            c.line(MARGIN + 0.22 * inch, ry,
                   MARGIN + CONTENT_W - 0.22 * inch, ry)


def _aud_table_dark(c, ff, cursor, rows, row_h, head_h):
    col_aud_w  = 2.55 * inch
    col_over_w = 1.00 * inch
    col_resp_w = 1.00 * inch
    col_idx_w  = 0.75 * inch
    col_aud_x  = MARGIN + 0.22 * inch
    col_over_x = col_aud_x + col_aud_w
    col_resp_x = col_over_x + col_over_w
    col_idx_x  = col_resp_x + col_resp_w
    col_fit_x  = col_idx_x + col_idx_w

    c.setFillColor(DARK_TEXT_MUTED)
    c.setFont(_font(ff, "-Bold"), 7.5)
    head_y = cursor - 0.20 * inch
    c.drawString(col_aud_x, head_y, "AUDIENCE")
    c.drawRightString(col_over_x + col_over_w - 0.10 * inch, head_y, "OVERLAP")
    c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, head_y, "RESPONSE")
    c.drawRightString(col_idx_x + col_idx_w - 0.10 * inch, head_y, "VS GENPOP")
    c.drawString(col_fit_x, head_y, "FIT")

    c.setStrokeColor(SLATE_BORDER)
    c.setLineWidth(0.4)
    c.line(MARGIN + 0.22 * inch, cursor - head_h + 0.08 * inch,
           MARGIN + CONTENT_W - 0.22 * inch, cursor - head_h + 0.08 * inch)

    ry = cursor - head_h
    for i, a in enumerate(rows):
        rm = ry - row_h / 2 + 0.03 * inch
        name = _sanitize(a.get("audience") or a.get("display") or "-")
        if len(name) > 36:
            name = name[:35] + "..."
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawString(col_aud_x, rm, name)
        c.drawRightString(col_over_x + col_over_w - 0.10 * inch, rm,
                          _fmt_pct(a.get("overlap_pct"), 1))
        c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, rm,
                          _fmt_pct(a.get("response_pct"), 1))
        idx_v = a.get("vs_gen_pop_x")
        if idx_v is not None:
            try:
                ivf = float(idx_v); idx_s = f"{ivf:.1f}x"
                if ivf >= 1.3:  c.setFillColor(SIGNAL_GREEN)
                elif ivf < 0.7: c.setFillColor(DARK_TEXT_MUTED)
                else:           c.setFillColor(DARK_TEXT_PRIMARY)
            except (TypeError, ValueError):
                idx_s = "-"; c.setFillColor(DARK_TEXT_MUTED)
        else:
            idx_s = "-"; c.setFillColor(DARK_TEXT_MUTED)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawRightString(col_idx_x + col_idx_w - 0.10 * inch, rm, idx_s)

        fit_raw = _sanitize(a.get("fit") or "")
        fk = _fit_key(fit_raw)
        c.setFillColor(FIT_DOTS_DARK[fk])
        c.circle(col_fit_x + 0.06 * inch, rm + 0.03 * inch,
                 0.045 * inch, stroke=0, fill=1)
        c.setFillColor(DARK_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 9)
        c.drawString(col_fit_x + 0.18 * inch, rm,
                     fit_raw or "Off-target")

        ry -= row_h
        if i < len(rows) - 1:
            c.setStrokeColor(DARK_ROW_DIVIDER)
            c.setLineWidth(0.4)
            c.line(MARGIN + 0.22 * inch, ry,
                   MARGIN + CONTENT_W - 0.22 * inch, ry)


def _footer_dark(c, ff, week_end: str) -> None:
    y = 0.65 * inch
    c.setStrokeColor(SLATE_BORDER)
    c.setLineWidth(0.4)
    c.line(MARGIN, y + 0.32 * inch, MARGIN + CONTENT_W, y + 0.32 * inch)
    c.setFillColor(DARK_TEXT_FOOTER)
    _draw_wrapped(
        c,
        "Directional read from Crosswalk's opted-in behavioral panel, "
        "week ending " + _fmt_iso_date(week_end) + ". Under-served flags "
        "cohorts responding above index with low reach; sweet spot flags "
        "high reach and high affinity. Precise budget reallocations and "
        "causal attribution require a higher validation standard.",
        x=MARGIN, y=y + 0.20 * inch, max_width=CONTENT_W,
        font=_font(ff), size=8, color=DARK_TEXT_FOOTER, leading=10,
    )
    c.setFillColor(DARK_TEXT_FOOTER)
    c.setFont(_font(ff, "-Bold"), 7.5)
    c.drawString(MARGIN, 0.30 * inch,
                 "CROSSWALK  \u00b7  BEHAVIORAL INTELLIGENCE")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.drawRightString(MARGIN + CONTENT_W, 0.30 * inch,
                      f"GENERATED {stamp}".upper())


# ===========================================================================
# LIGHT VARIANT (Off-White portrait document)
# ===========================================================================
def _build_light_pdf(payload: dict) -> bytes:
    ff = _register_inter()
    d  = _extract(payload)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER, pageCompression=1)
    c.setTitle(f"Attribution IQ Weekly - {d['display_name']} - {d['week_end']}")
    c.setAuthor("Crosswalk")
    c.setSubject("Attribution IQ Weekly Summary")
    c.setCreator("Crosswalk Attribution IQ")

    # Off-White page ground
    c.setFillColor(OFF_WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # -- Graphite header band (with hero over it) ----------------------
    HEADER_H = 1.20 * inch
    c.setFillColor(GRAPHITE_TEAL)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, stroke=0, fill=1)

    top     = PAGE_H - 0.32 * inch
    right_x = PAGE_W - MARGIN

    # Hero tile top-right, fits inside the header band (max 0.90" tall
    # to leave room for the WoW chip below it). Movie posters (portrait)
    # end up ~0.60" wide; YT thumbs (16:9) fill.
    hero_w, hero_h = _draw_hero_tile(
        c, d["hero_bytes"],
        right_x=right_x, top_y=top - 0.02 * inch,
        max_w=1.20 * inch, max_h=0.72 * inch,
        border_color=SLATE_BORDER,
    )
    text_right_x = (right_x - hero_w - 0.20 * inch) if hero_w > 0 else right_x

    # Eyebrow on the band (Signal Green on Graphite, allowed on dark).
    # Dashboard label is "WEEKLY SUMMARY" (no product prefix) so we
    # match exactly.
    c.setFillColor(SIGNAL_GREEN)
    c.circle(MARGIN + 0.06 * inch, top - 0.05 * inch,
             0.055 * inch, stroke=0, fill=1)
    c.setFillColor(SIGNAL_GREEN)
    c.setFont(_font(ff, "-Bold"), 8.5)
    c.drawString(MARGIN + 0.20 * inch, top - 0.09 * inch,
                 "WEEKLY SUMMARY")

    # Title on the band
    c.setFillColor(OFF_WHITE)
    c.setFont(_font(ff, "-Bold"), 22)
    c.drawString(MARGIN, top - 0.42 * inch, d["display_name"])

    # Single-line sub-meta ("Sep 22, 2026 · T+221 days") under the
    # title. Same dashboard-mirror rule as the dark variant.
    sub_line = d["subtitle"]
    if not sub_line:
        parts: list[str] = []
        pretty = _fmt_iso_date(d["as_of"]) if d["as_of"] else ""
        if pretty:
            parts.append(pretty)
        if isinstance(d["days_to_open"], (int, float)):
            n = int(d["days_to_open"])
            if n == 0:
                parts.append("opening day")
            elif n > 0:
                parts.append(f"T-{n} days")
            else:
                parts.append(f"T+{abs(n)} days")
        sub_line = "  \u00b7  ".join(parts)
    if sub_line:
        c.setFillColor(DUSK)
        c.setFont(_font(ff), 9.5)
        c.drawString(MARGIN, top - 0.62 * inch, sub_line)

    # WoW chip: rounded pill anchored under the hero image,
    # right-aligned. Sits inside the Graphite header band so we use
    # the "dark" tone palette here (light-page portrait document,
    # but the header band is dark ground per documents.md).
    chip_y_top = top - hero_h - 0.10 * inch if hero_h > 0 else top - 0.06 * inch
    _draw_wow_chip(c, d["wow_chip"], right_x, chip_y_top, ff, "dark")

    cursor = PAGE_H - HEADER_H - 0.35 * inch

    # -- Stat row (three figures with a hairline underneath) -----------
    snap = d["snapshot"]
    stat_h  = 0.86 * inch
    stat_top    = cursor
    stat_bottom = stat_top - stat_h
    col_w = CONTENT_W / 3.0
    c.setStrokeColor(LIGHT_CARD_STROKE)
    c.setLineWidth(0.5)
    c.line(MARGIN, stat_bottom, MARGIN + CONTENT_W, stat_bottom)

    def _stat_light(idx: int, label: str, value: str,
                    dtxt: str | None, dcol: HexColor | None) -> None:
        x = MARGIN + idx * col_w
        c.setFillColor(LIGHT_TEXT_BODY)
        c.setFont(_font(ff, "-Bold"), 8.5)
        c.drawString(x, stat_top - 0.20 * inch, label.upper())
        c.setFillColor(LIGHT_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Bold"), 26)
        c.drawString(x, stat_top - 0.60 * inch, value)
        if dtxt:
            c.setFillColor(dcol or LIGHT_TEXT_MUTED)
            c.setFont(_font(ff, "-Medium"), 9)
            c.drawString(x, stat_top - 0.78 * inch, "WoW " + dtxt)

    ed_t, ed_c = _fmt_delta_pct(snap.get("exposed_delta_pct"), "light")
    rd_t, rd_c = _fmt_delta_pct(snap.get("response_delta_pct"), "light")
    _stat_light(0, "Exposed viewers",
                _fmt_int(snap.get("exposed_viewers")),
                ed_t if snap.get("exposed_delta_pct") is not None else None, ed_c)
    _stat_light(1, _sanitize(snap.get("response_metric_label") or "Response rate"),
                _fmt_pct(snap.get("response_rate_pct")),
                rd_t if snap.get("response_delta_pct") is not None else None, rd_c)
    _stat_light(2, "Sample this week",
                _fmt_int(snap.get("sample_size")), None, None)

    cursor = stat_bottom - 0.28 * inch

    # -- Bullets -------------------------------------------------------
    # Signal Olive intro + olive dot per bullet, mirroring the
    # dashboard's light-mode ".iiq-hero-bullets" scoped colors
    # (Signal Green -> Signal Olive twin per the brand twin rule).
    # Each bullet is a "Label: body" pair with the label rendered
    # bold Signal Olive and the body in LIGHT_TEXT_BODY. The soft-
    # signal bullet uses Amethyst for the label (Orchid twin).
    if d["bullets"]:
        c.setFillColor(SIGNAL_OLIVE)
        c.setFont(_font(ff, "-Bold"), 8.5)
        c.drawString(MARGIN, cursor, "WHAT CHANGED THIS WEEK.")
        cursor -= 0.16 * inch
        for b in d["bullets"][:5]:
            dy = cursor
            label, body = _split_label_body(b)
            is_soft = label.lower().startswith("where signals are soft")
            dot_color = AMETHYST if is_soft else SIGNAL_OLIVE
            lbl_color = AMETHYST if is_soft else SIGNAL_OLIVE
            c.setFillColor(dot_color)
            c.circle(MARGIN + 0.05 * inch, dy + 0.04 * inch,
                     0.040 * inch, stroke=0, fill=1)
            end_y = _draw_label_body_wrapped(
                c, label, body,
                x=MARGIN + 0.20 * inch, y=dy,
                max_width=CONTENT_W - 0.20 * inch,
                font_family=ff, size=10.5,
                label_color=lbl_color, body_color=LIGHT_TEXT_BODY,
                leading=14.0,
            )
            cursor = end_y - 0.16 * inch
        cursor -= 0.10 * inch

    # -- Asset card ----------------------------------------------------
    if d["top_assets"]:
        rows_a = min(len(d["top_assets"]), 5)
        row_h  = 0.30 * inch
        head_h = 0.30 * inch
        title_h = 0.32 * inch
        a_card_h = title_h + head_h + row_h * rows_a + 0.12 * inch
        _light_card(c, MARGIN, cursor - a_card_h, CONTENT_W, a_card_h)
        c.setFillColor(LIGHT_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Bold"), 12)
        c.drawString(MARGIN + 0.20 * inch, cursor - 0.24 * inch,
                     "Strongest asset signals.")
        cursor -= title_h
        _asset_table_light(c, ff, cursor, d["top_assets"][:rows_a],
                           row_h, head_h)
        cursor -= head_h + row_h * rows_a + 0.24 * inch

    # -- Audience card -------------------------------------------------
    if d["top_audiences"]:
        rows_u = min(len(d["top_audiences"]), 5)
        row_h  = 0.30 * inch
        head_h = 0.30 * inch
        title_h = 0.32 * inch
        u_card_h = title_h + head_h + row_h * rows_u + 0.12 * inch
        _light_card(c, MARGIN, cursor - u_card_h, CONTENT_W, u_card_h)
        c.setFillColor(LIGHT_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Bold"), 12)
        c.drawString(MARGIN + 0.20 * inch, cursor - 0.24 * inch,
                     "Audiences responding, and audiences responding but under-served.")
        cursor -= title_h
        _aud_table_light(c, ff, cursor, d["top_audiences"][:rows_u],
                         row_h, head_h)
        cursor -= head_h + row_h * rows_u + 0.22 * inch

    _footer_light(c, ff, d["week_end"])
    c.showPage()
    c.save()
    return buf.getvalue()


def _light_card(c, x, y, w, h):
    c.setFillColor(LIGHT_CARD_FILL)
    c.setStrokeColor(LIGHT_CARD_STROKE)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 0.14 * inch, stroke=1, fill=1)


def _asset_table_light(c, ff, cursor, rows, row_h, head_h):
    col_asset_w = 2.55 * inch
    col_ch_w    = 1.50 * inch
    col_exp_w   = 1.10 * inch
    col_resp_w  = 0.85 * inch
    col_lift_w  = 0.50 * inch
    col_asset_x = MARGIN + 0.20 * inch
    col_ch_x    = col_asset_x + col_asset_w
    col_exp_x   = col_ch_x + col_ch_w
    col_resp_x  = col_exp_x + col_exp_w
    col_lift_x  = col_resp_x + col_resp_w

    c.setFillColor(LIGHT_TEXT_MUTED)
    c.setFont(_font(ff, "-Bold"), 7.5)
    head_y = cursor - 0.18 * inch
    c.drawString(col_asset_x, head_y, "ASSET")
    c.drawString(col_ch_x, head_y, "CHANNEL  \u00b7  PHASE")
    c.drawRightString(col_exp_x + col_exp_w - 0.10 * inch, head_y, "EXPOSURE")
    c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, head_y, "RESPONSE")
    c.drawRightString(col_lift_x + col_lift_w - 0.05 * inch, head_y, "LIFT")

    c.setStrokeColor(LIGHT_CARD_STROKE)
    c.setLineWidth(0.4)
    c.line(MARGIN + 0.20 * inch, cursor - head_h + 0.05 * inch,
           MARGIN + CONTENT_W - 0.20 * inch, cursor - head_h + 0.05 * inch)
    ry = cursor - head_h

    for i, a in enumerate(rows):
        rm = ry - row_h / 2 + 0.02 * inch
        c.setFillColor(LIGHT_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 10)
        lbl = _sanitize(a.get("asset") or a.get("action_label") or "-")
        if len(lbl) > 42:
            lbl = lbl[:41].rstrip() + "..."
        c.drawString(col_asset_x, rm, lbl)
        c.setFillColor(LIGHT_TEXT_BODY)
        c.setFont(_font(ff), 9)
        ch = _sanitize(a.get("channel") or "-")
        ph = _sanitize(a.get("phase") or "")
        chp = ch + ("  \u00b7  " + ph if ph else "")
        if len(chp) > 30:
            chp = chp[:29].rstrip() + "..."
        c.drawString(col_ch_x, rm, chp)
        c.setFillColor(LIGHT_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawRightString(col_exp_x + col_exp_w - 0.10 * inch, rm,
                          _fmt_int(a.get("exposure") or a.get("views_total")
                                   or a.get("ext_view_count")))
        c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, rm,
                          _fmt_pct(a.get("response_pct")))
        lift = a.get("lift_x")
        if lift is not None:
            try:
                lv = float(lift); lift_str = f"{lv:.1f}x"
                if lv >= 1.3:  c.setFillColor(SIGNAL_OLIVE)
                else:          c.setFillColor(LIGHT_TEXT_BODY)
            except (TypeError, ValueError):
                lift_str = "-"; c.setFillColor(LIGHT_TEXT_MUTED)
        else:
            lift_str = "-"; c.setFillColor(LIGHT_TEXT_MUTED)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawRightString(col_lift_x + col_lift_w - 0.05 * inch, rm, lift_str)

        ry -= row_h
        if i < len(rows) - 1:
            c.setStrokeColor(LIGHT_ROW_DIVIDER)
            c.setLineWidth(0.3)
            c.line(MARGIN + 0.20 * inch, ry,
                   MARGIN + CONTENT_W - 0.20 * inch, ry)


def _aud_table_light(c, ff, cursor, rows, row_h, head_h):
    col_aud_w  = 2.55 * inch
    col_over_w = 1.05 * inch
    col_resp_w = 1.05 * inch
    col_idx_w  = 0.75 * inch
    col_aud_x  = MARGIN + 0.20 * inch
    col_over_x = col_aud_x + col_aud_w
    col_resp_x = col_over_x + col_over_w
    col_idx_x  = col_resp_x + col_resp_w
    col_fit_x  = col_idx_x + col_idx_w

    c.setFillColor(LIGHT_TEXT_MUTED)
    c.setFont(_font(ff, "-Bold"), 7.5)
    head_y = cursor - 0.18 * inch
    c.drawString(col_aud_x, head_y, "AUDIENCE")
    c.drawRightString(col_over_x + col_over_w - 0.10 * inch, head_y, "OVERLAP")
    c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, head_y, "RESPONSE")
    c.drawRightString(col_idx_x + col_idx_w - 0.10 * inch, head_y, "VS GENPOP")
    c.drawString(col_fit_x, head_y, "FIT")

    c.setStrokeColor(LIGHT_CARD_STROKE)
    c.setLineWidth(0.4)
    c.line(MARGIN + 0.20 * inch, cursor - head_h + 0.05 * inch,
           MARGIN + CONTENT_W - 0.20 * inch, cursor - head_h + 0.05 * inch)
    ry = cursor - head_h

    for i, a in enumerate(rows):
        rm = ry - row_h / 2 + 0.02 * inch
        name = _sanitize(a.get("audience") or a.get("display") or "-")
        if len(name) > 40:
            name = name[:39].rstrip() + "..."
        c.setFillColor(LIGHT_TEXT_PRIMARY)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawString(col_aud_x, rm, name)
        c.drawRightString(col_over_x + col_over_w - 0.10 * inch, rm,
                          _fmt_pct(a.get("overlap_pct"), 1))
        c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, rm,
                          _fmt_pct(a.get("response_pct"), 1))
        idx_v = a.get("vs_gen_pop_x")
        if idx_v is not None:
            try:
                ivf = float(idx_v); idx_s = f"{ivf:.1f}x"
                if ivf >= 1.3:   c.setFillColor(SIGNAL_OLIVE)
                elif ivf < 0.7:  c.setFillColor(LIGHT_TEXT_MUTED)
                else:            c.setFillColor(LIGHT_TEXT_BODY)
            except (TypeError, ValueError):
                idx_s = "-"; c.setFillColor(LIGHT_TEXT_MUTED)
        else:
            idx_s = "-"; c.setFillColor(LIGHT_TEXT_MUTED)
        c.setFont(_font(ff, "-Medium"), 10)
        c.drawRightString(col_idx_x + col_idx_w - 0.10 * inch, rm, idx_s)

        fit_raw = _sanitize(a.get("fit") or "")
        fk = _fit_key(fit_raw)
        fill_c, text_c = FIT_TINTS_LIGHT[fk]
        chip_w = 1.05 * inch
        chip_h = 0.20 * inch
        chip_x = col_fit_x
        chip_y = rm - 0.05 * inch
        c.setFillColor(fill_c); c.setStrokeColor(fill_c)
        c.roundRect(chip_x, chip_y, chip_w, chip_h,
                    chip_h / 2, stroke=0, fill=1)
        c.setFillColor(text_c)
        c.setFont(_font(ff, "-Bold"), 8)
        c.drawCentredString(chip_x + chip_w / 2, chip_y + 0.05 * inch,
                            (fit_raw or "Off-target").upper())

        ry -= row_h
        if i < len(rows) - 1:
            c.setStrokeColor(LIGHT_ROW_DIVIDER)
            c.setLineWidth(0.3)
            c.line(MARGIN + 0.20 * inch, ry,
                   MARGIN + CONTENT_W - 0.20 * inch, ry)


def _footer_light(c, ff, week_end: str) -> None:
    y = 0.55 * inch
    c.setStrokeColor(LIGHT_CARD_STROKE)
    c.setLineWidth(0.4)
    c.line(MARGIN, y + 0.35 * inch, MARGIN + CONTENT_W, y + 0.35 * inch)
    c.setFillColor(LIGHT_TEXT_FOOTER)
    _draw_wrapped(
        c,
        "Directional read from Crosswalk's opted-in behavioral panel, "
        "week ending " + _fmt_iso_date(week_end) + ". Under-served flags "
        "cohorts responding above index with low reach; sweet spot flags "
        "high reach and high affinity. Precise budget reallocations and "
        "causal attribution require a higher validation standard.",
        x=MARGIN, y=y + 0.20 * inch, max_width=CONTENT_W,
        font=_font(ff), size=8, color=LIGHT_TEXT_FOOTER, leading=10,
    )
    c.setFillColor(LIGHT_TEXT_FOOTER)
    c.setFont(_font(ff, "-Bold"), 7.5)
    c.drawString(MARGIN, 0.30 * inch,
                 "CROSSWALK  \u00b7  BEHAVIORAL INTELLIGENCE")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.drawRightString(MARGIN + CONTENT_W, 0.30 * inch,
                      f"GENERATED {stamp}".upper())
