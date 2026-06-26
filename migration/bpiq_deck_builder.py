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

# Font stack - python-pptx writes font names directly into the XML; if
# the viewer doesn't have these, PowerPoint substitutes automatically.
FONT_DISPLAY = "Helvetica Neue"
FONT_BODY    = "Helvetica Neue"
FONT_NUMERIC = "Helvetica Neue"


# ─────────────────────────────────────────────────────────────────────
#  Number / money formatting helpers
# ─────────────────────────────────────────────────────────────────────
def fmt_money(n: Any) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "$0"
    if abs(n) >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n/1_000:.0f}K"
    return f"${n:,.0f}"


def fmt_num(n: Any) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(n) >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.0f}K"
    return f"{int(n):,}"


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
    """Solid full-bleed background rectangle."""
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H)
    bg.line.fill.background()
    bg.fill.solid()
    bg.fill.fore_color.rgb = color
    bg.shadow.inherit = False
    return bg


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
    paragraphs = str(text).split("\n")
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
        r.font.italic = italic
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


def _page_footer(slide, idx: int, total: int, project_label: str, *, on_dark: bool = True):
    """Bottom bar: project tag (left), CROSSWALK BehaviorGraph (center
    bottom-left), and `idx / total` (right). Echoes LISA's footer."""
    color = MUTED_DK if on_dark else MUTED_LT
    _add_text(
        slide, Inches(0.5), Inches(7.10), Inches(6), Inches(0.3),
        project_label.upper(),
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
def _download_image(url: str, max_dim: int = 2000,
                    crop_aspect: Optional[float] = None) -> Optional[str]:
    """Fetch a remote image to a tmp JPEG. Optional `crop_aspect` (w/h)
    center-crops the image to fit a target slot - lets the cover slide
    place a fixed-aspect hero without distortion. Returns path or None
    on any failure (deck-builder must NEVER crash because of a bad
    photo URL)."""
    if not url or not isinstance(url, str):
        return None
    try:
        r = requests.get(url, timeout=8, stream=True, headers={
            "User-Agent": "BehaviorGraph-DeckBuilder/1.0",
        })
        if r.status_code != 200:
            return None
        raw = io.BytesIO(r.content)
        im = Image.open(raw).convert("RGB")
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
    font_path_candidates = [
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


def _render_crosswalk_logo_png(on_dark: bool = True) -> str:
    """Pillow-rendered Crosswalk wordmark for the cover slide. No SVG
    dependency required. Returns a path to a transparent PNG."""
    w, h = 760, 100
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fg = (244, 241, 234, 255) if on_dark else (12, 22, 24, 255)
    accent = (200, 230, 0, 255)
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
    # Lozenge accent + product subtitle, BehaviorGraph-style
    try:
        small = ImageFont.truetype(font.path, 22) if hasattr(font, "path") else font
    except Exception:
        small = font
    d.rectangle((420, 60, 460, 84), fill=accent)
    d.text((470, 60), "BehaviorGraph", fill=fg, font=small)
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


def _derive_context(data: dict, image_url: Optional[str],
                    category: Optional[str]) -> DeckCtx:
    totals = data.get("totals") or {}
    val    = data.get("valuation") or {}
    rates  = val.get("rates") or {}
    cg     = data.get("control_group") or {}
    is_auto = (str(category or "").strip().lower() == "automotive")
    conv = data.get("conversions") or {}
    has_conv = (
        not is_auto
        and bool(conv.get("enabled"))
        and not bool(conv.get("low_signal"))
        and (conv.get("post_users_projected") or 0) > 0
    )
    yearly = data.get("yearly_breakdown") or []
    has_yearly = isinstance(yearly, list) and len(yearly) >= 2

    pre_pen  = float(totals.get("audience_pen_pre_pct")  or 0)
    post_pen = float(totals.get("audience_pen_post_pct") or 0)
    delta_pp = round(post_pen - pre_pen, 2)
    delta_rel = ((post_pen - pre_pen) / pre_pen * 100) if pre_pen else 0.0

    # Cover image is placed in a 7.6"×7.5" slot - center-crop to the
    # matching aspect (≈1.013) so faces stay framed and the slot fills
    # cleanly with no letterboxing.
    cover = _download_image(image_url, crop_aspect=7.6 / 7.5) if image_url else None
    if cover is None:
        cover = _generate_typographic_hero(data.get("brand_partner")
                                           or data.get("project_name") or "BPIQ")

    logo = _render_crosswalk_logo_png(on_dark=True)

    project = data.get("project_name") or "Brand Partnership Valuation"
    brand   = data.get("brand_partner") or "Brand"
    cat_lbl = (category or "").strip().title() or "Brand Partnership"
    start   = data.get("start_date") or ""
    end     = data.get("end_date")   or ""
    win     = f"{start} → {end}" if start and end else ""

    src = (
        f"Source: Crosswalk BehaviorGraph · "
        f"Window {win} · "
        f"Attribution {int(data.get('attribution_window_days') or 0)}d · "
        f"Panel n={int(totals.get('pre_users') or 0)} pre / "
        f"{int(totals.get('post_users') or 0)} post"
    )

    return DeckCtx(
        project=project,
        brand=brand,
        category_label=cat_lbl,
        is_auto=is_auto,
        cover_image=cover,
        logo=logo,
        total_value=float(val.get("total_brand_value") or 0),
        emv=float(val.get("earned_media_value") or 0),
        bev=float(val.get("brand_engagement_value") or 0),
        blv=float(val.get("brand_lift_value") or 0),
        cv=float(val.get("conversion_value") or 0) if has_conv else 0.0,
        incr_users=int(val.get("incremental_users") or 0),
        target_size=int(data.get("audience_size") or 0),
        audience_proj=int(data.get("projected_audience_size") or 0),
        pre_users=int(totals.get("pre_users") or 0),
        post_users=int(totals.get("post_users") or 0),
        pre_users_proj=int(totals.get("pre_users_projected") or 0),
        post_users_proj=int(totals.get("post_users_projected") or 0),
        pre_pen=pre_pen,
        post_pen=post_pen,
        delta_pp=delta_pp,
        delta_rel=delta_rel,
        incr_lift_pp=float(cg.get("incremental_lift_pp") or 0),
        incr_lift_rel=float(cg.get("incremental_lift_rel_pct") or 0),
        campaign_window=win,
        attribution_d=int(data.get("attribution_window_days") or 0),
        panel_size=int(data.get("audience_size") or 0),
        sample_pre=int(totals.get("pre_users") or 0),
        sample_post=int(totals.get("post_users") or 0),
        has_conversions=has_conv,
        has_yearly=has_yearly,
        sources_line=src,
        data=data,
    )


def _engagement_headline(ctx: DeckCtx) -> str:
    """MIDG-style declarative headline that names the lift, the
    consumer count, and the brand."""
    if ctx.delta_pp <= 0:
        return (
            f"The campaign held {ctx.brand}'s baseline.\n"
            f"{fmt_num(ctx.post_users_proj)} consumers engaged with the brand "
            f"in the post-window."
        )
    moved = max(0, ctx.post_users_proj - ctx.pre_users_proj)
    return (
        f"A {ctx.delta_pp:.1f}pt Lift In Brand Engagement\n"
        f"That Moved {fmt_num(moved)} New Consumers Toward {ctx.brand}."
    )


def _final_insight_lines(ctx: DeckCtx) -> tuple[str, str]:
    """Two-line editorial close - mirrors LISA's 'LISA wears it, it
    sells out.' format. We synthesize from the data so every partnership
    gets a tuned message."""
    if ctx.total_value <= 0:
        return ("The partnership ran.", "The audience didn't move - yet.")
    if ctx.delta_rel >= 200:
        line1 = f"When {ctx.brand} shows up with"
        line2 = f"{ctx.project.split('x')[0].strip() or 'the talent'}, the audience triples."
    elif ctx.delta_rel >= 100:
        line1 = f"{ctx.brand} more than doubles"
        line2 = "its share of voice across the audience."
    elif ctx.delta_rel >= 25:
        line1 = f"{ctx.brand} won meaningful"
        line2 = f"new ground — {ctx.delta_rel:.0f}% lift, post-campaign."
    else:
        line1 = "Steady lift,"
        line2 = "compounding reach over time."
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
            s.shapes.add_picture(
                ctx.cover_image,
                Inches(5.8), Inches(0),
                width=Inches(7.6), height=Inches(7.5),
            )
        except Exception:
            pass
    # Left-hand title stack. Title font size is scaled to length so
    # long partnerships (e.g. "Penélope Cruz x CHANEL Endorsement")
    # still fit inside the available block without crashing into the
    # subtitle below.
    _add_text(s, Inches(0.6), Inches(0.6), Inches(5.2), Inches(0.4),
              "A BRAND PARTNERSHIP VALUATION · 2026",
              size=10, bold=True, color=MUTED_DK, letter_spacing=0.2)
    title_text = ctx.project + "."
    title_len  = len(title_text)
    if   title_len <= 20: title_size = 64
    elif title_len <= 30: title_size = 52
    elif title_len <= 42: title_size = 42
    else:                 title_size = 34
    _add_text(s, Inches(0.6), Inches(1.7), Inches(5.0), Inches(3.6),
              title_text,
              size=title_size, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    # Subtitle - MIDG-style tagline pulled from headline narrative.
    # Anchored to bottom of left column so it doesn't depend on
    # exactly where the title block ends.
    subtitle = _engagement_headline(ctx).split("\n")[0]
    _add_text(s, Inches(0.6), Inches(5.6), Inches(5.0), Inches(1.0),
              subtitle,
              size=16, color=FG_LIGHT, spacing=1.2, font=FONT_BODY,
              anchor="bottom")
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


def _slide_methodology(prs, ctx: DeckCtx, idx: int, total: int):
    """MIDG 'Direct Observational Data' slide - credibility / what
    makes BehaviorGraph trustworthy."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "01 · The Data Foundation", on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.4),
              "Direct observational data.\nNo surveys. No self-report.",
              size=44, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    body = (
        "Crosswalk's BehaviorGraph captures the real digital behavior of a "
        "zero-party-data panel of opted-in U.S. consumers. Unlike surveys "
        "that ask people what they remember doing, BehaviorGraph records "
        "what they actually did - which sites they visited, which platforms "
        "they spent time on, and which brand touchpoints appeared in their "
        "behavior before, during, and after the partnership ran."
    )
    _add_text(s, Inches(0.6), Inches(3.3), Inches(7), Inches(2.6),
              body, size=14, color=FG_DARK, spacing=1.35)
    # Right column - 3 stat tiles
    stats = [
        ("Panel size",   f"{fmt_num(ctx.panel_size)}", "Targeted cohort"),
        ("Window",       ctx.campaign_window or "—",   "Pre + campaign + post"),
        ("Attribution",  f"{ctx.attribution_d}d",     "Post-event window"),
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
                  Inches(col_w.inches - 0.5), Inches(0.6),
                  value, size=22, bold=True, color=FG_DARK,
                  font=FONT_NUMERIC)
        _add_text(s, col_x + Inches(0.25), y + Inches(0.72),
                  Inches(col_w.inches - 0.5), Inches(0.3),
                  sub, size=9, color=MUTED_LT, italic=True)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_audience_scale(prs, ctx: DeckCtx, idx: int, total: int):
    """LISA-style 'THE LISA AUDIENCE · SCALE' big stat slide."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             f"02 · The {ctx.brand} Audience · Scale", on_dark=True)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"Reach and resonance,\nside by side.",
              size=44, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    # Three giant stat tiles
    tiles = [
        (fmt_num(ctx.audience_proj),
         "U.S. CONSUMER AUDIENCE",
         f"Projected from {fmt_num(ctx.target_size)} zero-party panelists "
         f"observed in window."),
        (fmt_num(ctx.post_users_proj),
         f"POST-CAMPAIGN {ctx.brand.upper()} CONSUMERS",
         f"{fmt_pct(ctx.post_pen)} of the targeted cohort had a brand "
         f"touchpoint in the post-window."),
        (fmt_money(ctx.total_value),
         "TOTAL BRAND VALUE DELIVERED",
         f"Sum of Earned Media, Brand Engagement, "
         f"{'Brand Lift, and Conversion' if ctx.has_conversions else 'and Brand Lift'} value."),
    ]
    tile_w = Inches(4.0)
    gutter = Inches(0.15)
    base_x = Inches(0.6)
    for i, (num, label, sub) in enumerate(tiles):
        x = base_x + (tile_w + gutter) * i
        y = Inches(3.4)
        _add_rect(s, x, y, tile_w, Inches(3.1),
                  fill=None, line=MUTED_DK, line_w=0.5)
        _add_text(s, x + Inches(0.25), y + Inches(0.25),
                  tile_w - Inches(0.5), Inches(1.4),
                  num, size=54, bold=True, color=ACCENT,
                  font=FONT_NUMERIC, spacing=0.95)
        _add_text(s, x + Inches(0.25), y + Inches(1.7),
                  tile_w - Inches(0.5), Inches(0.4),
                  label, size=10, bold=True, color=FG_LIGHT,
                  letter_spacing=0.14)
        _add_text(s, x + Inches(0.25), y + Inches(2.1),
                  tile_w - Inches(0.5), Inches(0.9),
                  sub, size=10, color=MUTED_DK, spacing=1.3)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_engagement_lift(prs, ctx: DeckCtx, idx: int, total: int):
    """MIDG centerpiece - 'A 3.2pt Lift That Moved $19M' headline +
    Pre vs Post stat blocks + Read As callout."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             f"03 · {ctx.brand} · Engagement Lift", on_dark=False)
    headline_lines = _engagement_headline(ctx).split("\n")
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.6),
              headline_lines[0],
              size=42, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    if len(headline_lines) > 1:
        _add_text(s, Inches(0.6), Inches(2.05), Inches(11), Inches(0.7),
                  headline_lines[1],
                  size=22, color=FG_DARK, font=FONT_DISPLAY)
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
    _add_text(s, Inches(0.9), plate_y + Inches(1.9), plate_w - Inches(0.5),
              Inches(0.4),
              f"{fmt_num(ctx.pre_users_proj)} U.S. consumers",
              size=14, color=FG_LIGHT)
    _add_text(s, Inches(0.9), plate_y + Inches(2.25), plate_w - Inches(0.5),
              Inches(0.4),
              "engaged with the brand in the year before campaign launch.",
              size=11, italic=True, color=MUTED_DK)
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
    _add_text(s, post_x + Inches(0.3), plate_y + Inches(1.9),
              plate_w - Inches(0.5), Inches(0.4),
              f"{fmt_num(ctx.post_users_proj)} U.S. consumers",
              size=14, color=FG_DARK)
    _add_text(s, post_x + Inches(0.3), plate_y + Inches(2.25),
              plate_w - Inches(0.5), Inches(0.4),
              f"engaged with the brand within {ctx.attribution_d}d of "
              f"campaign end.",
              size=11, italic=True, color=FG_DARK)
    # Read As callout, MIDG-style
    moved = max(0, ctx.post_users_proj - ctx.pre_users_proj)
    read_as = (
        f"Read As: {fmt_num(moved)} more U.S. consumers were observed "
        f"engaging with {ctx.brand} after the campaign — a relative "
        f"+{ctx.delta_rel:.1f}% lift over baseline."
    )
    _add_text(s, Inches(0.6), Inches(6.4), Inches(12), Inches(0.5),
              read_as, size=12, italic=True, color=MUTED_LT, align="center")
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_total_value_hero(prs, ctx: DeckCtx, idx: int, total: int):
    """Hero $ slide - LISA's 'ONE INSIGHT' moment, repurposed for the
    total brand value. Solid pink so it explodes off the page after the
    cream lift slide."""
    s = _blank(prs)
    _add_bg(s, BG_PINK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "04 · The Headline", on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.4), Inches(12), Inches(1.3),
              "Total Brand Value Delivered.",
              size=44, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    # The big number
    _add_text(s, Inches(0.6), Inches(2.7), Inches(12), Inches(2.6),
              fmt_money(ctx.total_value),
              size=180, bold=True, color=FG_DARK,
              font=FONT_NUMERIC, spacing=0.85, align="left")
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
    base_y = Inches(5.7)
    for i, (label, value) in enumerate(parts):
        x = base_x + tile_w * i + Inches(0.05 if i > 0 else 0)
        _add_rect(s, x, base_y, tile_w - Inches(0.1), Inches(1.05),
                  fill=FG_DARK, line=None)
        _add_text(s, x + Inches(0.25), base_y + Inches(0.15),
                  tile_w - Inches(0.4), Inches(0.3),
                  label.upper(), size=9, bold=True, color=ACCENT,
                  letter_spacing=0.16)
        _add_text(s, x + Inches(0.25), base_y + Inches(0.42),
                  tile_w - Inches(0.4), Inches(0.55),
                  fmt_money(value), size=22, bold=True, color=FG_LIGHT,
                  font=FONT_NUMERIC)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_emv(prs, ctx: DeckCtx, idx: int, total: int):
    """Per-platform earned media breakdown - LISA index-list pattern.
    Each row: PLATFORM · post% · +lift% · incremental consumers · CPM · EMV."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "05 · Earned Media Value", on_dark=True)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"{fmt_money(ctx.emv)} of earned media,\n"
              f"platform by platform.",
              size=40, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    # Pull EMV breakdown, ranked by emv_value desc, take top 7
    rows = sorted(
        (ctx.data.get("valuation") or {}).get("emv_breakdown") or [],
        key=lambda r: float(r.get("emv_value") or 0),
        reverse=True,
    )[:7]
    if not rows:
        _add_text(s, Inches(0.6), Inches(4), Inches(11), Inches(0.6),
                  "No platform-level signal in this window.",
                  size=14, italic=True, color=MUTED_DK)
        _page_footer(s, idx, total, ctx.project, on_dark=True)
        return s
    table_top = Inches(3.4)
    row_h = Inches(0.45)
    # Header row
    headers = [
        ("PLATFORM",         Inches(0.6), Inches(2.3)),
        ("INCR. CONSUMERS",  Inches(3.1), Inches(2.5)),
        ("CPM",              Inches(5.8), Inches(1.4)),
        ("EMV",              Inches(7.4), Inches(2.0)),
    ]
    for label, x, w in headers:
        _add_text(s, x, table_top, w, Inches(0.3),
                  label, size=9, bold=True, color=MUTED_DK,
                  letter_spacing=0.16)
    # Underline below header
    line = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6),
                              table_top + Inches(0.3), Inches(12.1),
                              Emu(8000))
    line.line.fill.background()
    line.fill.solid()
    line.fill.fore_color.rgb = MUTED_DK
    # Rows
    for i, r in enumerate(rows):
        y = table_top + Inches(0.45) + row_h * i
        _add_text(s, Inches(0.6), y, Inches(2.3), row_h,
                  r.get("platform") or "—",
                  size=14, bold=True, color=FG_LIGHT)
        _add_text(s, Inches(3.1), y, Inches(2.5), row_h,
                  fmt_num(r.get("incremental_users_projected") or 0),
                  size=14, color=FG_LIGHT, font=FONT_NUMERIC)
        _add_text(s, Inches(5.8), y, Inches(1.4), row_h,
                  f"${float(r.get('emv_per_user_rate') or 0):.2f}",
                  size=14, color=MUTED_DK, font=FONT_NUMERIC)
        _add_text(s, Inches(7.4), y, Inches(2.0), row_h,
                  fmt_money(r.get("emv_value") or 0),
                  size=14, bold=True, color=ACCENT, font=FONT_NUMERIC)
    # Right-side "WHY IT MATTERS" callout, LISA-style
    callout_x = Inches(10.0)
    _add_rect(s, callout_x, Inches(3.4), Inches(2.8), Inches(2.8),
              fill=None, line=ACCENT, line_w=1.0)
    _add_text(s, callout_x + Inches(0.25), Inches(3.55), Inches(2.5),
              Inches(0.3),
              "WHY IT MATTERS", size=9, bold=True, color=ACCENT,
              letter_spacing=0.18)
    _add_text(s, callout_x + Inches(0.25), Inches(3.9), Inches(2.5),
              Inches(2.0),
              f"Every incremental brand consumer is priced at the platform's "
              f"organic-engagement equivalent of 2026 industry CPM benchmarks. "
              f"This is the open-market value of the audience the partnership "
              f"earned for {ctx.brand}.",
              size=10, color=FG_LIGHT, spacing=1.35)
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_DK)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_brand_engagement_value(prs, ctx: DeckCtx, idx: int, total: int):
    """BEV calculator slide. Cream bg, LISA stat-tile rhythm."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "06 · Brand Engagement Value", on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"{fmt_money(ctx.bev)} of post-campaign\nbrand engagement.",
              size=40, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    # Math row
    rate = float((ctx.data.get("valuation") or {})
                 .get("rates", {}).get("bev_per_user") or 0)
    _math_calculator(
        s, Inches(0.6), Inches(3.6), Inches(8.0),
        [
            ("Post-Campaign Engaged Consumers", fmt_num(ctx.post_users_proj)),
            ("× $/consumer rate",               f"$ {rate:.2f}"),
        ],
        total_label="Brand Engagement Value",
        total_value=fmt_money(ctx.bev),
        on_dark=False,
        accent=RGBColor(0x63, 0x66, 0xF1),
    )
    # Why-it-matters
    _add_rect(s, Inches(9.0), Inches(3.6), Inches(3.8), Inches(2.8),
              fill=None, line=FG_DARK, line_w=0.75)
    _add_text(s, Inches(9.25), Inches(3.75), Inches(3.4), Inches(0.3),
              "WHY IT MATTERS", size=9, bold=True, color=FG_DARK,
              letter_spacing=0.18)
    _add_text(s, Inches(9.25), Inches(4.1), Inches(3.4), Inches(2.2),
              "Every consumer who engaged with the brand in the post-window "
              "carries open-market value. This is the dollar equivalent of "
              "the total post-campaign audience BehaviorGraph observed for "
              f"{ctx.brand}.",
              size=11, color=FG_DARK, spacing=1.4)
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_brand_lift_value(prs, ctx: DeckCtx, idx: int, total: int):
    """BLV slide - difference-in-differences vs. control."""
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "07 · Brand Lift Value", on_dark=True)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"{fmt_money(ctx.blv)} of incremental\nbrand engagement.",
              size=40, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    rate = float((ctx.data.get("valuation") or {})
                 .get("rates", {}).get("blv_per_incr_user") or 0)
    _math_calculator(
        s, Inches(0.6), Inches(3.6), Inches(8.0),
        [
            ("Incremental Consumers (post − pre · DiD vs Gen Pop)",
             fmt_num(ctx.incr_users)),
            ("× $/incremental-consumer rate", f"$ {rate:.2f}"),
        ],
        total_label="Brand Lift Value",
        total_value=fmt_money(ctx.blv),
        on_dark=True,
        accent=RGBColor(0xF5, 0x9E, 0x0B),
    )
    _add_rect(s, Inches(9.0), Inches(3.6), Inches(3.8), Inches(2.8),
              fill=None, line=ACCENT, line_w=1.0)
    _add_text(s, Inches(9.25), Inches(3.75), Inches(3.4), Inches(0.3),
              "WHY IT MATTERS", size=9, bold=True, color=ACCENT,
              letter_spacing=0.18)
    detail = (
        "Brand Lift Value isolates the NET-NEW engagement attributable "
        "to the partnership. We subtract a size-matched gen-pop control "
        "cohort's natural drift so we don't double-count baseline brand "
        "interest the campaign didn't move."
    )
    _add_text(s, Inches(9.25), Inches(4.1), Inches(3.4), Inches(2.2),
              detail, size=11, color=FG_LIGHT, spacing=1.4)
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_DK)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_conversion_value(prs, ctx: DeckCtx, idx: int, total: int):
    """CV slide - only shown when conversions are enabled and signal
    is good (skipped for automotive partnerships per .cursor/rules)."""
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "08 · Conversion Value", on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"{fmt_money(ctx.cv)} of observed\npost-campaign conversions.",
              size=40, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    conv = ctx.data.get("conversions") or {}
    rate = float((ctx.data.get("valuation") or {})
                 .get("rates", {}).get("conv_value_per_user") or 0)
    _math_calculator(
        s, Inches(0.6), Inches(3.6), Inches(8.0),
        [
            ("Post-Campaign Conversions",
             fmt_num(conv.get("post_users_projected") or 0)),
            ("× Average Order Value (AOV)", f"$ {rate:.2f}"),
        ],
        total_label="Conversion Value",
        total_value=fmt_money(ctx.cv),
        on_dark=False,
        accent=RGBColor(0xEF, 0x44, 0x44),
    )
    _add_rect(s, Inches(9.0), Inches(3.6), Inches(3.8), Inches(2.8),
              fill=None, line=FG_DARK, line_w=0.75)
    _add_text(s, Inches(9.25), Inches(3.75), Inches(3.4), Inches(0.3),
              "WHY IT MATTERS", size=9, bold=True, color=FG_DARK,
              letter_spacing=0.18)
    _add_text(s, Inches(9.25), Inches(4.1), Inches(3.4), Inches(2.2),
              "Direct purchase-funnel signal: orders, sign-ups, or other "
              "transactional events observed in panel behavior in the "
              "post-window. AOV is editable in the live dashboard.",
              size=11, color=FG_DARK, spacing=1.4)
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _math_calculator(slide, left, top, width, rows, *,
                     total_label: str, total_value: str,
                     on_dark: bool, accent: RGBColor):
    """Render a calculator-style math block. Matches the dashboard's
    in-card calculator (label · value, then a single total row)."""
    body_color = FG_LIGHT if on_dark else FG_DARK
    muted = MUTED_DK if on_dark else MUTED_LT
    row_h = Inches(0.85)
    for i, (label, value) in enumerate(rows):
        y = top + row_h * i
        _add_text(slide, left, y + Inches(0.15), Inches(width.inches * 0.65),
                  Inches(0.5),
                  label, size=14, color=muted)
        _add_text(slide, left + Inches(width.inches * 0.65),
                  y + Inches(0.05), Inches(width.inches * 0.35), Inches(0.6),
                  value, size=24, bold=True, color=body_color,
                  font=FONT_NUMERIC, align="right")
        if i < len(rows) - 1:
            sep = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, left, y + row_h - Emu(4000),
                width, Emu(4000))
            sep.line.fill.background()
            sep.fill.solid()
            sep.fill.fore_color.rgb = muted
    # Total bar
    total_y = top + row_h * len(rows) + Inches(0.1)
    _add_rect(slide, left, total_y, width, Inches(0.05),
              fill=accent, line=None)
    _add_text(slide, left, total_y + Inches(0.2),
              Inches(width.inches * 0.65), Inches(0.5),
              total_label.upper(), size=11, bold=True, color=muted,
              letter_spacing=0.16)
    _add_text(slide, left + Inches(width.inches * 0.55),
              total_y + Inches(0.1), Inches(width.inches * 0.45),
              Inches(1.0),
              total_value, size=44, bold=True, color=accent,
              font=FONT_NUMERIC, align="right")


def _slide_demographics(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             f"09 · Who Engaged With {ctx.brand}", on_dark=False)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              "Post-campaign audience profile.",
              size=40, bold=True, color=FG_DARK,
              spacing=0.95, font=FONT_DISPLAY)
    demos = (ctx.data.get("demographics") or {}).get("post") or {}
    cats = [
        ("AGE",     demos.get("age")     or []),
        ("GENDER",  demos.get("gender")  or []),
        ("INCOME",  demos.get("income")  or []),
    ]
    col_w = Inches(4.0)
    base_x = Inches(0.6)
    base_y = Inches(3.0)
    for i, (label, items) in enumerate(cats):
        x = base_x + (col_w + Inches(0.15)) * i
        _add_text(s, x, base_y, col_w, Inches(0.3),
                  label, size=11, bold=True, color=MUTED_LT,
                  letter_spacing=0.18)
        # top 5 buckets by pct
        rows = sorted(items, key=lambda r: float(r.get("percentage") or 0),
                      reverse=True)[:5]
        for j, r in enumerate(rows):
            y = base_y + Inches(0.5 + j * 0.65)
            _add_text(s, x, y, Inches(col_w.inches * 0.65), Inches(0.4),
                      str(r.get("value") or "—"), size=14, color=FG_DARK)
            _add_text(s, x + Inches(col_w.inches * 0.65), y,
                      Inches(col_w.inches * 0.35), Inches(0.4),
                      fmt_pct(r.get("percentage") or 0),
                      size=14, bold=True, color=FG_DARK,
                      font=FONT_NUMERIC, align="right")
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_LT)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


def _slide_top_touchpoints(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_DARK)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "10 · Most Engaged Brand Touchpoints", on_dark=True)
    _add_text(s, Inches(0.6), Inches(1.0), Inches(11), Inches(1.3),
              f"Where {ctx.brand} showed up.",
              size=40, bold=True, color=FG_LIGHT,
              spacing=0.95, font=FONT_DISPLAY)
    post = ctx.data.get("top_brand_properties") or []
    pre  = ctx.data.get("top_brand_properties_pre") or []
    pre_map = {(p.get("common_name") or "").lower():
               int(p.get("hits_projected") or 0) for p in pre}
    rows = sorted(post, key=lambda r: int(r.get("hits_projected") or 0),
                  reverse=True)[:7]
    # Header
    table_top = Inches(3.0)
    _add_text(s, Inches(0.6), table_top, Inches(5.0), Inches(0.3),
              "BRAND TOUCHPOINT", size=9, bold=True, color=MUTED_DK,
              letter_spacing=0.16)
    _add_text(s, Inches(5.8), table_top, Inches(2.0), Inches(0.3),
              "PRE REACH", size=9, bold=True, color=MUTED_DK,
              letter_spacing=0.16)
    _add_text(s, Inches(8.0), table_top, Inches(2.0), Inches(0.3),
              "POST REACH", size=9, bold=True, color=MUTED_DK,
              letter_spacing=0.16)
    _add_text(s, Inches(10.2), table_top, Inches(2.5), Inches(0.3),
              "LIFT", size=9, bold=True, color=MUTED_DK,
              letter_spacing=0.16, align="right")
    for i, r in enumerate(rows):
        y = table_top + Inches(0.45 + i * 0.45)
        name = str(r.get("common_name") or "—").title()
        post_h = int(r.get("hits_projected") or 0)
        pre_h = pre_map.get((r.get("common_name") or "").lower(), 0)
        _add_text(s, Inches(0.6), y, Inches(5.0), Inches(0.4),
                  name, size=14, bold=True, color=FG_LIGHT)
        _add_text(s, Inches(5.8), y, Inches(2.0), Inches(0.4),
                  fmt_num(pre_h) if pre_h else "—",
                  size=13, color=FG_LIGHT, font=FONT_NUMERIC)
        _add_text(s, Inches(8.0), y, Inches(2.0), Inches(0.4),
                  fmt_num(post_h),
                  size=13, color=FG_LIGHT, font=FONT_NUMERIC)
        if pre_h:
            lift = (post_h - pre_h) / pre_h * 100
            color = LIFT_UP if lift >= 0 else LIFT_DOWN
            _add_text(s, Inches(10.2), y, Inches(2.5), Inches(0.4),
                      fmt_signed_pct(lift),
                      size=13, bold=True, color=color, font=FONT_NUMERIC,
                      align="right")
        else:
            _add_text(s, Inches(10.2), y, Inches(2.5), Inches(0.4),
                      "NEW",
                      size=11, bold=True, color=ACCENT,
                      letter_spacing=0.18, align="right")
    _add_text(s, Inches(0.6), Inches(6.6), Inches(12), Inches(0.3),
              ctx.sources_line, size=8, italic=True, color=MUTED_DK)
    _page_footer(s, idx, total, ctx.project, on_dark=True)
    return s


def _slide_year_over_year(prs, ctx: DeckCtx, idx: int, total: int):
    s = _blank(prs)
    _add_bg(s, BG_CREAM)
    _eyebrow(s, Inches(0.6), Inches(0.6),
             "11 · Year Over Year", on_dark=False)
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
             "12 · The Final Insight", on_dark=False)
    l1, l2 = _final_insight_lines(ctx)
    _add_text(s, Inches(0.6), Inches(2.2), Inches(12.1), Inches(1.6),
              l1, size=64, bold=True, color=FG_DARK,
              font=FONT_DISPLAY, spacing=0.95)
    _add_text(s, Inches(0.6), Inches(3.6), Inches(12.1), Inches(1.6),
              l2, size=64, bold=True, color=FG_DARK,
              font=FONT_DISPLAY, spacing=0.95)
    # Closing data line
    closing = (
        f"{fmt_money(ctx.total_value)} in total brand value · "
        f"{fmt_num(ctx.post_users_proj)} U.S. consumers reached · "
        f"+{ctx.delta_rel:.1f}% engagement lift"
    )
    _add_text(s, Inches(0.6), Inches(5.7), Inches(12.1), Inches(0.5),
              closing, size=16, italic=True, color=FG_DARK)
    _page_footer(s, idx, total, ctx.project, on_dark=False)
    return s


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
         "Crosswalk BehaviorGraph - a zero-party-data panel of opted-in "
         "U.S. consumers. Every observation in this deck reflects real "
         "behavioral signal (visits, engagements, brand touchpoints) - "
         "no surveys, no self-report."),
        ("PROJECTION",
         "Panel-observed counts are projected to U.S. gen pop using a "
         "calibrated cohort weight. Numbers labeled 'projected' or "
         "'U.S. consumers' have been weight-extrapolated."),
        ("ATTRIBUTION WINDOW",
         f"Brand touchpoints are counted within {ctx.attribution_d} days "
         "of campaign end - long enough to capture delayed brand behavior, "
         "tight enough to keep causality clean."),
        ("BRAND LIFT (DiD)",
         "Brand Lift uses a difference-in-differences comparison against "
         "a size-matched, demographically balanced gen-pop control. We "
         "subtract the control cohort's natural drift to isolate the "
         "campaign's net contribution."),
    ]
    base_y = 2.6
    for i, (label, body) in enumerate(notes):
        y = Inches(base_y + i * 1.0)
        _add_text(s, Inches(0.6), y, Inches(3.0), Inches(0.3),
                  label, size=10, bold=True, color=ACCENT,
                  letter_spacing=0.18)
        _add_text(s, Inches(3.8), y, Inches(9.0), Inches(0.9),
                  body, size=11, color=FG_LIGHT, spacing=1.35)
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
               category: Optional[str] = None) -> bytes:
    """Build a full BPIQ analysis deck and return the .pptx bytes.

    Parameters
    ----------
    data       : the BPIQ result JSON (matches /api/brand-partnership-iq/
                 results/<key> 'data' field).
    image_url  : admin-uploaded hero photo from the metadata sidecar
                 (used on the cover slide). Optional - falls back to a
                 typographic plate if missing or unfetchable.
    category   : admin-managed category (case-insensitive). Used to
                 suppress conversion slides for 'automotive' runs.
    """
    ctx = _derive_context(data, image_url, category)

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

    out = io.BytesIO()
    prs.save(out)

    # Best-effort cleanup of any tmp files we materialized.
    for p in (ctx.cover_image, ctx.logo):
        try:
            if p and os.path.exists(p):
                os.unlink(p)
        except Exception:
            pass

    return out.getvalue()


def suggested_filename(data: dict, ext: str = "pptx") -> str:
    name = safe_filename(data.get("project_name") or "BPIQ_Deck")
    stamp = (data.get("created_at") or "")[:10] or "deck"
    return f"{name}_Analysis_{stamp}.{ext}"

