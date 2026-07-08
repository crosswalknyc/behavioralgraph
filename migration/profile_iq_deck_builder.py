"""Profile IQ deck builder — single-profile and multi-profile comparison decks.

Generates a designed PowerPoint (.pptx) that mirrors the LISA reference
deck's visual language (see /Users/jennamenking/Downloads/LISA_Audience_Profile
2026-06-23 v1.0.pptx):

  * 16:9 (13.33" × 7.50"), Arial throughout
  * Palette: dark charcoal (#0C1618), warm greys (#8A938F / #76868A / #59636A),
    off-white cream (#E9E8E1), accent lavender (#B7B3D8), pop lime (#C7F23E),
    magenta (#E782FF), and electric blue (#3358FF)
  * Editorial slide taxonomy: cover → overview → scale → demographics →
    "one insight" → category deep-dives → case studies → final insight
  * Sentence-case narrative headlines, huge stats with tiny labels, and a
    persistent CROSSWALK · PROFILE IQ footer

Two public entry points:
  * ``build_deck(profile_data, image_url=None, category=None)`` -> bytes
      Single-profile deck. Data payload matches what the Profile IQ dashboard
      already keeps in ``currentDashboardData`` (demographics + demographicsIndex
      + demographicsGenPop + behavioral + sample/projection counters).
  * ``build_combined_deck(profiles: list[dict])`` -> bytes
      Multi-profile comparative deck. Each profile is one entry of the same
      payload shape (name, image_url, category, demographics, behavioral, …).
      Adds side-by-side scale / demo / brand-overlap / category-winner slides.

The Flask endpoints in ``bg-webapp/app.py`` fetch the admin-managed profile
image via ``image_url`` (usually a ``/api/profile-image-file/...`` URL that we
resolve to bytes with ``urllib``) so the cover slide gets the right photo.
Any image fetch failure falls back to a colour block with a subject initial
so the deck always builds.

Dependencies (pinned in bg-webapp/requirements.txt):
  * python-pptx >= 1.0.0
  * Pillow >= 10.4.0
"""
from __future__ import annotations

import io
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Iterable, Optional

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt


# =============================================================================
#  Design tokens (LISA palette)
# =============================================================================

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5

C_DARK     = RGBColor(0x0C, 0x16, 0x18)   # near-black background
C_CREAM    = RGBColor(0xE9, 0xE8, 0xE1)   # primary light text
C_MUTED    = RGBColor(0x8A, 0x93, 0x8F)   # secondary text
C_MUTED2   = RGBColor(0x76, 0x86, 0x8A)   # tertiary text / footer
C_STROKE   = RGBColor(0x59, 0x63, 0x6A)   # thin rule lines
C_LAVENDER = RGBColor(0xB7, 0xB3, 0xD8)   # accent chip
C_LIME     = RGBColor(0xC7, 0xF2, 0x3E)   # pop accent
C_MAGENTA  = RGBColor(0xE7, 0x82, 0xFF)   # index-hot accent
C_BLUE     = RGBColor(0x33, 0x58, 0xFF)   # data accent

# Chart-friendly cycle used in demographic comparison bars
CYCLE_ACCENT = [C_LAVENDER, C_MAGENTA, C_LIME, C_BLUE, C_CREAM]

FONT_MAIN = "Arial"

US_POPULATION = 329_900_000


# =============================================================================
#  Public API
# =============================================================================


def build_deck(
    data: dict,
    *,
    image_url: Optional[str] = None,
    category: Optional[str] = None,
) -> bytes:
    """Build a single-profile Profile IQ deck and return the .pptx bytes."""
    profile = _normalize_payload(data, image_url=image_url, category=category)
    prs = _new_presentation()

    _slide_cover(prs, profile)
    _slide_overview(prs, profile)
    _slide_scale(prs, profile)
    _slide_demographics(prs, profile)
    _slide_one_insight(prs, profile)

    persona_slides = _pick_persona_category_slides(profile)
    for cat, items in persona_slides:
        _slide_persona_category(prs, profile, cat, items)

    _slide_case_study(prs, profile)
    _slide_media_and_social(prs, profile)
    _slide_final_insight(prs, profile)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def build_combined_deck(profiles: Iterable[dict]) -> bytes:
    """Comparative deck across multiple profiles + Gen Pop."""
    normalized = [_normalize_payload(p) for p in profiles if p]
    if not normalized:
        raise ValueError("build_combined_deck requires at least one profile")
    prs = _new_presentation()

    _slide_combined_cover(prs, normalized)
    _slide_combined_overview(prs, normalized)
    _slide_combined_scale(prs, normalized)
    _slide_combined_demographics(prs, normalized)
    _slide_combined_category_winners(prs, normalized)
    _slide_combined_brand_overlap(prs, normalized)
    _slide_combined_final_insight(prs, normalized)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def suggested_filename(data: dict, ext: str = "pptx") -> str:
    """Return a safe download filename for a single-profile deck."""
    name = (data.get("name") or data.get("brand") or "Profile").strip() or "Profile"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "Profile"
    return f"{safe}_Audience_Profile.{ext}"


def suggested_combined_filename(profiles: Iterable[dict], ext: str = "pptx") -> str:
    names = []
    for p in profiles or []:
        n = (p.get("name") or p.get("brand") or "").strip()
        if n:
            names.append(re.sub(r"[^A-Za-z0-9]+", "", n))
        if len(names) >= 4:
            break
    joined = "_vs_".join(names[:4]) or "Combined"
    return f"{joined}_Audience_Comparison.{ext}"


# =============================================================================
#  Payload normalization
# =============================================================================


def _normalize_payload(
    data: dict,
    *,
    image_url: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    """Return a payload with the exact keys the slide builders expect.

    Accepts both the ``currentDashboardData`` shape (camelCase, JS-style) and
    a snake_case variant, so the endpoint doesn't have to translate.
    """
    d = data or {}

    def _pick(*keys, default=None):
        for k in keys:
            if k in d and d[k] not in (None, "", {}, []):
                return d[k]
        return default

    demos      = _pick("demographics", "demos", default={}) or {}
    demos_idx  = _pick("demographicsIndex", "demographics_index", default={}) or {}
    demos_gp   = _pick("demographicsGenPop", "demographics_gen_pop", default={}) or {}
    demos_proj = _pick("demographicsProjection", "demographics_projection", default={}) or {}
    behavioral = _pick("behavioral", default={}) or {}
    locations  = _pick("locations", default=[]) or []
    interests  = _pick("interests", default={}) or {}

    payload = {
        "name":           (_pick("name", "brand", "project_name", default="Audience") or "Audience").strip(),
        "brand_category": (_pick("brand_category", "brandCategory", "category", default=(category or "")) or "").strip(),
        "sample_size":    int(_pick("sample_size", "sampleSize", default=0) or 0),
        "projected_us":   int(_pick("projected_us", "projectedUS", default=0) or 0),
        "date_range":     (_pick("date_range", "dateRange", default="") or "").strip(),
        "image_url":      image_url or _pick("image_url", "imageUrl", default="") or "",
        "s3_key":         (_pick("s3_key", "s3Key", default="") or "").strip(),
        "demographics":              _lowercase_demo_keys(demos),
        "demographics_index":        _lowercase_demo_keys(demos_idx),
        "demographics_gen_pop":      _lowercase_demo_keys(demos_gp),
        "demographics_projection":   _lowercase_demo_keys(demos_proj),
        "behavioral": {k: list(v or []) for k, v in behavioral.items()},
        "locations": list(locations),
        "interests": dict(interests),
    }

    # Sort behavioral items by index desc (over-indexers first) so slides pick
    # the "story" brands, not just the biggest-reach ones. Tie-break on pct.
    for cat, items in payload["behavioral"].items():
        items.sort(key=lambda it: (
            -_num(it.get("index", 0)),
            -_num(it.get("pct", 0)),
        ))
    return payload


def _lowercase_demo_keys(demos: dict) -> dict:
    """Normalize outer keys to lowercase (age/gender/ethnicity/...) so the
    slide builders don't need to worry about camelCase or upper variants."""
    out = {}
    for k, v in (demos or {}).items():
        key = str(k or "").strip().lower()
        if key.startswith("sexual"):
            key = "sexual_orientation"
        if key.startswith("parental"):
            key = "parental_status"
        out[key] = dict(v or {})
    return out


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# =============================================================================
#  Presentation shell / slide primitives
# =============================================================================


def _new_presentation() -> Presentation:
    prs = Presentation()
    prs.slide_width  = Inches(SLIDE_W_IN)
    prs.slide_height = Inches(SLIDE_H_IN)
    return prs


def _blank_slide(prs: Presentation, bg=C_DARK):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # completely blank layout
    _fill_bg(slide, bg)
    return slide


def _fill_bg(slide, color: RGBColor) -> None:
    box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0,
                                 Inches(SLIDE_W_IN), Inches(SLIDE_H_IN))
    box.line.fill.background()
    box.fill.solid()
    box.fill.fore_color.rgb = color


