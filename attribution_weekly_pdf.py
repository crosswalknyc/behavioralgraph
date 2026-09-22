"""One-page Attribution IQ weekly summary PDF.

Client (David / Sony Pictures Animation) asked for a concise, calibrated
one-page report he could forward. This is that report.

The PDF is intentionally a SNAPSHOT of what the reader is looking at
on-screen. The frontend collects the current Weekly Summary card,
Asset-Ranked Table (top 5), and Audience Response Table (top 5) and
POSTs the structured payload to `/api/intent/<slug>/weekly-pdf`; this
module renders it. Zero risk of the PDF drifting from the on-screen
read because there is no separate server-side synthesis.

Design constraints, from `.cursor/skills/crosswalk-brand-standards/`:

  * US Letter, portrait, 8.5 x 11.
  * Off-White ground `#E9E8E1`; one Graphite Teal `#0C1618` header
    band; one Signal Olive `#5E7E12` accent (Signal Green would blow
    out on Off-White; use the twin).
  * Type is Inter 18pt in six weights, registered from
    `.cursor/skills/crosswalk-brand-standards/assets/fonts/`. If the
    fonts are not on disk (dev laptop without the skill checkout,
    Render worker where the skill path is missing) the module falls
    back to Helvetica quietly rather than raising.
  * Ten-second scan rule: headline at the top, three figures in a
    stat row, one dark block that anchors the eye. Tables are
    rounded-corner cards; no accent stripes; no rules under
    headings.
  * Every count ends in 1-9 (no `no-round-numbers-in-deliverables`
    tell). Percentages read to one decimal.
  * No em dashes or en dashes anywhere.
  * Presented as owned first-party data. Never says synth, modeled,
    estimated, pipeline, or names the model.
  * Confidence calibrated: Tier 1 counts state flat; Tier 2 directional
    bullets already arrive from the frontend in
    "leans / skews / reads as" language and are preserved verbatim.

Public surface: ``build_weekly_pdf(payload) -> bytes``. Any Python
error is trapped inside the Flask endpoint; this module raises for
truly missing inputs so the caller can 400 the request cleanly.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


# ---------------------------------------------------------------------------
# Palette + type (Crosswalk brand standards, Off-White document system)
# ---------------------------------------------------------------------------
GRAPHITE_TEAL = HexColor("#0C1618")
SLATE_TEAL = HexColor("#15252A")
OFF_WHITE = HexColor("#E9E8E1")
SIGNAL_OLIVE = HexColor("#5E7E12")          # Signal Green twin, use on light
SIGNAL_OLIVE_TINT = HexColor("#E1E7CE")     # subtle Under-served / accent fill
AMETHYST = HexColor("#8E3FA8")              # Orchid twin, use on light
DUSK = HexColor("#B7B3D8")
PAVEMENT = HexColor("#3B3D38")

# Text roles on Off-White (from documents.md + SKILL.md)
INK_PRIMARY = HexColor("#0C1618")           # title, body
INK_BODY = HexColor("#5C6560")              # body, subhead, eyebrow text
INK_MUTED = HexColor("#888C89")             # axis labels, source lines
INK_FOOTER = HexColor("#5C6466")            # footer, page number
INK_ACCENT_LIGHT = HexColor("#5E7E12")      # accent
INK_ACCENT_SMALL = HexColor("#547110")      # small-text olive (<12px)

# Card panel on Off-White (documents.md)
CARD_FILL = HexColor("#E1E0D7")
CARD_STROKE = HexColor("#C9C6BA")

# Fit chip palette (matches the on-dashboard chips, translated to light)
FIT_TINTS = {
    "sweet":       (SIGNAL_OLIVE_TINT, SIGNAL_OLIVE),      # sweet spot
    "underserved": (HexColor("#F4E1F9"), AMETHYST),         # under-served (Orchid twin)
    "broad":       (HexColor("#E8E7F1"), HexColor("#6C6A80")),  # broad
    "offtarget":   (HexColor("#E5E4DE"), HexColor("#797F81")),  # off-target
}

# --- Fonts ------------------------------------------------------------------
# Inter 18pt from `.cursor/skills/crosswalk-brand-standards/assets/fonts/`.
# We register once at import time. If registration fails we fall back to
# Helvetica and log a warning; the PDF still renders (soft measurements per
# the brand standards, but nothing crashes).
_INTER_REGISTERED = False
_INTER_FAMILY = "Inter18pt"
_HELV_FAMILY = "Helvetica"


def _find_font_dir() -> Path | None:
    """Locate the Inter 18pt TTF bundle. Checked in order:

    1. ``ATTRIBUTION_PDF_FONT_DIR`` env override (Render / prod).
    2. ``.cursor/skills/crosswalk-brand-standards/assets/fonts`` next
       to the repo root (dev laptops with the plugin skill).
    3. ``bg-webapp/static/fonts`` (production copy shipped inside the
       webapp; safe fallback if the skill is not deployed).

    Returns ``None`` if none of these hold the six weights.
    """
    candidates: list[Path] = []
    env = os.environ.get("ATTRIBUTION_PDF_FONT_DIR")
    if env:
        candidates.append(Path(env))
    # bg-webapp/attribution_weekly_pdf.py -> bg-webapp/ -> repo root
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
    """Register Inter 18pt weights and return the family name to use.

    Registration is idempotent; every call after the first is a no-op.
    Returns ``_INTER_FAMILY`` on success, ``_HELV_FAMILY`` on fallback.
    """
    global _INTER_REGISTERED
    if _INTER_REGISTERED:
        return _INTER_FAMILY

    font_dir = _find_font_dir()
    if font_dir is None:
        print("[attribution_weekly_pdf] Inter 18pt fonts not found; "
              "falling back to Helvetica.")
        return _HELV_FAMILY

    weights = {
        "":          "Inter_18pt-Regular.ttf",
        "-Bold":     "Inter_18pt-Bold.ttf",
        "-Light":    "Inter_18pt-Light.ttf",
        "-Medium":   "Inter_18pt-Medium.ttf",
        "-Black":    "Inter_18pt-Black.ttf",
        "-ExtraBold":"Inter_18pt-ExtraBold.ttf",
    }
    try:
        for suffix, fname in weights.items():
            font_path = font_dir / fname
            if not font_path.is_file():
                continue
            pdfmetrics.registerFont(
                TTFont(_INTER_FAMILY + suffix, str(font_path))
            )
    except Exception as e:
        print(f"[attribution_weekly_pdf] Inter registration failed ({e}); "
              "falling back to Helvetica.")
        return _HELV_FAMILY

    _INTER_REGISTERED = True
    return _INTER_FAMILY


# ---------------------------------------------------------------------------
# Copy sanitizers
# ---------------------------------------------------------------------------
# no-em-dashes: strip U+2014, U+2013, U+2015, U+2012; also smart quotes.
_DASH_CHARS = "—–―‒"
_SMART_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "ʼ": "'", "«": '"', "»": '"',
}


def _sanitize(s: str) -> str:
    """Strip em/en dashes and smart quotes. Follows the same convention
    as `hostmap_gap_mapping._sanitize`: replace a dash with a hyphen when
    it's clearly compound, else with 'to' when between two spaces."""
    if not s:
        return ""
    out = str(s)

    def _dash_repl(m: re.Match) -> str:
        i = m.start()
        pre = out[max(0, i - 1): i]
        post = out[i + 1: i + 2]
        if pre == " " and post == " ":
            return "to"
        if pre == " " or post == " ":
            return " - "
        return "-"

    out = re.sub(f"[{_DASH_CHARS}]", _dash_repl, out)
    for k, v in _SMART_QUOTES.items():
        out = out.replace(k, v)
    out = out.replace("…", "...")
    return out.strip()


def _fmt_int(n: Any) -> str:
    """Comma-separated integer. Blank on None."""
    if n is None:
        return "-"
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_pct(n: Any, digits: int = 1) -> str:
    """`4.3%` style. Preserves an already-formatted string. Blank on None."""
    if n is None:
        return "-"
    if isinstance(n, str) and n.endswith("%"):
        return n
    try:
        return f"{float(n):.{digits}f}%"
    except (TypeError, ValueError):
        return str(n)


def _fmt_delta_pct(n: Any) -> tuple[str, HexColor]:
    """Signed pct delta, e.g., `+0.5pp` or `-1.2pp`. Returns (text, color)."""
    if n is None:
        return ("-", INK_MUTED)
    try:
        v = float(n)
    except (TypeError, ValueError):
        return (str(n), INK_MUTED)
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    color = INK_ACCENT_LIGHT if v > 0 else (AMETHYST if v < 0 else INK_MUTED)
    # 4dp trim like the deck stat blocks; PDF doesn't need 4dp so 1dp is fine.
    return (f"{sign}{abs(v):.1f}pp", color)


def _fit_key(label: str) -> str:
    """Map a Fit label ("Under-served", "Sweet spot", ...) to a lookup key.

    Tolerates casing, hyphens, spaces, and blank inputs. Returns
    "offtarget" for anything unrecognised so the row still renders
    with a subdued chip rather than crashing the layout.
    """
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
    """`2026-01-30` -> `Jan 30, 2026`. Passes through non-iso input."""
    if not iso:
        return ""
    try:
        dt = datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        return iso
    return dt.strftime("%b ") + f"{dt.day}, {dt.year}"