def _text(
    slide,
    left, top, width, height,
    text: str,
    *,
    size: float = 12,
    bold: bool = False,
    color: RGBColor = C_CREAM,
    align: int = PP_ALIGN.LEFT,
    anchor: int = MSO_ANCHOR.TOP,
    font: str = FONT_MAIN,
    line_spacing: Optional[float] = None,
    letter_spacing: Optional[float] = None,
):
    """Draw a single-run text box (unless text has \\n — then multi-run).

    Positions are inches. Returns the shape so callers can tweak further.
    """
    tb = slide.shapes.add_textbox(Inches(left), Inches(top),
                                  Inches(width), Inches(height))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    tb.line.fill.background()

    lines = str(text).split("\n") if text is not None else [""]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if line_spacing is not None:
            p.line_spacing = line_spacing
        r = p.add_run()
        r.text = line
        r.font.name = font
        r.font.size = Pt(size)
        r.font.bold = bool(bold)
        r.font.color.rgb = color
        # letter_spacing in python-pptx isn't a first-class attribute; skip
        # for now (LISA deck uses default tracking anyway).
    return tb


def _rect(slide, left, top, width, height, color: RGBColor, *,
          radius: Optional[float] = None, line=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    if radius is not None:
        # python-pptx uses adjustment 0.0-1.0 of half the shorter side
        try:
            shape.adjustments[0] = float(radius)
        except Exception:
            pass
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
    return shape


def _hairline(slide, left, top, width, color: RGBColor = C_STROKE):
    line = slide.shapes.add_connector(1, Inches(left), Inches(top),
                                      Inches(left + width), Inches(top))
    line.line.color.rgb = color
    line.line.width = Emu(9525)  # ~0.75pt
    return line


def _footer(slide, page_num: int, subject_label: str = ""):
    _text(slide, 0.62, 7.10, 6.0, 0.28,
          f"{subject_label.upper()} · AUDIENCE PROFILE" if subject_label else "AUDIENCE PROFILE",
          size=9, color=C_MUTED2, letter_spacing=0.05)
    _text(slide, 6.5, 7.10, 5.0, 0.28,
          "CROSSWALK  ·  PROFILE IQ",
          size=9, color=C_MUTED2, align=PP_ALIGN.CENTER, letter_spacing=0.05)
    _text(slide, 12.0, 7.10, 0.9, 0.28,
          str(page_num), size=9, color=C_MUTED2, align=PP_ALIGN.RIGHT)


def _section_eyebrow(slide, top: float, subject_label: str, section: str):
    """Small tracked eyebrow like 'THE LISA AUDIENCE  ·  IDENTITY'."""
    label = (subject_label or "Audience").strip().upper()
    _text(slide, 0.62, top, 12.0, 0.30,
          f"THE {label} AUDIENCE  ·  {section.upper()}",
          size=10, color=C_MUTED, letter_spacing=0.06)


def _title_block(slide, top: float, headline_a: str, headline_b: str = ""):
    _text(slide, 0.62, top, 11.5, 1.10, headline_a,
          size=44, bold=True, color=C_CREAM, line_spacing=1.05)
    if headline_b:
        _text(slide, 0.62, top + 0.85, 11.5, 1.10, headline_b,
              size=44, bold=True, color=C_CREAM, line_spacing=1.05)


# =============================================================================
#  Image fetch / cover art
# =============================================================================


def _fetch_image_bytes(image_url: str) -> Optional[bytes]:
    """Fetch a profile image URL. Returns None on any failure."""
    if not image_url:
        return None
    try:
        # Support /api/profile-image-file/... by resolving to the S3 host.
        # In production these URLs are same-origin; in the deck builder we
        # translate them to the direct S3 URL. If a full http(s) URL is
        # provided, use it as-is.
        if image_url.startswith("/api/profile-image-file/"):
            s3_key = image_url[len("/api/profile-image-file/"):]
            # Fall back to the public S3 URL (dashboard-inputs bucket is
            # readable from the app's IAM role but not necessarily anon; we
            # try anyway — most profile-images/ objects have been uploaded
            # by admin flow and are publicly readable).
            image_url = f"https://dashboard-inputs.s3.us-east-2.amazonaws.com/{urllib.parse.quote(s3_key)}"
        req = urllib.request.Request(image_url, headers={
            "User-Agent": "CrosswalkProfileIQDeck/1.0 (jenna@crosswalknyc.com)",
            "Accept": "image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        if len(data) < 200:
            return None
        return data
    except Exception:
        return None


def _placeholder_cover(slide, subject_name: str):
    """Ambient dark-gradient cover when no image is available."""
    # Big diagonal gradient effect using two overlapping colour blocks + a
    # subtle tinted right column so the composition doesn't feel flat.
    _rect(slide, 0, 0, SLIDE_W_IN, SLIDE_H_IN, C_DARK)
    _rect(slide, 0, 0, 6.5, SLIDE_H_IN, RGBColor(0x14, 0x22, 0x24))
    _rect(slide, SLIDE_W_IN - 3.2, 0, 3.2, SLIDE_H_IN, RGBColor(0x08, 0x0F, 0x11))
    # Subject initial as a decorative graphic
    initial = (subject_name.strip() or "A")[0].upper()
    _text(slide, SLIDE_W_IN - 3.0, 1.4, 2.4, 4.0, initial,
          size=260, bold=True, color=RGBColor(0x1B, 0x27, 0x2A),
          align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def _cover_image(slide, image_bytes: Optional[bytes], subject_name: str):
    """Full-bleed cover imagery. Falls back to a placeholder gradient."""
    if not image_bytes:
        _placeholder_cover(slide, subject_name)
        return
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(image_bytes))
        im = im.convert("RGB")
        # Fit-to-cover the left 8.4" of the slide (LISA cover proportion)
        target_w_in = 8.4
        target_h_in = SLIDE_H_IN
        target_w_px = int(target_w_in * 300)
        target_h_px = int(target_h_in * 300)
        src_w, src_h = im.size
        src_ratio = src_w / src_h
        tgt_ratio = target_w_px / target_h_px
        if src_ratio > tgt_ratio:
            new_w = int(src_h * tgt_ratio)
            offset = (src_w - new_w) // 2
            im = im.crop((offset, 0, offset + new_w, src_h))
        else:
            new_h = int(src_w / tgt_ratio)
            offset = (src_h - new_h) // 2
            im = im.crop((0, offset, src_w, offset + new_h))
        im = im.resize((target_w_px, target_h_px), Image.LANCZOS)
        # Darken the image so title text is readable
        from PIL import ImageEnhance
        im = ImageEnhance.Brightness(im).enhance(0.72)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        buf.seek(0)
        slide.shapes.add_picture(buf, 0, 0,
                                 width=Inches(target_w_in),
                                 height=Inches(target_h_in))
    except Exception:
        _placeholder_cover(slide, subject_name)
        return
    # Right-column accent strip (LISA has a dark solid on the right)
    _rect(slide, 8.4, 0, SLIDE_W_IN - 8.4, SLIDE_H_IN, C_DARK)
    # Bottom scrim so the tagline row has consistent contrast
    _rect(slide, 0, 4.9, SLIDE_W_IN, 2.6, C_DARK)


# =============================================================================
#  Slide builders  ·  SINGLE PROFILE
# =============================================================================


def _slide_cover(prs: Presentation, p: dict):
    slide = _blank_slide(prs, bg=C_DARK)
    img_bytes = _fetch_image_bytes(p.get("image_url") or "")
    _cover_image(slide, img_bytes, p["name"])

    year_label = _year_label(p.get("date_range")) or datetime.now().strftime("%Y")
    _text(slide, 0.62, 0.62, 10.0, 0.30,
          f"A BEHAVIORAL AUDIENCE PROFILE   ·   {year_label}",
          size=10, color=C_MUTED, letter_spacing=0.06)

    subject = p["name"]
    _text(slide, 0.58, 3.08, 8.60, 1.10, f"The {subject}",
          size=66, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.58, 3.95, 8.60, 1.10, "audience.",
          size=66, bold=True, color=C_CREAM, line_spacing=1.02)

    tagline = _generate_tagline(p)
    _text(slide, 0.62, 5.02, 5.0, 1.20, tagline,
          size=12, color=C_MUTED, line_spacing=1.35)

    _text(slide, 0.62, 7.00, 4.0, 0.28, "CONFIDENTIAL",
          size=9, color=C_MUTED2, letter_spacing=0.06)
    _text(slide, 8.11, 7.00, 4.60, 0.28, "CROSSWALK  PROFILE IQ",
          size=9, color=C_MUTED2, align=PP_ALIGN.RIGHT, letter_spacing=0.06)


def _slide_overview(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "OVERVIEW")
    _title_block(slide, 1.0, "What's inside.")

    body = (
        f"Over twelve months, Crosswalk observed how {p['name']}'s audience "
        f"behaves, what they watch, scroll, wear, and buy. Drawn from "
        f"{_fmt_int(p['sample_size']) if p['sample_size'] else '700,000+'} "
        f"zero-party panelists and projected across "
        f"{_fmt_int(p['projected_us']) if p['projected_us'] else '20M+'} "
        f"U.S. digital adults."
    )
    _text(slide, 0.62, 2.35, 8.0, 1.4, body, size=13, color=C_MUTED,
          line_spacing=1.45)

    # Four chapter cards (dynamic based on what data is present)
    chapters = _pick_overview_chapters(p)
    left_x, top_y = 0.62, 4.05
    card_w, card_h = 2.94, 2.65
    gap = 0.18
    for i, (num, title, subtitle) in enumerate(chapters):
        x = left_x + i * (card_w + gap)
        _rect(slide, x, top_y, card_w, card_h, RGBColor(0x14, 0x1F, 0x22))
        _text(slide, x + 0.32, top_y + 0.30, 2.4, 0.32, num,
              size=11, bold=True, color=C_LAVENDER, letter_spacing=0.05)
        _text(slide, x + 0.32, top_y + 0.72, 2.4, 1.0, title,
              size=17, bold=True, color=C_CREAM, line_spacing=1.15)
        _text(slide, x + 0.32, top_y + 1.65, 2.4, 0.95, subtitle,
              size=10.5, color=C_MUTED, line_spacing=1.35)

    _footer(slide, 2, p["name"])


def _slide_scale(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "SCALE")
    _title_block(slide, 1.0,
                 f"{p['name']} reaches a large,",
                 "high-engagement audience.")

    proj = p["projected_us"] or 0
    sample = p["sample_size"] or 0
    year_lbl = _year_label(p.get("date_range")) or "the trailing 12 months"

    # Three big stats across the middle
    stats = [
        (_fmt_int_compact(proj) or "N/A", "U.S. DIGITAL AUDIENCE",
         f"Projected from {_fmt_int(sample) or 'panel'} zero-party panelists observed {year_lbl}."),
        (_fmt_int_compact(_top_owned_reach(p)) or "—", "TOP TOUCHPOINT REACH",
         f"Adults reached by {p['name']}'s highest-engagement digital surface in-window."),
        (_fmt_int(len(_all_behavioral_items(p)) or 0),
         "BRAND SIGNALS OBSERVED",
         "Distinct branded touchpoints observed across social, retail, media & platform categories."),
    ]

    x0, y0 = 0.62, 3.6
    card_w = 4.0
    for i, (stat, lbl, desc) in enumerate(stats):
        x = x0 + i * (card_w + 0.10)
        _text(slide, x, y0, card_w, 1.0, stat,
              size=54, bold=True, color=C_CREAM, line_spacing=1.0)
        _hairline(slide, x, y0 + 1.05, 3.4, C_STROKE)
        _text(slide, x, y0 + 1.15, card_w, 0.30, lbl,
              size=10, bold=True, color=C_LAVENDER, letter_spacing=0.06)
        _text(slide, x, y0 + 1.50, card_w, 1.4, desc,
              size=10.5, color=C_MUTED, line_spacing=1.4)

    _footer(slide, 3, p["name"])


def _slide_demographics(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "DEMOGRAPHICS")
    _title_block(slide, 1.0, "Who this audience is,", "vs. Gen Pop.")

    _text(slide, 0.62, 2.35, 11.5, 0.6,
          "Every dimension compared against the national baseline. "
          "Larger bars = higher share of this audience. Index vs Gen Pop shown right.",
          size=11, color=C_MUTED, line_spacing=1.4)

    # Draw four demographic strips: AGE, GENDER, ETHNICITY, INCOME
    demos     = p["demographics"]
    demos_gp  = p["demographics_gen_pop"]
    demos_idx = p["demographics_index"]

    strips = [
        ("AGE",       demos.get("age", {}),       demos_gp.get("age", {}),       demos_idx.get("age", {})),
        ("GENDER",    demos.get("gender", {}),    demos_gp.get("gender", {}),    demos_idx.get("gender", {})),
        ("ETHNICITY", demos.get("ethnicity", {}), demos_gp.get("ethnicity", {}), demos_idx.get("ethnicity", {})),
        ("INCOME",    demos.get("income", {}),    demos_gp.get("income", {}),    demos_idx.get("income", {})),
    ]

    top_y = 3.05
    strip_h = 0.98
    strip_gap = 0.10
    for i, (label, pct_map, gp_map, idx_map) in enumerate(strips):
        y = top_y + i * (strip_h + strip_gap)
        _draw_demo_strip(slide, 0.62, y, 12.10, strip_h, label, pct_map, gp_map, idx_map)

    _footer(slide, 4, p["name"])


def _draw_demo_strip(slide, x, y, w, h, label, pct_map, gp_map, idx_map):
    """One row of grouped bars: Audience vs Gen Pop for each bucket."""
    _text(slide, x, y, 1.4, 0.3, label,
          size=10, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    # Buckets: keep only ones with a value in the audience
    buckets = list(pct_map.keys())
    if not buckets:
        _text(slide, x + 1.5, y + 0.20, w - 1.5, 0.4, "No data available",
              size=10, color=C_MUTED)
        return

    # Preserve source order but truncate if too many
    if len(buckets) > 7:
        buckets = buckets[:7]

    inner_x = x + 1.55
    inner_w = w - 1.55
    slot_w = inner_w / len(buckets)
    for i, b in enumerate(buckets):
        pct = _num(pct_map.get(b, 0))
        gp  = _num(gp_map.get(b, 0))
        idx = _num(idx_map.get(b, 0))
        cx = inner_x + i * slot_w
        # Bucket label
        _text(slide, cx, y + 0.72, slot_w - 0.05, 0.24,
              _short_bucket_label(b),
              size=8.5, color=C_MUTED, align=PP_ALIGN.CENTER)
        # Bar area: 0.55" tall band, subject bar on top, gp bar behind (lighter)
        max_val = max(pct, gp, 1) * 1.05
        bar_bottom = y + 0.68
        max_bar_h = 0.62
        sub_h = max_bar_h * (pct / max_val)
        gp_h  = max_bar_h * (gp  / max_val)
        # Gen Pop bar (grey background)
        bar_w = min(0.42, slot_w - 0.35)
        _rect(slide, cx + (slot_w - bar_w) / 2 - 0.10, bar_bottom - gp_h,
              bar_w, gp_h, C_STROKE)
        # Subject bar (accent)
        color = C_MAGENTA if idx >= 120 else (C_LIME if idx >= 100 else C_LAVENDER)
        _rect(slide, cx + (slot_w - bar_w) / 2 + 0.10, bar_bottom - sub_h,
              bar_w, sub_h, color)
        # Small % label above the taller bar
        _text(slide, cx, bar_bottom - max_bar_h - 0.22, slot_w - 0.05, 0.22,
              f"{pct:.0f}%",
              size=9, bold=True, color=C_CREAM, align=PP_ALIGN.CENTER)


def _slide_one_insight(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "ONE INSIGHT")

    # Compose a two-line headline from the biggest over-index and share
    top = _top_over_index_brand(p)
    if top:
        cat = (top.get("_cat") or "").title()
        brand = top.get("name", "")
        idx = int(_num(top.get("index", 0)))
        pct = _num(top.get("pct", 0))
        headline_a = f"{p['name']} doesn't just reach fans."
        headline_b = f"They define {cat.lower() or 'category'} taste."
        body = (
            f"{pct:.0f}% of this audience engages with {brand}, a {idx}-index "
            f"vs. the national average, and the strongest single-brand signal in the file. "
            f"The fandom and the buying behavior are the same audience."
        )
    else:
        headline_a = f"{p['name']} is a taste signal,"
        headline_b = "not just an audience."
        body = ("This audience over-indexes across multiple categories, "
                "meaning any brand aligned with them captures a habit, not a moment.")

    _text(slide, 0.62, 1.6, 12.0, 1.4, headline_a,
          size=44, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 2.5, 12.0, 1.4, headline_b,
          size=44, bold=True, color=C_LAVENDER, line_spacing=1.05)

    _hairline(slide, 0.62, 4.05, 12.10, C_STROKE)

    _text(slide, 0.62, 4.35, 9.5, 2.5, body,
          size=14, color=C_MUTED, line_spacing=1.55)

    _footer(slide, 5, p["name"])


def _slide_persona_category(prs: Presentation, p: dict, cat: str, items: list):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], cat)
    headline, sub = _persona_category_headline(cat)
    _title_block(slide, 1.0, headline, sub)

    _text(slide, 0.62, 2.35, 11.5, 0.55,
          f"Top {p['name']} audience brands in {_pretty_cat(cat)}, ranked by index vs Gen Pop.",
          size=11, color=C_MUTED, line_spacing=1.4)

    # Grid of up to 6 brand tiles
    top = items[:6]
    if not top:
        _text(slide, 0.62, 4.0, 11.5, 0.6,
              "No branded signals observed in this category for this audience.",
              size=12, color=C_MUTED)
        _footer(slide, 6, p["name"])
        return

    cols = 3
    tile_w, tile_h = 4.00, 1.85
    gap_x, gap_y = 0.10, 0.15
    x0, y0 = 0.62, 3.0
    for i, it in enumerate(top):
        r, c = divmod(i, cols)
        x = x0 + c * (tile_w + gap_x)
        y = y0 + r * (tile_h + gap_y)
        _rect(slide, x, y, tile_w, tile_h, RGBColor(0x14, 0x1F, 0x22))
        pct = _num(it.get("pct", 0))
        idx = int(_num(it.get("index", 0)))
        # Big percentage
        _text(slide, x + 0.32, y + 0.20, 2.0, 0.9, f"{pct:.1f}%",
              size=32, bold=True, color=C_CREAM, line_spacing=1.0)
        _text(slide, x + 0.32, y + 1.10, 2.0, 0.30, "reach",
              size=9, color=C_MUTED, letter_spacing=0.06)
        # Brand name + index
        brand_name = (it.get("name") or "").strip()
        _text(slide, x + 2.4, y + 0.30, tile_w - 2.55, 0.55, brand_name,
              size=13, bold=True, color=C_CREAM, line_spacing=1.15)
        idx_color = C_MAGENTA if idx >= 120 else (C_LIME if idx >= 100 else C_LAVENDER)
        _text(slide, x + 2.4, y + 0.95, tile_w - 2.55, 0.35, f"idx  {idx}",
              size=12, bold=True, color=idx_color, letter_spacing=0.05)
        _text(slide, x + 2.4, y + 1.30, tile_w - 2.55, 0.40,
              f"vs {_num(it.get('genPopPct', 0)):.1f}% Gen Pop",
              size=9, color=C_MUTED)

    _footer(slide, 6, p["name"])


def _slide_case_study(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "CASE STUDY")
    top = _top_over_index_brand(p)
    if not top:
        _title_block(slide, 1.0, "No standout brand signal.",
                     "The audience distributes evenly.")
        _footer(slide, 7, p["name"])
        return
    brand = top.get("name", "")
    _title_block(slide, 1.0, f"The {brand} signal.",
                 "It's not coincidence.")

    idx = int(_num(top.get("index", 0)))
    pct = _num(top.get("pct", 0))
    proj = _num(top.get("projection", 0))

    # Big index number
    _text(slide, 0.62, 2.9, 5.0, 1.6, f"{idx}",
          size=160, bold=True, color=C_LIME, line_spacing=1.0)
    _hairline(slide, 0.62, 4.65, 4.6)
    _text(slide, 0.62, 4.72, 5.0, 0.30,
          f"{brand.upper()}  PURCHASE INDEX", size=10.5, bold=True,
          color=C_LAVENDER, letter_spacing=0.05)
    _text(slide, 0.62, 5.05, 5.0, 0.30, "VS  GEN. POP.",
          size=10, color=C_MUTED, letter_spacing=0.06)

    # Right column: three stat rows
    y = 2.9
    for lbl, val in [
        (f"{brand.upper()}  REACH", f"{pct:.1f}%"),
        (f"{brand.upper()}  ADULTS", _fmt_int_compact(proj) or "—"),
        ("CATEGORY  RANK", "#1 over-indexer"),
    ]:
        _text(slide, 6.5, y, 6.0, 0.30, lbl,
              size=10, bold=True, color=C_MUTED, letter_spacing=0.05)
        _text(slide, 6.5, y + 0.30, 6.0, 0.9, val,
              size=44, bold=True, color=C_CREAM, line_spacing=1.0)
        y += 1.30

    _footer(slide, 7, p["name"])


def _slide_media_and_social(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "SOCIAL & PLATFORM")
    _title_block(slide, 1.0, "Where the audience", "spends its screen time.")

    social_items = _first_available(p, [
        "SOCIAL MEDIA", "SOCIAL_MEDIA", "PLATFORM", "APP/PLATFORM",
    ])
    media_items = _first_available(p, [
        "STREAMING/PLATFORM", "STREAMING_PLATFORM",
        "STREAMING VIDEO", "STREAMING_VIDEO", "MEDIA",
    ])

    # Left column: social
    _text(slide, 0.62, 2.4, 5.5, 0.30, "SOCIAL",
          size=11, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _draw_stat_list(slide, 0.62, 2.85, 5.5, social_items[:5])

    # Right column: streaming
    _text(slide, 7.0, 2.4, 5.5, 0.30, "STREAMING / MEDIA",
          size=11, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _draw_stat_list(slide, 7.0, 2.85, 5.5, media_items[:5])

    _footer(slide, 8, p["name"])


def _draw_stat_list(slide, x, y, w, items):
    if not items:
        _text(slide, x, y, w, 0.4, "No data in this category.",
              size=11, color=C_MUTED)
        return
    row_h = 0.72
    for i, it in enumerate(items):
        ry = y + i * row_h
        name = (it.get("name") or "").strip()
        pct = _num(it.get("pct", 0))
        idx = int(_num(it.get("index", 0)))
        _text(slide, x, ry, 3.4, 0.35, name,
              size=13, bold=True, color=C_CREAM)
        _text(slide, x, ry + 0.34, 3.4, 0.24,
              f"{pct:.1f}% reach", size=9, color=C_MUTED)
        idx_color = C_MAGENTA if idx >= 120 else (C_LIME if idx >= 100 else C_LAVENDER)
        _text(slide, x + 3.6, ry + 0.05, 1.6, 0.6, f"{idx}",
              size=28, bold=True, color=idx_color, align=PP_ALIGN.RIGHT,
              line_spacing=1.0)
        _text(slide, x + 3.6, ry + 0.55, 1.6, 0.20, "idx",
              size=9, color=C_MUTED, align=PP_ALIGN.RIGHT, letter_spacing=0.05)
        if i < len(items) - 1:
            _hairline(slide, x, ry + row_h - 0.05, w - 0.1, C_STROKE)


def _slide_final_insight(prs: Presentation, p: dict):
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.62, p["name"], "THE FINAL INSIGHT")

    top = _top_over_index_brand(p)
    n_over = sum(1 for it in _all_behavioral_items(p) if _num(it.get("index", 0)) >= 150)
    subject = p["name"]

    _text(slide, 0.62, 1.6, 12.0, 1.4, f"When {subject} shows up,",
          size=44, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 2.5, 12.0, 1.4, "the audience follows.",
          size=44, bold=True, color=C_LAVENDER, line_spacing=1.05)

    _hairline(slide, 0.62, 4.05, 12.10, C_STROKE)

    body_lines = [
        f"Across {n_over}+ signals, {subject}'s audience over-indexes 150+ vs. the U.S. baseline.",
        f"That is not a coincidence. It is a taste graph.",
    ]
    if top:
        body_lines.append(
            f"The strongest single signal is {top['name']} at {int(_num(top['index']))} index, "
            f"which any partner brand should read as durable, addressable audience overlap."
        )
    _text(slide, 0.62, 4.35, 11.5, 2.6, "\n\n".join(body_lines),
          size=14, color=C_MUTED, line_spacing=1.55)

    _footer(slide, 9, p["name"])


# =============================================================================
#  Slide builders  ·  COMBINED / COMPARATIVE
# =============================================================================


def _slide_combined_cover(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs, bg=C_DARK)
    _placeholder_cover(slide, "&")

    year_label = datetime.now().strftime("%Y")
    _text(slide, 0.62, 0.62, 10.0, 0.30,
          f"A BEHAVIORAL AUDIENCE COMPARISON   ·   {year_label}",
          size=10, color=C_MUTED, letter_spacing=0.06)

    if len(profiles) == 2:
        title_a = profiles[0]["name"]
        title_b = f"vs. {profiles[1]['name']}."
    else:
        title_a = f"{len(profiles)} audiences."
        title_b = "One dataset."
    _text(slide, 0.58, 3.08, 8.60, 1.10, title_a,
          size=66, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.58, 3.95, 8.60, 1.10, title_b,
          size=66, bold=True, color=C_CREAM, line_spacing=1.02)

    subj_list = ", ".join(pr["name"] for pr in profiles[:4])
    tag = (
        f"Where do their fans overlap, and where do they diverge? "
        f"Side-by-side comparison of {subj_list} on scale, demographics, and category signal."
    )
    _text(slide, 0.62, 5.02, 5.0, 1.30, tag,
          size=12, color=C_MUTED, line_spacing=1.35)

    _text(slide, 0.62, 7.00, 4.0, 0.28, "CONFIDENTIAL",
          size=9, color=C_MUTED2, letter_spacing=0.06)
    _text(slide, 8.11, 7.00, 4.60, 0.28, "CROSSWALK  PROFILE IQ",
          size=9, color=C_MUTED2, align=PP_ALIGN.RIGHT, letter_spacing=0.06)


def _slide_combined_overview(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs)
    _text(slide, 0.62, 0.62, 12.0, 0.30,
          "AUDIENCE COMPARISON  ·  OVERVIEW",
          size=10, color=C_MUTED, letter_spacing=0.06)
    _title_block(slide, 1.0, "What's inside.")

    body = (
        f"We stacked {len(profiles)} audiences against each other and the U.S. baseline. "
        f"Every slide answers one question: who's biggest, who over-indexes with whom, and "
        f"which brands live in every fandom."
    )
    _text(slide, 0.62, 2.35, 8.0, 1.4, body, size=13, color=C_MUTED,
          line_spacing=1.45)

    chapters = [
        ("01", "Scale", "How large is each digital audience, side by side?"),
        ("02", "Demographics", "Age, gender, ethnicity, income (who's in each room)."),
        ("03", "Category winners", "For each behavioral category, which audience over-indexes most."),
        ("04", "Shared brands", "Which brands live in every audience: the connective tissue."),
    ]
    left_x, top_y = 0.62, 4.05
    card_w, card_h = 2.94, 2.65
    gap = 0.18
    for i, (num, title, subtitle) in enumerate(chapters):
        x = left_x + i * (card_w + gap)
        _rect(slide, x, top_y, card_w, card_h, RGBColor(0x14, 0x1F, 0x22))
        _text(slide, x + 0.32, top_y + 0.30, 2.4, 0.32, num,
              size=11, bold=True, color=C_LAVENDER, letter_spacing=0.05)
        _text(slide, x + 0.32, top_y + 0.72, 2.4, 1.0, title,
              size=17, bold=True, color=C_CREAM, line_spacing=1.15)
        _text(slide, x + 0.32, top_y + 1.65, 2.4, 0.95, subtitle,
              size=10.5, color=C_MUTED, line_spacing=1.35)

    _footer(slide, 2, "Comparison")


def _slide_combined_scale(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs)
    _text(slide, 0.62, 0.62, 12.0, 0.30,
          "AUDIENCE COMPARISON  ·  SCALE",
          size=10, color=C_MUTED, letter_spacing=0.06)
    _title_block(slide, 1.0, "How large is each audience.",
                 "Projected to U.S. digital adults.")

    _text(slide, 0.62, 2.35, 11.5, 0.6,
          "Every projection is the audience size that the Crosswalk panel scales to. "
          "Bars are proportional; index vs Gen Pop shown as a chip.",
          size=11, color=C_MUTED, line_spacing=1.4)

    # Horizontal bar chart
    max_proj = max((p["projected_us"] for p in profiles), default=0) or 1
    row_h = min(0.85, 3.4 / max(len(profiles), 1))
    y0 = 3.15
    label_w = 2.8
    bar_start = 0.62 + label_w
    bar_max_w = SLIDE_W_IN - bar_start - 2.6

    for i, p in enumerate(profiles):
        y = y0 + i * (row_h + 0.18)
        _text(slide, 0.62, y, label_w, row_h - 0.10,
              p["name"], size=15, bold=True, color=C_CREAM,
              anchor=MSO_ANCHOR.MIDDLE)
        proj = p["projected_us"] or 0
        pct_of_max = proj / max_proj
        color = CYCLE_ACCENT[i % len(CYCLE_ACCENT)]
        _rect(slide, bar_start, y + 0.05, max(bar_max_w * pct_of_max, 0.05),
              row_h - 0.20, color)
        _text(slide, bar_start + bar_max_w + 0.15, y + 0.05, 2.4, row_h - 0.20,
              _fmt_int_compact(proj) or "—",
              size=18, bold=True, color=C_CREAM,
              anchor=MSO_ANCHOR.MIDDLE)

    _footer(slide, 3, "Comparison")


def _slide_combined_demographics(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs)
    _text(slide, 0.62, 0.62, 12.0, 0.30,
          "AUDIENCE COMPARISON  ·  DEMOGRAPHICS",
          size=10, color=C_MUTED, letter_spacing=0.06)
    _title_block(slide, 1.0, "Who's in each room.")

    _text(slide, 0.62, 2.35, 11.5, 0.6,
          "Age and gender skew for every audience, side by side. Percentages are share of that audience.",
          size=11, color=C_MUTED, line_spacing=1.4)

    # Two panels: AGE (grouped bars per audience) and GENDER
    _combined_demo_panel(slide, 0.62, 3.05, 6.05, 3.9,
                         "AGE", profiles,
                         lambda p: p["demographics"].get("age", {}))
    _combined_demo_panel(slide, 6.72, 3.05, 6.05, 3.9,
                         "GENDER", profiles,
                         lambda p: p["demographics"].get("gender", {}))

    _footer(slide, 4, "Comparison")


def _combined_demo_panel(slide, x, y, w, h, label, profiles, getter):
    _text(slide, x, y, w, 0.30, label,
          size=10, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    # Get union of buckets across profiles (preserve order from first)
    buckets = []
    seen = set()
    for p in profiles:
        for k in getter(p).keys():
            if k not in seen:
                seen.add(k)
                buckets.append(k)
    if not buckets:
        _text(slide, x, y + 0.4, w, 0.4, "No data.",
              size=10, color=C_MUTED)
        return
    if len(buckets) > 6:
        buckets = buckets[:6]

    # Grouped bars: one column per bucket, N side-by-side bars per column
    panel_top = y + 0.6
    panel_h = h - 0.9
    slot_w = w / len(buckets)
    n_prof = len(profiles)
    bar_group_w = slot_w * 0.68
    bar_w = bar_group_w / max(n_prof, 1)

    for bi, b in enumerate(buckets):
        cx = x + bi * slot_w
        # Bucket label
        _text(slide, cx, panel_top + panel_h + 0.02, slot_w - 0.05, 0.24,
              _short_bucket_label(b), size=8.5, color=C_MUTED,
              align=PP_ALIGN.CENTER)
        # Bars
        max_val = max(_num(getter(p).get(b, 0)) for p in profiles)
        max_val = max(max_val * 1.1, 1)
        for pi, p in enumerate(profiles):
            val = _num(getter(p).get(b, 0))
            bar_h = panel_h * (val / max_val)
            bx = cx + (slot_w - bar_group_w) / 2 + pi * bar_w
            color = CYCLE_ACCENT[pi % len(CYCLE_ACCENT)]
            _rect(slide, bx, panel_top + (panel_h - bar_h),
                  bar_w * 0.85, bar_h, color)

    # Mini legend under panel
    _draw_legend(slide, x, y + h - 0.30, profiles)


def _draw_legend(slide, x, y, profiles):
    chip_x = x
    for i, p in enumerate(profiles):
        color = CYCLE_ACCENT[i % len(CYCLE_ACCENT)]
        _rect(slide, chip_x, y + 0.06, 0.16, 0.14, color)
        # Estimate width from name length to avoid overlap
        est_w = 0.10 + 0.06 * len(p["name"])
        _text(slide, chip_x + 0.22, y, est_w + 0.4, 0.26,
              p["name"], size=9, color=C_CREAM)
        chip_x += 0.32 + est_w


def _slide_combined_category_winners(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs)
    _text(slide, 0.62, 0.62, 12.0, 0.30,
          "AUDIENCE COMPARISON  ·  CATEGORY WINNERS",
          size=10, color=C_MUTED, letter_spacing=0.06)
    _title_block(slide, 1.0, "Who owns each category.")

    _text(slide, 0.62, 2.35, 11.5, 0.6,
          "For each behavioral category, we identify which audience over-indexes hardest and what "
          "their #1 brand is. A dash means the audience has no signal in that category.",
          size=11, color=C_MUTED, line_spacing=1.45)

    # Get union of categories across profiles
    cats = _combined_category_ranking(profiles)[:8]
    if not cats:
        _text(slide, 0.62, 4.0, 11.5, 0.5,
              "No overlapping behavioral categories to compare.",
              size=12, color=C_MUTED)
        _footer(slide, 5, "Comparison")
        return

    # Table header row
    y = 3.15
    row_h = 0.42
    col_x = [0.62, 3.6, 6.8, 9.4]
    col_w = [2.9, 3.1, 2.5, 3.5]

    _text(slide, col_x[0], y, col_w[0], 0.28, "CATEGORY",
          size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _text(slide, col_x[1], y, col_w[1], 0.28, "TOP-INDEX AUDIENCE",
          size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _text(slide, col_x[2], y, col_w[2], 0.28, "INDEX",
          size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _text(slide, col_x[3], y, col_w[3], 0.28, "THEIR TOP BRAND",
          size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _hairline(slide, 0.62, y + 0.30, 12.10, C_STROKE)

    y += 0.42
    for cat_row in cats:
        cat, winner_name, winner_idx, brand = cat_row
        _text(slide, col_x[0], y, col_w[0], row_h - 0.10,
              cat.title(), size=12, color=C_CREAM,
              anchor=MSO_ANCHOR.MIDDLE)
        _text(slide, col_x[1], y, col_w[1], row_h - 0.10,
              winner_name or "—", size=12, bold=True, color=C_CREAM,
              anchor=MSO_ANCHOR.MIDDLE)
        idx_color = C_MAGENTA if winner_idx >= 150 else C_LIME
        _text(slide, col_x[2], y, col_w[2], row_h - 0.10,
              f"{int(winner_idx)}" if winner_idx else "—",
              size=14, bold=True, color=idx_color,
              anchor=MSO_ANCHOR.MIDDLE)
        _text(slide, col_x[3], y, col_w[3], row_h - 0.10,
              brand or "—", size=12, color=C_CREAM,
              anchor=MSO_ANCHOR.MIDDLE)
        _hairline(slide, 0.62, y + row_h - 0.05, 12.10, C_STROKE)
        y += row_h

    _footer(slide, 5, "Comparison")


def _slide_combined_brand_overlap(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs)
    _text(slide, 0.62, 0.62, 12.0, 0.30,
          "AUDIENCE COMPARISON  ·  SHARED BRANDS",
          size=10, color=C_MUTED, letter_spacing=0.06)
    _title_block(slide, 1.0, "The connective tissue.",
                 "Brands that live in every fandom.")

    _text(slide, 0.62, 2.35, 11.5, 0.6,
          "Brands where every profile in this deck posts a meaningful over-index (≥ 110) vs. Gen Pop. "
          "The higher the row, the tighter the shared behavior.",
          size=11, color=C_MUTED, line_spacing=1.4)

    overlap = _brand_overlap_rows(profiles, min_index=110, top_n=8)
    if not overlap:
        _text(slide, 0.62, 4.0, 11.5, 0.6,
              "No brands over-index across all profiles at the 110+ threshold. "
              "These audiences don't share a single common brand. That is a signal in itself.",
              size=13, color=C_MUTED, line_spacing=1.45)
        _footer(slide, 6, "Comparison")
        return

    # Header
    y = 3.15
    row_h = 0.42
    per_profile_w = 1.3
    n = len(profiles)
    start_x = SLIDE_W_IN - 0.62 - per_profile_w * n
    _text(slide, 0.62, y, 4.0, 0.28, "BRAND",
          size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    _text(slide, 4.7, y, 2.5, 0.28, "CATEGORY",
          size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
    for i, p in enumerate(profiles):
        _text(slide, start_x + i * per_profile_w, y, per_profile_w - 0.1, 0.28,
              p["name"], size=9, bold=True, color=C_LAVENDER,
              align=PP_ALIGN.RIGHT, letter_spacing=0.05)
    _hairline(slide, 0.62, y + 0.30, 12.10, C_STROKE)

    y += 0.42
    for row in overlap:
        _text(slide, 0.62, y, 4.0, row_h - 0.10, row["brand"],
              size=12, bold=True, color=C_CREAM, anchor=MSO_ANCHOR.MIDDLE)
        _text(slide, 4.7, y, 2.5, row_h - 0.10, row["category"].title(),
              size=11, color=C_MUTED, anchor=MSO_ANCHOR.MIDDLE)
        for i, p in enumerate(profiles):
            idx = row["indices"].get(p["name"], 0)
            color = C_MAGENTA if idx >= 150 else (C_LIME if idx >= 120 else C_CREAM)
            _text(slide, start_x + i * per_profile_w, y,
                  per_profile_w - 0.1, row_h - 0.10,
                  f"{int(idx)}" if idx else "—",
                  size=14, bold=True, color=color, align=PP_ALIGN.RIGHT,
                  anchor=MSO_ANCHOR.MIDDLE)
        _hairline(slide, 0.62, y + row_h - 0.05, 12.10, C_STROKE)
        y += row_h

    _footer(slide, 6, "Comparison")


def _slide_combined_final_insight(prs: Presentation, profiles: list[dict]):
    slide = _blank_slide(prs)
    _text(slide, 0.62, 0.62, 12.0, 0.30,
          "AUDIENCE COMPARISON  ·  THE FINAL INSIGHT",
          size=10, color=C_MUTED, letter_spacing=0.06)

    largest = max(profiles, key=lambda p: p["projected_us"] or 0)
    tightest = max(profiles, key=lambda p: _peak_index(p))

    _text(slide, 0.62, 1.6, 12.0, 1.4, "Scale sells the room.",
          size=44, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 2.5, 12.0, 1.4, "Index sells the message.",
          size=44, bold=True, color=C_LAVENDER, line_spacing=1.05)

    _hairline(slide, 0.62, 4.05, 12.10, C_STROKE)

    body_lines = [
        f"{largest['name']} reaches the largest audience: "
        f"{_fmt_int_compact(largest['projected_us'])} U.S. digital adults.",
        f"{tightest['name']} owns the tightest signal: peak index of "
        f"{int(_peak_index(tightest))} vs. Gen Pop.",
        "Match the buy to the goal: reach-first for one, resonance-first for the other.",
    ]
    _text(slide, 0.62, 4.35, 11.5, 2.6, "\n\n".join(body_lines),
          size=14, color=C_MUTED, line_spacing=1.55)

    _footer(slide, 7, "Comparison")


# =============================================================================
#  Data helpers
# =============================================================================


def _fmt_int(n) -> str:
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{n:,}"


def _fmt_int_compact(n) -> str:
    """Human-readable: 23,100,000 -> '23.1M'."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if not n or n <= 0:
        return ""
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return f"{int(n)}"


def _short_bucket_label(b: str) -> str:
    """Shorten long demographic bucket labels for tight chart columns."""
    s = str(b or "").strip()
    if not s:
        return ""
    if len(s) <= 12:
        return s
    # Some canonical shortenings
    repl = {
        "Bachelor's Degree": "Bachelor's",
        "Master's Degree":   "Master's",
        "Doctoral Degree":   "PhD",
        "Some College":      "Some Coll.",
        "High School":       "HS",
        "Prefer Not to Say": "N/A",
        "Not Hispanic or Latino": "Non-Hisp.",
        "Hispanic or Latino":     "Hispanic",
        "Black or African American": "Black",
        "American Indian or Alaska Native": "Am. Indian",
        "Native Hawaiian or Other Pacific Islander": "NHPI",
        "Two or More Races": "Mixed",
        "White":             "White",
        "Asian":             "Asian",
    }
    if s in repl:
        return repl[s]
    return s[:14] + "…"


def _year_label(date_range: str) -> str:
    if not date_range:
        return ""
    m = re.search(r"(20\d{2})", date_range)
    return m.group(1) if m else ""


def _all_behavioral_items(p: dict) -> list[dict]:
    out = []
    for cat, items in (p.get("behavioral") or {}).items():
        for it in items:
            it2 = dict(it)
            it2["_cat"] = cat
            out.append(it2)
    return out


def _top_over_index_brand(p: dict) -> Optional[dict]:
    items = [it for it in _all_behavioral_items(p)
             if _num(it.get("index", 0)) > 0 and _num(it.get("pct", 0)) >= 1.0]
    if not items:
        return None
    items.sort(key=lambda it: (-_num(it.get("index", 0)),
                               -_num(it.get("pct", 0))))
    return items[0]


def _top_owned_reach(p: dict) -> float:
    items = _all_behavioral_items(p)
    if not items:
        return 0
    return max((_num(it.get("projection", 0)) for it in items), default=0)


def _first_available(p: dict, cat_names: list[str]) -> list[dict]:
    """Return the first matching category's item list."""
    beh = p.get("behavioral") or {}
    # Case-insensitive match
    lut = {k.upper(): v for k, v in beh.items()}
    for name in cat_names:
        v = lut.get(name.upper())
        if v:
            return list(v)
    return []


def _pick_overview_chapters(p: dict) -> list[tuple[str, str, str]]:
    """Choose the four chapter cards to render on the OVERVIEW slide, based
    on which behavioral categories actually have data."""
    beh = p.get("behavioral") or {}
    chapters = [
        ("01", "Who she reaches",
         f"Demographic composition of {p['name']}'s audience vs. Gen Pop."),
        ("02", "What she signals",
         "The category over-indexers, where this audience concentrates."),
    ]
    # Add category-specific chapters when relevant categories have data
    has_beauty  = _first_available(p, ["COSMETICS", "BEAUTY", "PERSONAL CARE", "PERSONAL_CARE"])
    has_lux     = _first_available(p, ["LUXURY FASHION", "LUXURY_FASHION",
                                       "APPAREL/FOOTWEAR", "APPAREL_FOOTWEAR", "MPB"])
    has_media   = _first_available(p, ["STREAMING/PLATFORM", "STREAMING_PLATFORM",
                                       "STREAMING VIDEO", "MEDIA", "SOCIAL MEDIA"])
    if has_beauty:
        chapters.append(("03", "Beauty & prestige",
                         "Cosmetics, skincare, fragrance: the audience's shopping habits."))
    if has_lux and len(chapters) < 4:
        chapters.append(("03" if len(chapters) == 2 else "04",
                         "Fashion & apparel",
                         "Luxury and everyday apparel where this audience actually spends."))
    if has_media and len(chapters) < 4:
        chapters.append((f"{len(chapters)+1:02d}",
                         "Where she watches",
                         "Streaming, social, and platform behavior."))
    # Pad to 4 if we still don't have enough
    filler = [
        ("Reach quality", "Beyond raw scale: engagement intensity by category."),
        ("Activation ready", "The strongest, most-actionable partnership signals."),
    ]
    while len(chapters) < 4 and filler:
        n = f"{len(chapters)+1:02d}"
        t, s = filler.pop(0)
        chapters.append((n, t, s))
    return chapters[:4]


PERSONA_CATEGORY_HEADLINES: dict[str, tuple[str, str]] = {
    "COSMETICS": ("Beauty retail", "dominates."),
    "PERSONAL CARE": ("A personal-care audience,", "already spending."),
    "APPAREL/FOOTWEAR": ("A clear lens on", "how she dresses."),
    "MPB": ("Where she actually shops.", "Most-purchased brands."),
    "MOST_PURCHASED_BRANDS": ("Where she actually shops.", "Most-purchased brands."),
    "LUXURY FASHION": ("A clear lens on luxury.", ""),
    "QSR": ("How she eats,", "when she's out."),
    "RETAIL": ("Where she buys,", "beyond luxury."),
    "TRAVEL": ("How she travels.", "And where."),
    "BANKING": ("Where the money lives.", ""),
    "AUTOMOBILE": ("What she drives.", ""),
    "TALENT/ACTOR": ("The talent overlap.", ""),
    "SOCIAL MEDIA": ("Social platform-native.", "Content curious."),
    "STREAMING/PLATFORM": ("How she watches,", "when she chooses."),
    "STREAMING VIDEO": ("How she watches,", "when she chooses."),
}


def _persona_category_headline(cat: str) -> tuple[str, str]:
    return PERSONA_CATEGORY_HEADLINES.get(cat.upper(), (f"{_pretty_cat(cat)}.", "The audience signal."))


# Category label prettifier so titles never render as "Qsr" or "Mpb".
_CAT_LABEL_OVERRIDES = {
    "QSR": "QSR", "MPB": "Most-Purchased Brands", "AI": "AI",
    "APP/PLATFORM": "App / Platform", "APPAREL/FOOTWEAR": "Apparel & Footwear",
    "STREAMING/PLATFORM": "Streaming & Platform",
    "STREAMING VIDEO": "Streaming Video", "STREAMING MUSIC": "Streaming Music",
    "SEARCH ENGINE/AI": "Search Engine & AI",
    "SOCIAL MEDIA": "Social Media", "MOST_PURCHASED_BRANDS": "Most-Purchased Brands",
    "PERSONAL CARE": "Personal Care", "LUXURY FASHION": "Luxury Fashion",
    "VMVPD/FAST": "vMVPD / FAST", "VIRTUAL MVPD/FAST": "Virtual MVPD / FAST",
}


def _pretty_cat(cat: str) -> str:
    """Return a title-cased version of a category name, respecting overrides."""
    if not cat:
        return ""
    key = cat.strip().upper()
    if key in _CAT_LABEL_OVERRIDES:
        return _CAT_LABEL_OVERRIDES[key]
    # Otherwise title-case, but keep known acronyms upper
    words = re.split(r"[\s_/]+", cat)
    out = []
    for w in words:
        if not w:
            continue
        if w.upper() in {"QSR", "MPB", "AI", "TV", "MLB", "NBA", "NFL",
                         "MLS", "WNBA", "MILB", "USPS", "CFB", "EPL"}:
            out.append(w.upper())
        else:
            out.append(w.title())
    return " ".join(out)


# Categories that make good persona deep-dive slides (skip social/streaming
# which get their own slide, skip huge structural buckets like AGE etc.)
_PERSONA_SLIDE_ORDER = [
    "COSMETICS", "PERSONAL CARE", "PERSONAL_CARE",
    "APPAREL/FOOTWEAR", "APPAREL_FOOTWEAR", "MPB",
    "LUXURY FASHION", "LUXURY_FASHION",
    "RETAIL", "QSR", "TRAVEL", "BANKING", "AUTOMOBILE",
]

_SKIP_PERSONA_CATS = {"SOCIAL MEDIA", "SOCIAL_MEDIA", "STREAMING/PLATFORM",
                      "STREAMING_PLATFORM", "STREAMING VIDEO",
                      "STREAMING_VIDEO", "MEDIA"}


def _pick_persona_category_slides(p: dict, max_slides: int = 2) -> list[tuple[str, list]]:
    """Pick up to N behavioral categories to spotlight as their own slides."""
    beh = p.get("behavioral") or {}
    picked: list[tuple[str, list]] = []
    seen = set()
    # Prefer canonical persona order first
    for pref in _PERSONA_SLIDE_ORDER:
        if len(picked) >= max_slides:
            break
        # case-insensitive match
        for cat, items in beh.items():
            if cat in seen:
                continue
            if cat.upper() == pref.upper() and items:
                picked.append((cat, items))
                seen.add(cat)
                break
    # Fallback: fill remaining slots with any high-signal category
    if len(picked) < max_slides:
        candidates = []
        for cat, items in beh.items():
            if cat in seen or cat.upper() in _SKIP_PERSONA_CATS:
                continue
            if not items:
                continue
            peak = max((_num(it.get("index", 0)) for it in items), default=0)
            if peak >= 120:
                candidates.append((peak, cat, items))
        candidates.sort(key=lambda t: -t[0])
        for _, cat, items in candidates[: max_slides - len(picked)]:
            picked.append((cat, items))
    return picked


def _generate_tagline(p: dict) -> str:
    """One-line tagline for the cover, driven by strongest audience signal."""
    top = _top_over_index_brand(p)
    if top:
        cat = (top.get("_cat") or "").lower()
        return (
            f"An audience that over-indexes on {top['name']} at {int(_num(top['index']))} "
            f"vs. the U.S. baseline. The strongest {cat or 'behavioral'} signal in the file, "
            f"and a durable lens on how they engage."
        )
    proj = _fmt_int_compact(p["projected_us"] or 0)
    return (
        f"A behavioral profile of the {p['name']} audience. "
        f"{proj or 'A'} U.S. digital adults, drawn from Crosswalk's zero-party panel."
    )


def _combined_category_ranking(profiles: list[dict]) -> list[tuple[str, str, float, str]]:
    """Return rows of (category, winner_audience, winner_peak_index, winner_top_brand)."""
    cats = set()
    for p in profiles:
        for c in (p.get("behavioral") or {}).keys():
            if c.upper() not in _SKIP_PERSONA_CATS:
                cats.add(c)
    rows = []
    for c in cats:
        best = None
        for p in profiles:
            items = (p.get("behavioral") or {}).get(c) or []
            if not items:
                # Try case-variant match
                lut = {k.upper(): v for k, v in p["behavioral"].items()}
                items = lut.get(c.upper()) or []
            if not items:
                continue
            peak = max(items, key=lambda it: _num(it.get("index", 0)))
            peak_idx = _num(peak.get("index", 0))
            if not best or peak_idx > best[2]:
                best = (c, p["name"], peak_idx, peak.get("name", ""))
        if best and best[2] >= 100:
            rows.append(best)
    rows.sort(key=lambda r: -r[2])
    return rows


def _brand_overlap_rows(profiles: list[dict], *,
                        min_index: float = 110, top_n: int = 8) -> list[dict]:
    """Return brands that every profile posts an index >= min_index for."""
    if not profiles:
        return []
    # Build per-profile { normalized_brand -> (raw_name, category, index) }
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    per: list[dict] = []
    for p in profiles:
        d = {}
        for cat, items in (p.get("behavioral") or {}).items():
            if cat.upper() in _SKIP_PERSONA_CATS:
                continue
            for it in items:
                nm = norm(it.get("name", ""))
                if not nm:
                    continue
                idx = _num(it.get("index", 0))
                if idx < min_index:
                    continue
                cur = d.get(nm)
                if not cur or idx > cur[2]:
                    d[nm] = (it.get("name", ""), cat, idx)
        per.append(d)
    if not per:
        return []
    shared = set(per[0].keys())
    for m in per[1:]:
        shared &= set(m.keys())
    rows = []
    for k in shared:
        rec = {
            "brand":    per[0][k][0],
            "category": per[0][k][1],
            "indices":  {p["name"]: per[i][k][2] for i, p in enumerate(profiles) if k in per[i]},
        }
        rec["_avg_idx"] = sum(rec["indices"].values()) / max(len(rec["indices"]), 1)
        rows.append(rec)
    rows.sort(key=lambda r: -r["_avg_idx"])
    return rows[:top_n]


def _peak_index(p: dict) -> float:
    peak = 0.0
    for it in _all_behavioral_items(p):
        v = _num(it.get("index", 0))
        if v > peak:
            peak = v
    return peak


# =============================================================================
#  Optional: entry point for CLI testing
# =============================================================================


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: python3 profile_iq_deck_builder.py <profile_json> [more_profiles...]")
        print("       last arg can be --combined to build a combined deck")
        sys.exit(2)
    combined = False
    args = list(sys.argv[1:])
    if args and args[-1] == "--combined":
        combined = True
        args.pop()
    profiles = []
    for a in args:
        with open(a) as fh:
            profiles.append(json.load(fh))
    if combined and len(profiles) > 1:
        data = build_combined_deck(profiles)
        fname = suggested_combined_filename(profiles)
    else:
        data = build_deck(profiles[0])
        fname = suggested_filename(profiles[0])
    with open(fname, "wb") as fh:
        fh.write(data)
    print(f"Wrote {fname} ({len(data):,} bytes)")