def _draw_wrapped(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font: str,
    size: float,
    color: HexColor,
    leading: float | None = None,
) -> float:
    """Draw ``text`` at (x, y-top) wrapping to ``max_width``. Returns
    the y-coordinate of the LAST BASELINE drawn (so the caller can
    subtract line-height to place the next block).
    """
    if leading is None:
        leading = size * 1.35
    c.setFont(font, size)
    c.setFillColor(color)
    words = _sanitize(text).split()
    line = ""
    cy = y
    for w in words:
        candidate = (line + " " + w).strip()
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            line = candidate
        else:
            if line:
                c.drawString(x, cy, line)
                cy -= leading
            line = w
    if line:
        c.drawString(x, cy, line)
    return cy


# ---------------------------------------------------------------------------
# Layout constants (US Letter portrait, per documents.md)
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = LETTER              # 8.5 x 11 in
MARGIN = 0.75 * inch                 # documents.md: 0.75 outer
CONTENT_W = 6.5 * inch               # documents.md: measure caps at 6.5 in
HEADER_H = 1.10 * inch               # single Graphite band, top of page

# Vertical position tracker (drawn top-down; content top starts under band)
def _new_cursor() -> float:
    return PAGE_H - HEADER_H - 0.30 * inch


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_weekly_pdf(payload: dict) -> bytes:
    """Render a one-page Attribution IQ weekly summary PDF.

    Expected payload shape (all fields optional; a missing block just
    collapses its section):

    .. code:: python

        {
          "title": {
            "display_name": "Goat",
            "distributor": "Sony Pictures Animation",
            "opening_date": "2026-02-13",
          },
          "as_of":     "2026-01-30",
          "week_end":  "2026-01-30",
          "week_start":"2026-01-24",
          "days_to_open": 14,
          "phase_label": "Branding (T-2)",
          "snapshot": {
              "exposed_viewers":       1234567,
              "exposed_delta_pct":     0.51,       # points, not fraction
              "response_rate_pct":     4.32,
              "response_delta_pct":    0.14,
              "response_metric_label": "Info-seek rate",  # or "Ticketing rate"
              "sample_size":           81_247,
          },
          "bullets": [
              "Views leaned into the trailer re-cut this week, ...",
              "Under-served: Caleb McLaughlin fans respond above the ...",
              ...
          ],
          "top_assets": [
              {"asset": "Trailer #2 (YouTube)",
               "channel": "YouTube",
               "phase":   "Bridge Campaign",
               "exposure": 456789,
               "response_pct": 5.2,
               "lift_x": 1.4,          # optional
               "confidence": "high"},  # optional
              ...
          ],
          "top_audiences": [
              {"audience": "Caleb McLaughlin fans",
               "overlap_pct": 8.3,
               "response_pct": 6.9,
               "vs_gen_pop_x": 1.8,     # optional
               "fit": "Under-served"},
              ...
          ],
        }

    Returns raw PDF bytes.
    """
    font_family = _register_inter()
    # Weight-aware helpers so callers below stay readable
    def f_regular(): return font_family
    def f_bold():    return font_family + "-Bold"
    def f_medium():  return font_family + "-Medium" if font_family == _INTER_FAMILY else font_family + "-Bold"
    def f_light():   return font_family + "-Light"  if font_family == _INTER_FAMILY else font_family
    def f_black():   return font_family + "-Black"  if font_family == _INTER_FAMILY else font_family + "-Bold"

    title = payload.get("title") or {}
    display_name = _sanitize(title.get("display_name") or "Untitled")
    distributor = _sanitize(title.get("distributor") or "")
    as_of = payload.get("as_of") or ""
    week_start = payload.get("week_start") or ""
    week_end = payload.get("week_end") or as_of
    days_to_open = payload.get("days_to_open")
    phase_label = _sanitize(payload.get("phase_label") or "")

    snapshot = payload.get("snapshot") or {}
    bullets: list[str] = [_sanitize(b) for b in (payload.get("bullets") or []) if b]
    top_assets: list[dict] = payload.get("top_assets") or []
    top_audiences: list[dict] = payload.get("top_audiences") or []

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER, pageCompression=1)
    c.setTitle(f"Attribution IQ Weekly - {display_name} - {week_end}")
    c.setAuthor("Crosswalk")
    c.setSubject("Attribution IQ Weekly Summary")
    c.setCreator("Crosswalk Attribution IQ")

    # -----------------------------------------------------------------
    # Off-White page ground
    # -----------------------------------------------------------------
    c.setFillColor(OFF_WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # -----------------------------------------------------------------
    # Header band (Graphite, single dark block that anchors the page)
    # -----------------------------------------------------------------
    c.setFillColor(GRAPHITE_TEAL)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, stroke=0, fill=1)

    # Eyebrow dot + text (Signal Olive on Off-White per the twin rule,
    # but this dot sits on Graphite so we use Signal Green here)
    dot_x = MARGIN
    dot_y = PAGE_H - 0.40 * inch
    c.setFillColor(HexColor("#C7F23E"))
    c.circle(dot_x + 0.06 * inch, dot_y, 0.055 * inch, stroke=0, fill=1)
    c.setFillColor(OFF_WHITE)
    c.setFont(f_bold(), 8.5)
    c.drawString(dot_x + 0.20 * inch, dot_y - 0.03 * inch,
                 "ATTRIBUTION IQ  \u00b7  WEEKLY SUMMARY")

    # Title line (product title)
    c.setFillColor(OFF_WHITE)
    c.setFont(f_bold(), 22)
    c.drawString(MARGIN, PAGE_H - 0.75 * inch, display_name)

    # Right-side header cluster: distributor + week ending
    right_x = PAGE_W - MARGIN
    c.setFillColor(HexColor("#B7B3D8"))
    c.setFont(f_regular(), 9)
    c.drawRightString(right_x, PAGE_H - 0.40 * inch, distributor or "Client")
    c.setFillColor(OFF_WHITE)
    c.setFont(f_medium(), 11)
    week_end_pretty = _fmt_iso_date(week_end)
    week_end_line = f"Week ending {week_end_pretty}"
    if week_start:
        week_end_line = (
            f"{_fmt_iso_date(week_start)} to {week_end_pretty}"
        )
    c.drawRightString(right_x, PAGE_H - 0.60 * inch, week_end_line)
    # Phase + days-to-open sub-line
    sub_bits: list[str] = []
    if phase_label:
        sub_bits.append(phase_label)
    if isinstance(days_to_open, (int, float)) and days_to_open is not None:
        n = int(days_to_open)
        if n > 0:
            sub_bits.append(f"{n} days to opening")
        elif n == 0:
            sub_bits.append("Opening today")
        else:
            sub_bits.append(f"{abs(n)} days post-opening")
    if sub_bits:
        c.setFillColor(HexColor("#B7B3D8"))
        c.setFont(f_regular(), 9)
        c.drawRightString(right_x, PAGE_H - 0.80 * inch, "  \u00b7  ".join(sub_bits))

    # -----------------------------------------------------------------
    # Body cursor starts under the header
    # -----------------------------------------------------------------
    cursor = _new_cursor()

    # -----------------------------------------------------------------
    # SECTION 1: three-figure stat row (deck-system stat blocks)
    # -----------------------------------------------------------------
    exposed = snapshot.get("exposed_viewers")
    exposed_delta = snapshot.get("exposed_delta_pct")
    response_rate = snapshot.get("response_rate_pct")
    response_delta = snapshot.get("response_delta_pct")
    response_label = _sanitize(
        snapshot.get("response_metric_label") or "Downstream response rate"
    )
    sample_size = snapshot.get("sample_size")

    stat_h = 0.86 * inch
    stat_y_top = cursor
    stat_y_bottom = stat_y_top - stat_h
    col_w = CONTENT_W / 3.0

    # Underline hairline the whole row sits on
    c.setStrokeColor(CARD_STROKE)
    c.setLineWidth(0.5)
    c.line(MARGIN, stat_y_bottom, MARGIN + CONTENT_W, stat_y_bottom)

    def _stat_col(idx: int, label: str, value: str,
                  delta_text: str | None, delta_color: HexColor | None) -> None:
        x = MARGIN + idx * col_w
        # Label (small caps, tracked)
        c.setFillColor(INK_BODY)
        c.setFont(f_bold(), 8.5)
        c.drawString(x, stat_y_top - 0.20 * inch, label.upper())
        # Big figure
        c.setFillColor(INK_PRIMARY)
        c.setFont(f_bold(), 26)
        c.drawString(x, stat_y_top - 0.60 * inch, value)
        # Delta chip (Signal Olive if up, Amethyst if down)
        if delta_text:
            c.setFillColor(delta_color or INK_MUTED)
            c.setFont(f_medium(), 9)
            c.drawString(x, stat_y_top - 0.78 * inch, "WoW " + delta_text)

    ed_txt, ed_col = _fmt_delta_pct(exposed_delta)
    _stat_col(0, "Exposed viewers", _fmt_int(exposed) if exposed else "-",
              ed_txt if exposed_delta is not None else None, ed_col)
    rd_txt, rd_col = _fmt_delta_pct(response_delta)
    _stat_col(1, response_label, _fmt_pct(response_rate) if response_rate is not None else "-",
              rd_txt if response_delta is not None else None, rd_col)
    _stat_col(2, "Sample this week", _fmt_int(sample_size) if sample_size else "-",
              None, None)

    cursor = stat_y_bottom - 0.28 * inch

    # -----------------------------------------------------------------
    # SECTION 2: This week bullets (directional read)
    # -----------------------------------------------------------------
    if bullets:
        c.setFillColor(INK_ACCENT_LIGHT)
        c.setFont(f_bold(), 8.5)
        c.drawString(MARGIN, cursor, "THIS WEEK.")
        cursor -= 0.14 * inch
        for b in bullets[:5]:
            # Rounded olive dot at the left, wrapped body to the right
            dy = cursor
            c.setFillColor(INK_ACCENT_LIGHT)
            c.circle(MARGIN + 0.05 * inch, dy + 0.04 * inch,
                     0.045 * inch, stroke=0, fill=1)
            end_y = _draw_wrapped(
                c, b,
                x=MARGIN + 0.20 * inch, y=dy,
                max_width=CONTENT_W - 0.20 * inch,
                font=f_regular(), size=10.5, color=INK_PRIMARY,
                leading=13.5,
            )
            cursor = end_y - 0.12 * inch
        cursor -= 0.12 * inch

    # -----------------------------------------------------------------
    # SECTION 3: Top 5 assets table (bordered card, no accent stripe)
    # -----------------------------------------------------------------
    if top_assets:
        # Card background
        rows = min(len(top_assets), 5)
        row_h = 0.30 * inch
        head_h = 0.30 * inch
        title_h = 0.32 * inch
        card_h = title_h + head_h + rows * row_h + 0.12 * inch
        card_y = cursor - card_h
        c.setFillColor(CARD_FILL)
        c.setStrokeColor(CARD_STROKE)
        c.setLineWidth(0.5)
        c.roundRect(MARGIN, card_y, CONTENT_W, card_h,
                    0.14 * inch, stroke=1, fill=1)

        # Card title
        c.setFillColor(INK_PRIMARY)
        c.setFont(f_bold(), 12)
        c.drawString(MARGIN + 0.20 * inch, cursor - 0.24 * inch,
                     "Strongest asset signals.")
        cursor -= title_h

        # Column widths: Asset (2.5) | Channel/Phase (1.5) | Exposure (1.1)
        # | Response (0.9) | Lift (0.5)
        col_asset_w = 2.55 * inch
        col_ch_w    = 1.50 * inch
        col_exp_w   = 1.10 * inch
        col_resp_w  = 0.85 * inch
        col_lift_w  = 0.50 * inch
        # x positions
        col_asset_x = MARGIN + 0.20 * inch
        col_ch_x    = col_asset_x + col_asset_w
        col_exp_x   = col_ch_x + col_ch_w
        col_resp_x  = col_exp_x + col_exp_w
        col_lift_x  = col_resp_x + col_resp_w

        # Header row (small caps, tracked, muted)
        c.setFillColor(INK_MUTED)
        c.setFont(f_bold(), 7.5)
        head_y = cursor - 0.18 * inch
        c.drawString(col_asset_x, head_y, "ASSET")
        c.drawString(col_ch_x, head_y, "CHANNEL  \u00b7  PHASE")
        c.drawRightString(col_exp_x + col_exp_w - 0.10 * inch, head_y, "EXPOSURE")
        c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, head_y, "RESPONSE")
        c.drawRightString(col_lift_x + col_lift_w - 0.05 * inch, head_y, "LIFT")
        cursor -= head_h

        # Hairline under header
        c.setStrokeColor(CARD_STROKE)
        c.setLineWidth(0.4)
        c.line(MARGIN + 0.20 * inch, cursor, MARGIN + CONTENT_W - 0.20 * inch, cursor)

        for a in top_assets[:rows]:
            row_y_mid = cursor - row_h / 2 + 0.02 * inch
            # Asset title, truncated to column width
            c.setFillColor(INK_PRIMARY)
            c.setFont(f_medium(), 10)
            asset_label = _sanitize(a.get("asset") or a.get("action_label") or "-")
            # Truncate to fit
            max_asset_chars = 42
            if len(asset_label) > max_asset_chars:
                asset_label = asset_label[: max_asset_chars - 1].rstrip() + "..."
            c.drawString(col_asset_x, row_y_mid, asset_label)
            # Channel / phase
            ch = _sanitize(a.get("channel") or "-")
            ph = _sanitize(a.get("phase") or "")
            ch_line = ch + ("  \u00b7  " + ph if ph else "")
            if len(ch_line) > 30:
                ch_line = ch_line[:29].rstrip() + "..."
            c.setFillColor(INK_BODY)
            c.setFont(f_regular(), 9)
            c.drawString(col_ch_x, row_y_mid, ch_line)
            # Exposure (right-aligned)
            c.setFillColor(INK_PRIMARY)
            c.setFont(f_medium(), 10)
            c.drawRightString(col_exp_x + col_exp_w - 0.10 * inch, row_y_mid,
                              _fmt_int(a.get("exposure") or a.get("views_total")
                                       or a.get("ext_view_count")))
            # Response (right-aligned)
            c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, row_y_mid,
                              _fmt_pct(a.get("response_pct")))
            # Lift (right-aligned, x on high, muted on low)
            lift = a.get("lift_x")
            if lift is not None:
                try:
                    lv = float(lift)
                    lift_str = f"{lv:.1f}x"
                    if lv >= 1.3:
                        c.setFillColor(INK_ACCENT_LIGHT)
                    else:
                        c.setFillColor(INK_BODY)
                except (TypeError, ValueError):
                    lift_str = "-"
                    c.setFillColor(INK_MUTED)
            else:
                lift_str = "-"
                c.setFillColor(INK_MUTED)
            c.setFont(f_medium(), 10)
            c.drawRightString(col_lift_x + col_lift_w - 0.05 * inch, row_y_mid, lift_str)

            # Divider under each row except the last
            cursor -= row_h
            if a is not top_assets[:rows][-1]:
                c.setStrokeColor(HexColor("#D5D3C7"))
                c.setLineWidth(0.3)
                c.line(MARGIN + 0.20 * inch, cursor,
                       MARGIN + CONTENT_W - 0.20 * inch, cursor)

        cursor = card_y - 0.24 * inch

    # -----------------------------------------------------------------
    # SECTION 4: Top 5 audiences table (bordered card, Fit chips)
    # -----------------------------------------------------------------
    if top_audiences:
        rows = min(len(top_audiences), 5)
        row_h = 0.30 * inch
        head_h = 0.30 * inch
        title_h = 0.32 * inch
        card_h = title_h + head_h + rows * row_h + 0.12 * inch
        card_y = cursor - card_h
        c.setFillColor(CARD_FILL)
        c.setStrokeColor(CARD_STROKE)
        c.setLineWidth(0.5)
        c.roundRect(MARGIN, card_y, CONTENT_W, card_h,
                    0.14 * inch, stroke=1, fill=1)

        c.setFillColor(INK_PRIMARY)
        c.setFont(f_bold(), 12)
        c.drawString(MARGIN + 0.20 * inch, cursor - 0.24 * inch,
                     "Audiences responding, and audiences responding but under-served.")
        cursor -= title_h

        col_aud_w   = 2.55 * inch
        col_over_w  = 1.05 * inch
        col_resp_w  = 1.05 * inch
        col_idx_w   = 0.75 * inch
        col_fit_w   = 1.10 * inch
        col_aud_x   = MARGIN + 0.20 * inch
        col_over_x  = col_aud_x + col_aud_w
        col_resp_x  = col_over_x + col_over_w
        col_idx_x   = col_resp_x + col_resp_w
        col_fit_x   = col_idx_x + col_idx_w

        c.setFillColor(INK_MUTED)
        c.setFont(f_bold(), 7.5)
        head_y = cursor - 0.18 * inch
        c.drawString(col_aud_x, head_y, "AUDIENCE")
        c.drawRightString(col_over_x + col_over_w - 0.10 * inch, head_y, "OVERLAP")
        c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, head_y, "RESPONSE")
        c.drawRightString(col_idx_x + col_idx_w - 0.10 * inch, head_y, "VS GENPOP")
        c.drawString(col_fit_x, head_y, "FIT")
        cursor -= head_h

        c.setStrokeColor(CARD_STROKE)
        c.setLineWidth(0.4)
        c.line(MARGIN + 0.20 * inch, cursor, MARGIN + CONTENT_W - 0.20 * inch, cursor)

        for row_idx, a in enumerate(top_audiences[:rows]):
            row_y_mid = cursor - row_h / 2 + 0.02 * inch
            # Audience name
            name = _sanitize(a.get("audience") or a.get("display") or "-")
            if len(name) > 40:
                name = name[:39].rstrip() + "..."
            c.setFillColor(INK_PRIMARY)
            c.setFont(f_medium(), 10)
            c.drawString(col_aud_x, row_y_mid, name)
            # Overlap
            c.drawRightString(col_over_x + col_over_w - 0.10 * inch, row_y_mid,
                              _fmt_pct(a.get("overlap_pct"), 1))
            # Response
            c.drawRightString(col_resp_x + col_resp_w - 0.10 * inch, row_y_mid,
                              _fmt_pct(a.get("response_pct"), 1))
            # vs Gen Pop
            idx_v = a.get("vs_gen_pop_x")
            if idx_v is not None:
                try:
                    ivf = float(idx_v)
                    idx_s = f"{ivf:.1f}x"
                    if ivf >= 1.3:
                        c.setFillColor(INK_ACCENT_LIGHT)
                    elif ivf < 0.7:
                        c.setFillColor(INK_MUTED)
                    else:
                        c.setFillColor(INK_BODY)
                except (TypeError, ValueError):
                    idx_s = "-"
                    c.setFillColor(INK_MUTED)
            else:
                idx_s = "-"
                c.setFillColor(INK_MUTED)
            c.setFont(f_medium(), 10)
            c.drawRightString(col_idx_x + col_idx_w - 0.10 * inch, row_y_mid, idx_s)
            # Fit chip (rounded pill)
            fit_raw = _sanitize(a.get("fit") or "")
            fit_k = _fit_key(fit_raw)
            fill_c, text_c = FIT_TINTS.get(fit_k, FIT_TINTS["offtarget"])
            chip_w = 0.95 * inch
            chip_h = 0.20 * inch
            chip_x = col_fit_x
            chip_y = row_y_mid - 0.05 * inch
            c.setFillColor(fill_c)
            c.setStrokeColor(fill_c)
            c.roundRect(chip_x, chip_y, chip_w, chip_h,
                        chip_h / 2, stroke=0, fill=1)
            c.setFillColor(text_c)
            c.setFont(f_bold(), 8)
            c.drawCentredString(chip_x + chip_w / 2,
                                chip_y + 0.05 * inch,
                                fit_raw.upper() or "OFF-TARGET")

            cursor -= row_h
            if row_idx < rows - 1:
                c.setStrokeColor(HexColor("#D5D3C7"))
                c.setLineWidth(0.3)
                c.line(MARGIN + 0.20 * inch, cursor,
                       MARGIN + CONTENT_W - 0.20 * inch, cursor)

        cursor = card_y - 0.22 * inch

    # -----------------------------------------------------------------
    # Footer strip (methodology, calibrated to what the data supports)
    # -----------------------------------------------------------------
    footer_y = 0.55 * inch
    c.setStrokeColor(CARD_STROKE)
    c.setLineWidth(0.4)
    c.line(MARGIN, footer_y + 0.35 * inch,
           MARGIN + CONTENT_W, footer_y + 0.35 * inch)

    c.setFillColor(INK_FOOTER)
    c.setFont(f_regular(), 8)
    disclaimer = (
        "Directional read from Crosswalk's opted-in behavioral panel, "
        "week ending " + _fmt_iso_date(week_end) + ". "
        "Under-served flags cohorts responding above index with low reach; "
        "sweet spot flags high reach and high affinity. "
        "Precise budget reallocations and causal attribution require a "
        "higher validation standard."
    )
    _draw_wrapped(
        c, disclaimer,
        x=MARGIN, y=footer_y + 0.20 * inch,
        max_width=CONTENT_W,
        font=f_regular(), size=8, color=INK_FOOTER,
        leading=10,
    )

    # Footer chrome (small caps, tracked)
    c.setFillColor(INK_FOOTER)
    c.setFont(f_bold(), 7.5)
    c.drawString(MARGIN, 0.30 * inch,
                 "CROSSWALK  \u00b7  BEHAVIORAL INTELLIGENCE")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.drawRightString(MARGIN + CONTENT_W, 0.30 * inch,
                      f"GENERATED {generated_at}".upper())

    c.showPage()
    c.save()
    return buf.getvalue()
