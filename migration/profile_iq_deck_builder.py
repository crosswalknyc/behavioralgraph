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
    """Build a single-profile Profile IQ deck and return the .pptx bytes.

    Target slide count is 15-17, matching LISA's editorial density. The
    profile image (fetched once here) is passed into every slide that
    supports imagery so we don't hit S3 seven times per build."""
    profile = _normalize_payload(data, image_url=image_url, category=category)
    # Fetch the profile hero image ONCE and stash on the payload — cover,
    # overview, persona-category, case-study, and final-insight slides all
    # reuse it (LISA embeds the same subject photo 12+ times across the
    # deck for continuity).
    profile["_image_bytes"] = _fetch_image_bytes(profile.get("image_url") or "")

    prs = _new_presentation()

    # ── OPENING ARC ─────────────────────────────────────────────────────
    _slide_cover(prs, profile)              # slide 1
    _slide_overview(prs, profile)           # slide 2 — "What's inside."
    _slide_scale(prs, profile)              # slide 3 — "By the numbers."
    _slide_demographics(prs, profile)       # slide 4 — IDENTITY stat cards
    _slide_index_story(prs, profile)        # slide 5 — over-index callouts
    _slide_geography(prs, profile)          # slide 6 — top DMAs (if data)
    _slide_one_insight(prs, profile)        # slide 7 — mid-brief narrative

    # ── CATEGORY DEEP-DIVES (4-6 slides depending on data density) ──────
    persona_slides = _pick_persona_category_slides(profile, max_slides=6)
    for i, (cat, items) in enumerate(persona_slides):
        # Alternate layout so the deck has editorial rhythm: even index =
        # KD multi-sub-section list, odd index = LISA-style big stat splash.
        if i % 2 == 0:
            _slide_persona_category(prs, profile, cat, items)
        else:
            _slide_stat_splash(prs, profile, cat, items)

    # ── PROOF ARC ───────────────────────────────────────────────────────
    _slide_case_study(prs, profile)         # 1-3 KD-style case-study slides
    _slide_data_confirms(prs, profile)      # narrative bridge
    _slide_media_and_social(prs, profile)   # media / social spotlight
    _slide_mpb_table(prs, profile)          # top 20 MPB brands (if data)

    # ── CLOSING ARC ─────────────────────────────────────────────────────
    _slide_takeaways(prs, profile)          # KD "Six things to know."
    _slide_final_insight(prs, profile)      # full-bleed image closer
    _slide_methodology(prs, profile)        # sample / measurement / benchmark
    _slide_about(prs, profile)              # Crosswalk capabilities

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def build_combined_deck(profiles: Iterable[dict]) -> bytes:
    """Comparative deck across multiple profiles + Gen Pop.

    Slide arc (2 profiles -> ~10 slides, 3 profiles -> ~12 slides):
      1  Cover with per-profile image collage
      2  Overview (what the comparison covers)
      3  Scale bars
      4  Demographics side-by-side
      5-N  Per-profile spotlight (one slide per profile with its hero image
           + top stats + top over-index brand)
      N+1  Category winners
      N+2  Brand overlap
      N+3  Final insight
    """
    normalized = [_normalize_payload(p) for p in profiles if p]
    if not normalized:
        raise ValueError("build_combined_deck requires at least one profile")
    # Pre-fetch every profile image once so cover collage + per-profile
    # spotlights reuse the same bytes.
    for pr in normalized:
        pr["_image_bytes"] = _fetch_image_bytes(pr.get("image_url") or "")

    prs = _new_presentation()

    _slide_combined_cover(prs, normalized)
    _slide_combined_overview(prs, normalized)
    _slide_combined_scale(prs, normalized)
    _slide_combined_demographics(prs, normalized)
    for pr in normalized:
        _slide_combined_profile_spotlight(prs, pr)
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


_S3_BUCKET_NAME = "dashboard-inputs"
_S3_REGION = "us-east-2"


def _fetch_via_s3(s3_key: str) -> Optional[bytes]:
    """Fetch an S3 object using the app's IAM credentials.

    The ``dashboard-inputs`` bucket is NOT publicly readable — direct
    anonymous fetches to the public https URL return 403. Every Profile IQ
    profile image lives in this bucket (under ``profile-images/...``), so
    we must go through boto3 with the same credentials the Flask app already
    uses. Imported lazily so environments without boto3 still let the deck
    build (falls back to placeholder cover for those images)."""
    try:
        import boto3  # deferred to avoid hard dep at module import time
        from botocore.config import Config as _BotoConfig
    except Exception:
        return None
    try:
        client = boto3.client(
            "s3",
            region_name=_S3_REGION,
            config=_BotoConfig(retries={"max_attempts": 2, "mode": "standard"}),
        )
        resp = client.get_object(Bucket=_S3_BUCKET_NAME, Key=s3_key)
        data = resp["Body"].read()
        if not data or len(data) < 200:
            return None
        return data
    except Exception as exc:  # pragma: no cover — logged, not raised
        print(f"[profile_iq_deck_builder] boto3 fetch failed for {s3_key!r}: {exc}")
        return None


def _fetch_image_bytes(image_url: str) -> Optional[bytes]:
    """Fetch a profile image URL. Returns None on any failure.

    Handles three shapes of URL:
      1. ``/api/profile-image-file/<key>``  → private S3 (boto3 path)
      2. ``https://dashboard-inputs.s3...``  → private S3 (boto3 path)
      3. any other http/https URL          → urllib (public web assets:
         IMDB posters, Wikipedia hero shots, admin-provided direct URLs)
    """
    if not image_url:
        return None

    # Path 1: Flask same-origin profile-image proxy → resolve S3 key
    if image_url.startswith("/api/profile-image-file/"):
        s3_key = image_url[len("/api/profile-image-file/"):]
        return _fetch_via_s3(s3_key)

    # Path 2: direct dashboard-inputs S3 URL (private bucket, needs IAM)
    for prefix in (
        f"https://{_S3_BUCKET_NAME}.s3.{_S3_REGION}.amazonaws.com/",
        f"https://{_S3_BUCKET_NAME}.s3.amazonaws.com/",
        f"https://s3.{_S3_REGION}.amazonaws.com/{_S3_BUCKET_NAME}/",
    ):
        if image_url.startswith(prefix):
            s3_key = urllib.parse.unquote(image_url[len(prefix):])
            return _fetch_via_s3(s3_key)

    # Path 3: any other URL → try public web fetch
    try:
        req = urllib.request.Request(image_url, headers={
            "User-Agent": "CrosswalkProfileIQDeck/1.0 (+jenna@crosswalknyc.com)",
            "Accept": "image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        if not data or len(data) < 200:
            return None
        return data
    except Exception as exc:  # pragma: no cover — logged, not raised
        print(f"[profile_iq_deck_builder] urllib fetch failed for {image_url!r}: {exc}")
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
        _place_prepared_image(slide, image_bytes, x=0, y=0,
                              w=8.4, h=SLIDE_H_IN, darken=0.72)
    except Exception:
        _placeholder_cover(slide, subject_name)
        return
    # Right-column accent strip (LISA has a dark solid on the right)
    _rect(slide, 8.4, 0, SLIDE_W_IN - 8.4, SLIDE_H_IN, C_DARK)
    # Bottom scrim so the tagline row has consistent contrast
    _rect(slide, 0, 4.9, SLIDE_W_IN, 2.6, C_DARK)


def _place_prepared_image(slide, image_bytes: bytes, *, x, y, w, h,
                          darken: float = 1.0) -> None:
    """Center-crop + resize + optional darken, then place at (x, y) size (w, h).

    ``darken`` is a Pillow ``ImageEnhance.Brightness`` factor (1.0 = untouched,
    0.7 = darker). Kept in one helper so every slide that uses imagery has
    consistent tone. Raises on failure so callers can fall back."""
    from PIL import Image, ImageEnhance
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    target_w_px = max(int(w * 220), 320)
    target_h_px = max(int(h * 220), 320)
    src_w, src_h = im.size
    src_ratio = src_w / src_h
    tgt_ratio = target_w_px / target_h_px
    if src_ratio > tgt_ratio:
        new_w = int(src_h * tgt_ratio)
        offset = (src_w - new_w) // 2
        im = im.crop((offset, 0, offset + new_w, src_h))
    elif src_ratio < tgt_ratio:
        new_h = int(src_w / tgt_ratio)
        # Bias slightly toward top so faces don't lose foreheads (editorial
        # crop heuristic).
        offset = max(0, (src_h - new_h) // 3)
        im = im.crop((0, offset, src_w, offset + new_h))
    im = im.resize((target_w_px, target_h_px), Image.LANCZOS)
    if darken and darken != 1.0:
        im = ImageEnhance.Brightness(im).enhance(darken)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=86, optimize=True)
    buf.seek(0)
    slide.shapes.add_picture(buf, Inches(x), Inches(y),
                             width=Inches(w), height=Inches(h))


def _place_side_image(slide, image_bytes: Optional[bytes], *, x, y, w, h,
                      darken: float = 1.0,
                      subject_name: str = "") -> None:
    """Wraps ``_place_prepared_image`` with a graceful fallback. Used by
    overview / persona-category / case-study / final-insight slides that
    want an image in a bounded region (not full-bleed)."""
    if image_bytes:
        try:
            _place_prepared_image(slide, image_bytes,
                                  x=x, y=y, w=w, h=h, darken=darken)
            return
        except Exception as exc:
            print(f"[profile_iq_deck_builder] side image failed: {exc}")
    # Fallback: dark rect with subject initial so slot doesn't look broken
    _rect(slide, x, y, w, h, RGBColor(0x12, 0x1D, 0x1F))
    initial = ((subject_name or "").strip() or "A")[0].upper()
    _text(slide, x, y + (h / 2) - 1.2, w, 2.4, initial,
          size=int(min(160, h * 30)), bold=True,
          color=RGBColor(0x22, 0x30, 0x34),
          align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


# =============================================================================
#  Slide builders  ·  SINGLE PROFILE
# =============================================================================


def _slide_cover(prs: Presentation, p: dict):
    """Cover slide — LISA layout: full-bleed image, big title, tagline.

    Uses the pre-fetched ``p['_image_bytes']`` so we don't re-hit S3."""
    slide = _blank_slide(prs, bg=C_DARK)
    img_bytes = p.get("_image_bytes")
    _cover_image(slide, img_bytes, p["name"])

    year_label = _year_label(p.get("date_range")) or datetime.now().strftime("%Y")
    _text(slide, 0.62, 0.62, 10.0, 0.30,
          f"A BEHAVIORAL AUDIENCE PROFILE   \u00b7   {year_label}",
          size=10, color=C_MUTED, letter_spacing=0.06)

    subject = p["name"]
    # LISA-style two-line title: "The {Subject}\naudience."
    _text(slide, 0.58, 3.08, 8.60, 1.10, f"The {subject}",
          size=54, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.58, 3.95, 8.60, 1.10, "audience.",
          size=54, bold=True, color=C_CREAM, line_spacing=1.02)

    tagline = _generate_tagline(p)
    _text(slide, 0.62, 5.02, 5.4, 1.60, tagline,
          size=12, color=C_MUTED, line_spacing=1.4)

    _text(slide, 0.62, 7.00, 4.0, 0.28, "CONFIDENTIAL",
          size=9, color=C_MUTED2, letter_spacing=0.06)
    _text(slide, 8.11, 7.00, 4.60, 0.28, "CROSSWALK  PROFILE IQ",
          size=9, color=C_MUTED2, align=PP_ALIGN.RIGHT, letter_spacing=0.06)


def _slide_overview(prs: Presentation, p: dict):
    """LISA overview layout: left-column narrative + numbered rows,
    right-column full-height hero image (5.28" wide)."""
    slide = _blank_slide(prs)

    # Right-column full-height image (LISA "What's inside" pattern).
    # Falls back to a dark rect if no image so layout still balances.
    _place_side_image(slide, p.get("_image_bytes"), x=8.05, y=0.0,
                      w=SLIDE_W_IN - 8.05, h=SLIDE_H_IN,
                      darken=0.90, subject_name=p["name"])

    _section_eyebrow(slide, 0.62, p["name"], "OVERVIEW")
    _text(slide, 0.62, 0.82, 8.0, 1.10, "What's inside.",
          size=30, bold=True, color=C_CREAM, line_spacing=1.05)

    body = (
        f"Over twelve months, Crosswalk observed how {p['name']}'s audience "
        f"behaves — what they watch, scroll, wear, and buy. Drawn from "
        f"{_fmt_int(p['sample_size']) if p['sample_size'] else '700,000+'} "
        f"zero-party panelists and projected across "
        f"{_fmt_int(p['projected_us']) if p['projected_us'] else '20M+'} "
        f"U.S. digital adults."
    )
    _text(slide, 0.62, 2.05, 6.85, 1.70, body,
          size=13.5, color=C_MUTED, line_spacing=1.45)

    # LISA-style numbered rows: 01 magenta numeral, bold row title,
    # muted description to the right. Hairline separators between rows.
    chapters = _pick_overview_chapters(p)
    row_top = 3.90
    row_h = 0.95
    for i, (num, title, subtitle) in enumerate(chapters):
        y = row_top + i * row_h
        _text(slide, 0.62, y, 0.80, 0.50, num,
              size=20, bold=True, color=C_MAGENTA)
        _text(slide, 1.57, y + 0.02, 2.70, 0.50, title,
              size=13.5, bold=True, color=C_CREAM)
        _text(slide, 4.37, y + 0.02, 3.10, 0.85, subtitle,
              size=10.5, color=C_MUTED, line_spacing=1.4)
        if i < len(chapters) - 1:
            _hairline(slide, 0.62, y + row_h - 0.08, 6.70, C_STROKE)

    _footer(slide, 2, p["name"])


def _slide_scale(prs: Presentation, p: dict):
    """KD 'By the numbers' slide: 4 giant stat callouts on top, big display
    title at bottom. Stats pull from the strongest demographic + audience
    scale signals so the slide is meaningful even for MPB-only profiles."""
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], "BY THE NUMBERS")

    proj = p["projected_us"] or 0
    sample = p["sample_size"] or 0
    subhead = (
        f"A real-time view of {p['name']}'s fanbase — "
        f"{_fmt_int(sample) if sample else 'panel'} panelists, "
        f"projected to a U.S. digital audience."
    )
    _text(slide, 0.62, 0.90, 12.0, 0.55, subhead,
          size=12.5, color=C_MUTED, line_spacing=1.4)

    stat_top = 1.85
    stat_h = 3.10
    # Build the 4 by-the-numbers slots dynamically.
    stats = _by_the_numbers_stats(p)
    n = max(1, len(stats))
    slot_w = (SLIDE_W_IN - 1.24 - 0.12 * (n - 1)) / n
    x = 0.62
    for i, (value, label, note, tone) in enumerate(stats):
        color = C_LAVENDER if tone == "lavender" else (
            C_MAGENTA if tone == "magenta" else C_CREAM)
        # Giant stat number
        _text(slide, x, stat_top, slot_w, 1.20, value,
              size=64, bold=True, color=color, line_spacing=1.0)
        # Small label (letter-spaced caps)
        _text(slide, x, stat_top + 1.25, slot_w, 0.30, label.upper(),
              size=10, bold=True, color=C_LAVENDER, letter_spacing=0.10)
        # Narrative note
        _text(slide, x, stat_top + 1.60, slot_w, 1.40, note,
              size=10.5, color=C_MUTED, line_spacing=1.45)
        x += slot_w + 0.12

    # Large display title bottom-left, LISA/KD "By the numbers." style
    _text(slide, 0.62, 5.60, 12.0, 1.2, "By the numbers.",
          size=52, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.62, 6.75, 12.0, 0.30,
          _source_line(p),
          size=8.5, color=C_MUTED2, letter_spacing=0.02)

    _footer(slide, 3, p["name"])


def _slide_demographics(prs: Presentation, p: dict):
    """LISA IDENTITY layout: 3-4 large stat cards on left (% + label + idx),
    right-side full-height image with darkened overlay, WHY IT MATTERS narrative."""
    slide = _blank_slide(prs)

    # Right-side hero image (LISA identity slide places the photo at x=3.13)
    _place_side_image(slide, p.get("_image_bytes"), x=6.10, y=0.0,
                      w=SLIDE_W_IN - 6.10, h=SLIDE_H_IN,
                      darken=0.55, subject_name=p["name"])
    # Left-side dark overlay to keep stat text readable regardless of image
    _rect(slide, 0, 0, 6.30, SLIDE_H_IN, C_DARK)

    _section_eyebrow(slide, 0.62, p["name"], "IDENTITY")
    _text(slide, 0.60, 0.82, 6.0, 1.10, _identity_headline(p),
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)

    # Build the 4 most-interesting demographic call-outs (top female share,
    # dominant age bucket, top ethnicity, top income) — dynamically chosen
    # so the slide is meaningful even for MPB-only or partial-demo profiles.
    stats = _identity_stat_rows(p)

    row_top = 2.30
    row_h = 1.10
    for i, (pct_str, label, idx_val) in enumerate(stats[:4]):
        y = row_top + i * row_h
        # Giant % (LISA uses lavender for over-index rows, cream otherwise)
        color = C_LAVENDER if idx_val >= 130 else C_CREAM
        _text(slide, 0.62, y, 2.30, 0.72, pct_str,
              size=32, bold=True, color=color, line_spacing=1.0)
        _text(slide, 2.97, y + 0.06, 3.30, 0.36, label,
              size=12.5, bold=True, color=color, letter_spacing=0.04)
        if idx_val:
            _text(slide, 2.97, y + 0.44, 3.30, 0.30,
                  f"idx  {int(idx_val)}",
                  size=10, color=C_MUTED, letter_spacing=0.05)
        if i < len(stats[:4]) - 1:
            _hairline(slide, 0.62, y + row_h - 0.05, 5.90, C_STROKE)

    # WHY IT MATTERS (LISA lime accent)
    _text(slide, 0.62, 6.15, 5.90, 0.26, "WHY IT MATTERS",
          size=9.5, bold=True, color=C_LIME, letter_spacing=0.10)
    _text(slide, 0.62, 6.42, 5.90, 0.55, _identity_why_it_matters(p),
          size=11, color=C_MUTED, line_spacing=1.4)

    _footer(slide, 4, p["name"])


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
    """KD multi-sub-section pattern: split the category's top brands into
    3-4 conceptual sub-columns (KD uses e.g. 'DIGITAL BANKING & PAY',
    'BANKING', 'INVESTMENTS', 'INTEREST SIGNAL' — grouped by role) and
    render each as a vertical list of ``Brand\\nPEN% (IDX)`` entries.

    We can't infer semantic sub-groups reliably, so we do a two-tier split:
    top 12 brands by index, chunked into either 3 or 4 columns depending on
    count. Falls back to a single ranked column if few brands. WHY IT
    MATTERS narrative anchors the story."""
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], _pretty_cat(cat).upper())

    headline, sub = _persona_category_headline(cat)
    full_title = f"{headline} {sub}".strip()
    _text(slide, 0.62, 0.82, 12.0, 1.10, full_title,
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)

    top = [it for it in items[:16] if _num(it.get("pct", 0)) > 0]
    if not top:
        _text(slide, 0.62, 3.5, 11.5, 0.6,
              "No branded signals observed in this category.",
              size=12, color=C_MUTED)
        _footer(slide, 8, p["name"])
        return

    why = _why_it_matters_for_category(cat, p, top[:3])
    _text(slide, 0.62, 2.00, 12.0, 0.36,
          f"WHY IT MATTERS  /  {why}",
          size=10, color=C_MUTED, letter_spacing=0.04, line_spacing=1.4)

    # Layout: 4 sub-sections across if we have >=8, else 3, else 2, else 1.
    n = len(top)
    n_cols = 4 if n >= 12 else (3 if n >= 6 else (2 if n >= 3 else 1))
    per_col = min(6, (n + n_cols - 1) // n_cols)

    col_w = (SLIDE_W_IN - 1.24 - 0.30 * (n_cols - 1)) / n_cols
    col_x = [0.62 + i * (col_w + 0.30) for i in range(n_cols)]
    hdr_labels = _sub_section_headers(cat, n_cols)

    top_y = 2.80
    row_h = 0.72

    for c in range(n_cols):
        x = col_x[c]
        _text(slide, x, top_y, col_w, 0.30, hdr_labels[c],
              size=10, bold=True, color=C_LAVENDER, letter_spacing=0.10)
        _hairline(slide, x, top_y + 0.30, col_w - 0.10, C_STROKE)
        start = c * per_col
        for r, it in enumerate(top[start:start + per_col]):
            y = top_y + 0.42 + r * row_h
            brand_name = (it.get("name") or "").strip()
            pct = _num(it.get("pct", 0))
            idx = int(_num(it.get("index", 0)))
            # Brand name line
            _text(slide, x, y, col_w - 0.10, 0.34, brand_name,
                  size=12.5, bold=True, color=C_CREAM)
            # KD-style "PEN% (IDX)" line
            pct_str = f"{pct:.1f}%"
            idx_str = f"({idx})" if idx else ""
            idx_color = (C_MAGENTA if idx >= 150 else
                         (C_LIME if idx >= 110 else C_MUTED))
            _text(slide, x, y + 0.34, col_w - 1.0, 0.30, pct_str,
                  size=11, color=C_CREAM)
            _text(slide, x + col_w - 1.0, y + 0.34, 0.90, 0.30, idx_str,
                  size=11, bold=True, color=idx_color, align=PP_ALIGN.RIGHT)

    _text(slide, 0.62, 6.75, 12.0, 0.30, _source_line(p),
          size=8.5, color=C_MUTED2, letter_spacing=0.02)
    _footer(slide, 8, p["name"])


def _slide_case_study(prs: Presentation, p: dict):
    """Emits 1-3 case-study slides (one per top over-indexing brand),
    each in the KD format: subject headline + deal-narrative paragraph +
    3 giant stat callouts (index, penetration, gen-pop delta)."""
    picks = _pick_case_studies(p, n=3)
    if not picks:
        return
    for i, cs in enumerate(picks, start=1):
        _emit_case_study_slide(prs, p, cs, case_num=i)


def _emit_case_study_slide(prs, p: dict, cs: dict, *, case_num: int):
    """Render one KD case-study slide. ``cs`` shape:
    ``{name, category, pct, index, gen_pop_pct, projection, narrative}``."""
    slide = _blank_slide(prs, bg=C_DARK)
    # Right-column subject image with strong darken
    img = p.get("_image_bytes")
    if img:
        try:
            _place_prepared_image(slide, img, x=0, y=0,
                                  w=SLIDE_W_IN, h=SLIDE_H_IN, darken=0.30)
        except Exception:
            pass
    # Left-side dark scrim
    _rect(slide, 0, 0, 6.0, SLIDE_H_IN, RGBColor(0x08, 0x0F, 0x11))

    brand = cs["name"]
    _text(slide, 0.62, 0.55, 12.0, 0.30,
          f"CASE STUDY  {case_num:02d}",
          size=10.5, color=C_MAGENTA, letter_spacing=0.10)
    _text(slide, 0.62, 0.90, 12.0, 0.30,
          f"THE {p['name'].upper()} AUDIENCE  /  {brand.upper()}",
          size=9.5, color=C_MUTED, letter_spacing=0.06)

    # Two-line editorial headline
    headline_a, headline_b = cs["headline_a"], cs["headline_b"]
    _text(slide, 0.62, 1.70, 5.6, 1.30, headline_a,
          size=40, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.62, 2.80, 5.6, 1.30, headline_b,
          size=40, bold=True, color=C_LAVENDER, line_spacing=1.02)

    # Deal narrative paragraph
    _text(slide, 0.62, 4.20, 5.6, 2.30, cs["narrative"],
          size=11, color=C_CREAM, line_spacing=1.5)

    # Right side: 3 giant stat callouts
    x = 6.60
    y = 1.70
    for stat_num, (label, value, sub) in enumerate(cs["stats"][:3]):
        _text(slide, x, y, 6.2, 1.20, value,
              size=52, bold=True, color=C_MAGENTA, line_spacing=1.0)
        _text(slide, x, y + 1.20, 6.2, 0.30,
              label.upper(),
              size=10, bold=True, color=C_CREAM, letter_spacing=0.08)
        _text(slide, x, y + 1.52, 6.2, 0.50, sub,
              size=10.5, color=C_MUTED, line_spacing=1.4)
        if stat_num < 2:
            _hairline(slide, x, y + 2.10, 5.8, C_STROKE)
        y += 1.65

    _text(slide, 0.62, 6.75, 8.0, 0.30, _source_line(p),
          size=8.5, color=C_MUTED2, letter_spacing=0.02)
    _footer(slide, 99, p["name"])


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
    """KD/LISA final-insight closer: full-bleed dark hero image (darkened),
    tiny 'THE FINAL INSIGHT' eyebrow, massive display headline, muted
    narrative paragraph. Two-line display for editorial rhythm."""
    slide = _blank_slide(prs, bg=C_DARK)
    # Full-bleed darkened image (LISA slide 19 pattern)
    img_bytes = p.get("_image_bytes")
    if img_bytes:
        try:
            _place_prepared_image(slide, img_bytes, x=0, y=0,
                                  w=SLIDE_W_IN, h=SLIDE_H_IN, darken=0.38)
        except Exception:
            _rect(slide, 0, 0, SLIDE_W_IN, SLIDE_H_IN, C_DARK)
    # Extra dark scrim on the left half so headline reads clean
    _rect(slide, 0, 0, 9.0, SLIDE_H_IN, RGBColor(0x08, 0x0F, 0x11))

    subject = p["name"]
    top = _top_over_index_brand(p)

    _text(slide, 0.62, 0.62, 8.0, 0.30, "THE FINAL INSIGHT",
          size=10.5, color=C_LIME, letter_spacing=0.10)

    # KD's closer format is a single sentence broken into two/three big display
    # lines. We synthesize from the strongest signal.
    line1, line2 = _final_headline(p)
    _text(slide, 0.62, 2.20, 8.6, 1.60, line1,
          size=54, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.62, 3.55, 8.6, 1.60, line2,
          size=54, bold=True, color=C_LAVENDER, line_spacing=1.02)

    body = _final_body(p, top)
    _text(slide, 0.62, 5.30, 7.4, 1.60, body,
          size=13, color=C_CREAM, line_spacing=1.5)

    _footer(slide, 99, p["name"])


# =============================================================================
#  KD-inspired slide builders  ·  index story, geography, MPB table,
#  takeaways, methodology, about, narrative bridges
# =============================================================================


def _slide_index_story(prs: Presentation, p: dict):
    """KD slide 5 pattern: 'Where the audience over-indexes most.' — 4 large
    index-vs-Gen-Pop callouts pulled from the strongest demographic gaps."""
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"],
                     "INDEX VS GEN POP  /  THE STORY")
    _text(slide, 0.62, 0.82, 12.0, 1.10,
          "Where the audience over-indexes most.",
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)

    why = ("KD-style over-indexing across key demographic attributes — every "
           "callout is a targetable resonance signal.")
    _text(slide, 0.62, 1.98, 12.0, 0.36,
          f"WHY IT MATTERS  /  {why}",
          size=10, color=C_MUTED, letter_spacing=0.04)

    picks = _top_demo_index_callouts(p, n=4)
    if not picks:
        _text(slide, 0.62, 3.5, 11.5, 0.6,
              "Insufficient demographic data to compute over-index story.",
              size=12, color=C_MUTED)
        _footer(slide, 5, p["name"])
        return

    n = len(picks)
    slot_w = (SLIDE_W_IN - 1.24 - 0.20 * (n - 1)) / n
    x = 0.62
    y = 2.90
    for value, label, sub in picks:
        # Giant index number
        _text(slide, x, y, slot_w, 1.60, str(int(value)),
              size=88, bold=True, color=C_MAGENTA, line_spacing=1.0)
        _text(slide, x, y + 1.80, slot_w, 0.28,
              "INDEX vs GEN POP",
              size=9.5, color=C_MUTED, letter_spacing=0.10)
        _hairline(slide, x, y + 2.15, slot_w - 0.10, C_STROKE)
        _text(slide, x, y + 2.30, slot_w, 0.36, label,
              size=13.5, bold=True, color=C_CREAM, letter_spacing=0.04)
        _text(slide, x, y + 2.72, slot_w, 0.65, sub,
              size=10.5, color=C_MUTED, line_spacing=1.4)
        x += slot_w + 0.20

    _text(slide, 0.62, 6.75, 12.0, 0.30, _source_line(p),
          size=8.5, color=C_MUTED2, letter_spacing=0.02)
    _footer(slide, 5, p["name"])


def _slide_geography(prs: Presentation, p: dict):
    """KD-style geography slide: ranked list of top DMAs with pen% and idx.

    Renders as a two-column list (10 rows total) with big-market callout
    at the top. Skips itself gracefully if no location data is available."""
    dmas = _top_dmas_ranked(p, n=10)
    if not dmas:
        return  # skip slide if no geo data
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], "GEOGRAPHY")
    _text(slide, 0.62, 0.82, 12.0, 1.10,
          "Big-market reach, ranked.",
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 2.02, 12.0, 0.85,
          f"National footprint of the {p['name']} audience, ranked by share "
          "of panelists per Nielsen DMA. Index shows over/under vs national baseline.",
          size=12, color=C_MUTED, line_spacing=1.4)

    _text(slide, 0.62, 3.15, 12.0, 0.30, "TOP 10 NIELSEN DMAs  (RANK)",
          size=10, bold=True, color=C_LAVENDER, letter_spacing=0.10)

    # Two columns of 5 rows each
    row_h = 0.52
    col_w = 5.90
    col_x = [0.62, 0.62 + col_w + 0.30]
    top_y = 3.60
    for i, (name, pct, idx) in enumerate(dmas):
        col = i // 5
        row = i % 5
        x = col_x[col]
        y = top_y + row * row_h
        rank_lbl = f"{i+1:02d}"
        # Rank chip
        _text(slide, x, y, 0.55, row_h, rank_lbl,
              size=13, bold=True, color=C_LAVENDER, anchor=MSO_ANCHOR.MIDDLE)
        # DMA name
        _text(slide, x + 0.55, y, col_w - 2.30, row_h,
              name, size=12, bold=True, color=C_CREAM,
              anchor=MSO_ANCHOR.MIDDLE)
        # Pen %
        _text(slide, x + col_w - 1.70, y, 0.90, row_h,
              f"{pct:.1f}%", size=12, color=C_CREAM,
              align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
        # Index chip
        color = (C_MAGENTA if idx >= 150 else
                 (C_LIME if idx >= 110 else C_MUTED))
        _text(slide, x + col_w - 0.75, y, 0.75, row_h,
              f"{int(idx)}" if idx else "—",
              size=12, bold=True, color=color,
              align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
        _hairline(slide, x, y + row_h - 0.02,
                  col_w - 0.10, C_STROKE)

    _text(slide, 0.62, 6.75, 12.0, 0.30, _source_line(p),
          size=8.5, color=C_MUTED2, letter_spacing=0.02)
    _footer(slide, 6, p["name"])


def _slide_stat_splash(prs: Presentation, p: dict, cat: str, items: list):
    """LISA slide 10 pattern (KD sub-section stack): two large brand stat
    tiles per category showing giant reach% and giant idx. Used to break
    up the persona-category slide rhythm."""
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], _pretty_cat(cat).upper())
    headline, sub = _persona_category_headline(cat)
    _text(slide, 0.60, 0.82, 12.0, 1.10, f"{headline} {sub}".strip(),
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)

    top = [it for it in items[:2] if _num(it.get("pct", 0)) > 0]
    if not top:
        _text(slide, 0.62, 3.5, 11.5, 0.6,
              "No branded signals observed in this category.",
              size=12, color=C_MUTED)
        _footer(slide, 8, p["name"])
        return

    tile_w = 5.60
    tile_gap = 0.40
    y0 = 2.60
    x0 = 0.62
    for i, it in enumerate(top[:2]):
        x = x0 + i * (tile_w + tile_gap)
        pct = _num(it.get("pct", 0))
        idx = int(_num(it.get("index", 0)))
        proj = _num(it.get("projection", 0))
        name = (it.get("name") or "").strip()
        gp_pct = _num(it.get("genPopPct", 0))

        _text(slide, x, y0, tile_w, 0.36, name.upper(),
              size=11, bold=True, color=C_LAVENDER, letter_spacing=0.08)
        # Giant reach %
        _text(slide, x, y0 + 0.45, tile_w, 1.30, f"{pct:.1f}%",
              size=64, bold=True, color=C_CREAM, line_spacing=1.0)
        _text(slide, x, y0 + 1.75, tile_w, 0.28, "REACH",
              size=9.5, color=C_MUTED, letter_spacing=0.10)
        _hairline(slide, x, y0 + 2.05, tile_w - 0.10, C_STROKE)
        # Giant index
        idx_color = (C_MAGENTA if idx >= 150 else
                     (C_LIME if idx >= 110 else C_LAVENDER))
        _text(slide, x, y0 + 2.20, tile_w, 1.30,
              f"{idx}" if idx else "—",
              size=76, bold=True, color=idx_color, line_spacing=1.0)
        _text(slide, x, y0 + 3.55, tile_w, 0.28,
              f"INDEX vs GEN POP  ·  {gp_pct:.1f}% BASELINE",
              size=9.5, color=C_MUTED, letter_spacing=0.08)
        if proj:
            _text(slide, x, y0 + 3.85, tile_w, 0.28,
                  f"= {_fmt_int(proj)} U.S. adults",
                  size=10, color=C_MUTED)

    _text(slide, 0.62, 6.15, 12.0, 0.26, "WHY IT MATTERS",
          size=9.5, bold=True, color=C_LIME, letter_spacing=0.10)
    _text(slide, 0.62, 6.42, 12.0, 0.55,
          _why_it_matters_for_category(cat, p, top),
          size=11, color=C_MUTED, line_spacing=1.4)

    _footer(slide, 8, p["name"])


def _slide_data_confirms(prs: Presentation, p: dict):
    """LISA slide 9 pattern — a narrative bridge slide.

    Big two-line editorial statement + one-paragraph body that reframes
    the strongest signal in plain English. Uses lavender for the second
    line (LISA's contrast trick)."""
    slide = _blank_slide(prs, bg=C_DARK)
    img = p.get("_image_bytes")
    if img:
        try:
            _place_prepared_image(slide, img, x=8.10, y=0.0,
                                  w=SLIDE_W_IN - 8.10, h=SLIDE_H_IN,
                                  darken=0.55)
        except Exception:
            pass

    top = _top_over_index_brand(p)
    if top:
        cat = _pretty_cat(top.get("_cat", "")).lower()
        line1 = "The data confirms"
        line2 = "the connection."
        body = (
            f"{p['name']}'s audience over-indexes on {top['name']} at "
            f"{int(_num(top['index']))} — {cat or 'a durable category'}'s "
            f"strongest single signal in the file. The fandom and the buying "
            f"behavior are the same audience."
        )
    else:
        line1 = "One dataset,"
        line2 = "one audience."
        body = ("This audience concentrates habits across multiple categories, "
                "which is a signal in itself. Any brand aligned here captures "
                "a habit, not a moment.")

    _text(slide, 0.62, 2.50, 8.0, 1.60, line1,
          size=58, bold=True, color=C_CREAM, line_spacing=1.02)
    _text(slide, 0.62, 3.95, 8.0, 1.60, line2,
          size=58, bold=True, color=C_LAVENDER, line_spacing=1.02)
    _text(slide, 0.62, 5.70, 7.0, 1.30, body,
          size=13, color=C_MUTED, line_spacing=1.45)
    _footer(slide, 99, p["name"])


def _slide_mpb_table(prs: Presentation, p: dict):
    """KD slide 20 pattern: top 20 most-purchased brands ranked table.

    Two columns of 10 rows: rank / brand name / pen% / index. Uses the
    behavioral['MPB'] or ['MOST PURCHASED BRANDS'] category, whichever
    populates. Skips if <5 brands available."""
    brands = _mpb_top_brands(p, n=20)
    if len(brands) < 5:
        return
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], "MOST PURCHASED BRANDS")
    _text(slide, 0.62, 0.82, 12.0, 1.10, "The brands they buy.",
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)
    tracked = len(_all_behavioral_items(p))
    _text(slide, 0.62, 2.00, 12.0, 0.36,
          f"WHY IT MATTERS  /  Of {tracked} tracked signals, the top {len(brands)} "
          "reveal where this audience concentrates commerce.",
          size=10, color=C_MUTED, letter_spacing=0.04)

    # Column headers
    col_w = 5.90
    col_x = [0.62, 0.62 + col_w + 0.30]
    hdr_y = 2.85
    for cx in col_x:
        _text(slide, cx, hdr_y, 0.60, 0.28, "#",
              size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
        _text(slide, cx + 0.60, hdr_y, col_w - 2.40, 0.28, "BRAND",
              size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06)
        _text(slide, cx + col_w - 1.70, hdr_y, 0.90, 0.28, "PEN %",
              size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06,
              align=PP_ALIGN.RIGHT)
        _text(slide, cx + col_w - 0.75, hdr_y, 0.75, 0.28, "INDEX",
              size=9, bold=True, color=C_LAVENDER, letter_spacing=0.06,
              align=PP_ALIGN.RIGHT)
        _hairline(slide, cx, hdr_y + 0.28, col_w - 0.10, C_STROKE)

    row_h = 0.34
    top_y = 3.20
    for i, it in enumerate(brands[:20]):
        col = i // 10
        row = i % 10
        x = col_x[col]
        y = top_y + row * row_h
        _text(slide, x, y, 0.60, row_h, f"{i+1:02d}",
              size=10.5, bold=True, color=C_LAVENDER,
              anchor=MSO_ANCHOR.MIDDLE)
        _text(slide, x + 0.60, y, col_w - 2.40, row_h,
              (it.get("name") or "").strip(),
              size=11, color=C_CREAM, anchor=MSO_ANCHOR.MIDDLE)
        pct = _num(it.get("pct", 0))
        idx = int(_num(it.get("index", 0)))
        _text(slide, x + col_w - 1.70, y, 0.90, row_h,
              f"{pct:.1f}", size=11, color=C_CREAM,
              align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
        idx_color = (C_MAGENTA if idx >= 150 else
                     (C_LIME if idx >= 110 else C_MUTED))
        _text(slide, x + col_w - 0.75, y, 0.75, row_h,
              f"{idx}" if idx else "—",
              size=11, bold=True, color=idx_color,
              align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)

    _text(slide, 0.62, 6.75, 12.0, 0.30, _source_line(p),
          size=8.5, color=C_MUTED2, letter_spacing=0.02)
    _footer(slide, 99, p["name"])


def _slide_takeaways(prs: Presentation, p: dict):
    """KD slide 22 pattern: 'Six things to know.' — 6 numbered summary
    cards in a 3x2 grid, each with a big number, headline, and fact bullets."""
    picks = _pick_takeaways(p)
    if not picks:
        return
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], "KEY TAKEAWAYS")
    _text(slide, 0.62, 0.82, 12.0, 1.10, "Six things to know.",
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 2.00, 12.0, 0.36,
          "If you remember nothing else from this brief, remember these.",
          size=12, color=C_MUTED, line_spacing=1.4)

    tile_w = 4.05
    tile_h = 2.10
    tile_gap = 0.10
    top_y = 2.75
    for i, (num, title, body) in enumerate(picks[:6]):
        col = i % 3
        row = i // 3
        x = 0.62 + col * (tile_w + tile_gap)
        y = top_y + row * (tile_h + 0.12)
        _rect(slide, x, y, tile_w, tile_h, RGBColor(0x14, 0x1F, 0x22))
        _text(slide, x + 0.28, y + 0.20, 0.9, 0.60, num,
              size=26, bold=True, color=C_MAGENTA, line_spacing=1.0)
        _text(slide, x + 0.28, y + 0.72, tile_w - 0.50, 0.55, title,
              size=13, bold=True, color=C_CREAM, line_spacing=1.15)
        _text(slide, x + 0.28, y + 1.20, tile_w - 0.50, tile_h - 1.30, body,
              size=9.5, color=C_MUTED, line_spacing=1.4)

    _footer(slide, 99, p["name"])


def _slide_methodology(prs: Presentation, p: dict):
    """KD slide 23 pattern: 'How this brief was built.' — 4 subsections
    (Sample, Measurement, Benchmark, Reading the numbers)."""
    slide = _blank_slide(prs)
    _section_eyebrow(slide, 0.52, p["name"], "METHODOLOGY")
    _text(slide, 0.62, 0.82, 12.0, 1.10, "How this brief was built.",
          size=28, bold=True, color=C_CREAM, line_spacing=1.05)

    sample = p["sample_size"]
    proj   = p["projected_us"]
    dr     = p.get("date_range") or ""
    subj   = p["name"]

    sections = [
        ("SAMPLE",
         (f"{_fmt_int(sample) if sample else 'The panel'} zero-party opted-in "
          f"U.S. panelists who exhibited {subj} engagement behavior "
          f"{('between ' + dr) if dr else 'in the observation window'}. "
          "Census-based panel of 10M respondents projected to a total U.S. "
          "digital audience of "
          f"{_fmt_int(proj) if proj else '329.9M adults'}.\n\n"
          "Engagement vectors include: Search, Social, Owned Channels, "
          "Commerce, and Media.")),
        ("MEASUREMENT",
         ("Crosswalk Profile IQ. Behavioral observations are deterministic and "
          "observed across the open web at the individual panelist level. "
          "Demographic data is self-reported at registration.")),
        ("BENCHMARK",
         ("Comparison to a General Population (Gen. Pop.) sample projected to "
          "329.9M U.S. adults, drawn from the same observation window.\n\n"
          "Index = (audience share) / (Gen Pop share) x 100. "
          "Index of 100 = parity (national norm).")),
        ("READING THE NUMBERS",
         ("Charts display talent/brand/category percent penetration - the % "
          "of the audience exhibiting that behavior. Every panelist voluntarily "
          "opted in. Clean consent lineage. Audit-ready provenance.")),
    ]

    col_w = 5.90
    col_gap = 0.30
    row_h = 2.05
    positions = [
        (0.62, 2.30),
        (0.62 + col_w + col_gap, 2.30),
        (0.62, 2.30 + row_h + 0.20),
        (0.62 + col_w + col_gap, 2.30 + row_h + 0.20),
    ]
    for (title, body), (x, y) in zip(sections, positions):
        _text(slide, x, y, col_w, 0.32, title,
              size=11, bold=True, color=C_LAVENDER, letter_spacing=0.10)
        _text(slide, x, y + 0.38, col_w, row_h - 0.42, body,
              size=10.5, color=C_MUTED, line_spacing=1.4)

    _footer(slide, 99, p["name"])


def _slide_about(prs: Presentation, p: dict):
    """KD slide 24 pattern: 'Behavioral intelligence at scale.' — 4
    Crosswalk capability stats (events, panelists, freshness, archive)."""
    slide = _blank_slide(prs, bg=C_DARK)
    _text(slide, 0.62, 0.62, 12.0, 0.30, "ABOUT",
          size=10.5, color=C_LIME, letter_spacing=0.10)
    _text(slide, 0.62, 0.98, 12.0, 1.20, "Behavioral intelligence at scale.",
          size=36, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 2.55, 12.0, 0.40,
          "We don't infer behavior. We observe it.",
          size=15, color=C_MUTED, line_spacing=1.4)

    stats = [
        ("8B+", "DAILY BEHAVIORAL EVENTS",
         "Real-time observation across the open web — purchase, attention, "
         "subscription, media."),
        ("30M", "PERMISSIONED PANELISTS",
         "Zero-party consented individuals, longitudinally observed for up to "
         "five years. Demographic-grounded. 10M census-based Gen. Pop. subset."),
        ("T+1", "DAILY DATA",
         "Yesterday's behavior, not last year's. Live attention migration and "
         "consumer shifts available at enterprise scale."),
        ("ARCHIVE", "ACTIVE HISTORICAL",
         "Unprecedented access to historical data, anytime."),
    ]
    n = len(stats)
    slot_w = (SLIDE_W_IN - 1.24 - 0.15 * (n - 1)) / n
    x = 0.62
    for value, label, note in stats:
        _text(slide, x, 3.60, slot_w, 1.20, value,
              size=56, bold=True, color=C_LAVENDER, line_spacing=1.0)
        _text(slide, x, 4.85, slot_w, 0.30, label,
              size=10, bold=True, color=C_CREAM, letter_spacing=0.10)
        _text(slide, x, 5.22, slot_w, 1.30, note,
              size=10.5, color=C_MUTED, line_spacing=1.45)
        x += slot_w + 0.15

    _footer(slide, 99, p["name"])


# =============================================================================
#  Slide builders  ·  COMBINED / COMPARATIVE
# =============================================================================


def _slide_combined_cover(prs: Presentation, profiles: list[dict]):
    """Comparison cover: right-side vertical image collage of the profiles,
    left side big two-line comparison title + editorial tagline. Falls back
    to placeholder gradient if none of the profiles resolved images."""
    slide = _blank_slide(prs, bg=C_DARK)

    # Right-side image column split evenly by profile count. Each image is
    # darkened + center-cropped to fill its slot.
    imgs = [pr for pr in profiles if pr.get("_image_bytes")]
    if imgs:
        slot_h = SLIDE_H_IN / len(imgs)
        for i, pr in enumerate(imgs):
            try:
                _place_prepared_image(
                    slide, pr["_image_bytes"],
                    x=8.05, y=i * slot_h,
                    w=SLIDE_W_IN - 8.05, h=slot_h,
                    darken=0.68,
                )
            except Exception:
                _rect(slide, 8.05, i * slot_h,
                      SLIDE_W_IN - 8.05, slot_h,
                      RGBColor(0x12, 0x1D, 0x1F))
    else:
        _rect(slide, 8.05, 0, SLIDE_W_IN - 8.05, SLIDE_H_IN,
              RGBColor(0x12, 0x1D, 0x1F))
    # Left-side dark scrim
    _rect(slide, 0, 0, 8.05, SLIDE_H_IN, C_DARK)

    year_label = datetime.now().strftime("%Y")
    _text(slide, 0.62, 0.62, 10.0, 0.30,
          f"A BEHAVIORAL AUDIENCE COMPARISON   \u00b7   {year_label}",
          size=10, color=C_MUTED, letter_spacing=0.06)

    if len(profiles) == 2:
        title_a = profiles[0]["name"] + ","
        title_b = f"vs. {profiles[1]['name']}."
    elif len(profiles) == 3:
        title_a = f"{profiles[0]['name']}, {profiles[1]['name']},"
        title_b = f"and {profiles[2]['name']}."
    else:
        title_a = f"{len(profiles)} audiences."
        title_b = "One dataset."
    _text(slide, 0.62, 2.60, 7.30, 1.30, title_a,
          size=44, bold=True, color=C_CREAM, line_spacing=1.05)
    _text(slide, 0.62, 3.80, 7.30, 1.30, title_b,
          size=44, bold=True, color=C_CREAM, line_spacing=1.05)

    subj_list = ", ".join(pr["name"] for pr in profiles[:4])
    tag = (
        f"Where do their fans overlap, and where do they diverge? "
        f"A side-by-side read of {subj_list} on scale, demographics, "
        "category signal, and the brands they share."
    )
    _text(slide, 0.62, 5.30, 7.0, 1.60, tag,
          size=12, color=C_MUTED, line_spacing=1.45)

    _text(slide, 0.62, 7.00, 4.0, 0.28, "CONFIDENTIAL",
          size=9, color=C_MUTED2, letter_spacing=0.06)
    _text(slide, 8.11, 7.00, 4.60, 0.28, "CROSSWALK  PROFILE IQ",
          size=9, color=C_MUTED2, align=PP_ALIGN.RIGHT, letter_spacing=0.06)


def _slide_combined_profile_spotlight(prs: Presentation, pr: dict):
    """One slide per profile in the combined deck. Shows the subject image,
    scale, top demo call-out, and the profile's #1 over-index brand. Gives
    the deck a rhythm of comparison -> spotlight -> comparison -> spotlight."""
    slide = _blank_slide(prs, bg=C_DARK)
    # Right-side subject image (like LISA identity)
    _place_side_image(slide, pr.get("_image_bytes"), x=8.05, y=0.0,
                      w=SLIDE_W_IN - 8.05, h=SLIDE_H_IN,
                      darken=0.72, subject_name=pr["name"])

    _text(slide, 0.62, 0.62, 12.0, 0.30,
          f"AUDIENCE SPOTLIGHT  \u00b7  {pr['name'].upper()}",
          size=10, color=C_LAVENDER, letter_spacing=0.10)
    _text(slide, 0.60, 0.98, 8.0, 1.20, pr["name"] + ".",
          size=40, bold=True, color=C_CREAM, line_spacing=1.05)

    # Three-stat mini "by the numbers"
    proj = pr["projected_us"] or 0
    top = _top_over_index_brand(pr)
    peak = _peak_index(pr)
    demos = pr.get("demographics") or {}
    prime = sum(_num(v) for k, v in demos.get("age", {}).items()
                if str(k).replace("–", "-") in ("18-24", "25-34", "35-44"))

    stats = [
        (_fmt_int_compact(proj) or "—", "U.S. DIGITAL AUDIENCE"),
        (f"{prime:.1f}%" if prime else "—", "AGE 18-44 SHARE"),
        (f"{int(peak)}" if peak else "—", "TOP INDEX vs GEN POP"),
    ]
    y = 2.65
    for value, label in stats:
        _text(slide, 0.62, y, 3.4, 1.10, value,
              size=44, bold=True, color=C_CREAM, line_spacing=1.0)
        _text(slide, 0.62, y + 1.10, 3.4, 0.30, label,
              size=9.5, color=C_MUTED, letter_spacing=0.10)
        y += 1.50

    if top:
        _text(slide, 4.60, 2.65, 3.3, 0.30, "STRONGEST BRAND SIGNAL",
              size=9.5, bold=True, color=C_LIME, letter_spacing=0.10)
        _text(slide, 4.60, 3.00, 3.3, 0.50, top["name"],
              size=22, bold=True, color=C_CREAM)
        _text(slide, 4.60, 3.55, 3.3, 0.30,
              f"{_num(top.get('pct', 0)):.1f}% penetration",
              size=11, color=C_CREAM)
        _text(slide, 4.60, 3.85, 3.3, 0.30,
              f"{int(_num(top.get('index', 0)))} index vs Gen Pop",
              size=11, bold=True, color=C_MAGENTA)
        _text(slide, 4.60, 4.25, 3.3, 0.30,
              f"Category: {_pretty_cat(top.get('_cat', ''))}",
              size=10, color=C_MUTED)

    _footer(slide, 99, "Comparison")


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
    "APPAREL/FOOTWEAR", "APPAREL_FOOTWEAR",
    "LUXURY FASHION", "LUXURY_FASHION",
    "RETAIL", "QSR", "TRAVEL", "BANKING", "AUTOMOBILE",
    "GAMES", "GAMING", "TICKETING PLATFORMS", "FESTIVALS",
    "CULTURE", "MUSIC", "ARTIST", "ARTISTS",
]

_SKIP_PERSONA_CATS = {"SOCIAL MEDIA", "SOCIAL_MEDIA", "STREAMING/PLATFORM",
                      "STREAMING_PLATFORM", "STREAMING VIDEO",
                      "STREAMING_VIDEO", "MEDIA",
                      # MPB gets its own dedicated ranked-table slide via
                      # _slide_mpb_table, so don't also spend a persona slot on it.
                      "MPB", "MOST PURCHASED BRANDS", "MOST_PURCHASED_BRANDS"}


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
#  Data helpers for KD-style slides (by-the-numbers, index story, geography,
#  case studies, takeaways, MPB table, methodology, sub-section headers)
# =============================================================================

# Rough Gen Pop shares used for index computations when the payload does not
# ship pre-computed index maps. Approximates U.S. adult digital census (2025).
_APPROX_GENPOP_AGE = {
    "17 & under": 8.0, "17 and under": 8.0, "under 18": 8.0,
    "18-24": 12.0, "18–24": 12.0,
    "25-34": 17.0, "25–34": 17.0,
    "35-44": 16.5, "35–44": 16.5,
    "45-54": 15.5, "45–54": 15.5,
    "55-64": 14.0, "55–64": 14.0,
    "65+": 17.0,
}
_APPROX_GENPOP_ETH = {
    "White": 59.0, "Black": 12.5, "Black or African American": 12.5,
    "Hispanic": 18.5, "Hispanic or Latino": 18.5,
    "Asian": 6.5, "Two or More Races": 2.5, "Another": 1.5,
    "American Indian or Alaska Native": 0.7,
    "Native Hawaiian or Other Pacific Islander": 0.3,
}
_APPROX_GENPOP_GENDER = {"Female": 51.0, "Male": 49.0}


def _lookup_idx(p: dict, dim: str, bucket: str) -> float:
    """Return audience index for a demo bucket; fall back to approximation."""
    idx_map = (p.get("demographics_index") or {}).get(dim, {}) or {}
    v = _num(idx_map.get(bucket, 0))
    if v:
        return v
    share = _num((p.get("demographics") or {}).get(dim, {}).get(bucket, 0))
    if not share:
        return 0
    gp_source = {"age": _APPROX_GENPOP_AGE,
                 "ethnicity": _APPROX_GENPOP_ETH,
                 "gender": _APPROX_GENPOP_GENDER}.get(dim, {})
    gp = _num(gp_source.get(bucket, 0))
    if not gp:
        return 0
    return round(share / gp * 100)


def _source_line(p: dict) -> str:
    """Canonical KD-style source footer line."""
    n = _fmt_int(p.get("sample_size") or 0)
    dr = (p.get("date_range") or "").strip()
    parts = ["Source: Crosswalk Profile IQ"]
    if dr:
        parts.append(dr)
    if n:
        parts.append(f"n = {n} panelists")
    parts.append("Index = audience share vs Gen. Pop.")
    return "  /  ".join(parts)


def _by_the_numbers_stats(p: dict) -> list[tuple[str, str, str, str]]:
    """Pick the 4 most-quotable stats for the KD 'By the numbers' slide.
    Returns [(value, label, note, tone), ...] where tone is
    'cream' / 'lavender' / 'magenta' for color choice."""
    out: list[tuple[str, str, str, str]] = []
    proj = p.get("projected_us") or 0
    sample = p.get("sample_size") or 0

    # Stat 1: audience scale
    out.append((
        _fmt_int_compact(proj) or "—",
        "U.S. DIGITAL AUDIENCE",
        (f"Insights based on {_fmt_int(sample) or 'panel'} panelists" +
         (f", tracked {p.get('date_range')}" if p.get("date_range") else "")
         + "."),
        "cream",
    ))

    # Stat 2: primary age band (combine 18-24, 25-34, 35-44 if present)
    age = (p.get("demographics") or {}).get("age", {}) or {}
    prime = 0.0
    for k, v in age.items():
        key = str(k).strip().lower().replace("–", "-")
        if key in ("18-24", "25-34", "35-44"):
            prime += _num(v)
    if prime:
        prime_idx = round((prime / 41.5) * 100)  # ~41.5% Gen Pop 18-44
        out.append((
            f"{prime:.1f}%",
            "AGE 18-44",
            (f"Driving culture with a core audience. "
             f"Index {prime_idx} vs ~41.5% of Gen. Pop."),
            "lavender" if prime_idx >= 130 else "cream",
        ))

    # Stat 3: top ethnicity over-index
    eth = (p.get("demographics") or {}).get("ethnicity", {}) or {}
    top_eth = None
    best_idx = 0
    for k, v in eth.items():
        idx = _lookup_idx(p, "ethnicity", k)
        if idx > best_idx and idx > 110:
            top_eth = (k, _num(v), idx)
            best_idx = idx
    if top_eth:
        k, share, idx = top_eth
        gp = _APPROX_GENPOP_ETH.get(k, 0)
        note = (f"{share:.1f}% of the audience is {k}"
                + (f" vs {gp:.1f}% of Gen Pop." if gp else ".")
                + f" A significant anchor of this profile.")
        out.append((f"{int(idx)}", f"INDEX — {k.upper()} AUDIENCE",
                    note, "magenta"))

    # Stat 4: income $75k+ combined
    inc = (p.get("demographics") or {}).get("income", {}) or {}
    high = 0.0
    for k, v in inc.items():
        key = str(k).lower()
        if any(t in key for t in ("$75", "$100", "$150", "$200", "$250",
                                  "75-", "100-", "150-", "200+", "250+")):
            # Avoid double-counting narrow bands: keep only $75k+ patterns
            if ("75" in key or "100" in key or "150" in key or
                    "200" in key or "250" in key):
                high += _num(v)
    if high:
        out.append((
            f"{high:.1f}%",
            "INCOME $75K PLUS",
            ("Bifurcated commercial opportunities that run the gamut "
             "of household income levels."),
            "cream",
        ))

    return out[:4]


def _identity_headline(p: dict) -> str:
    """One-line demographic summary for the IDENTITY slide."""
    gender = (p.get("demographics") or {}).get("gender", {}) or {}
    fem = _num(gender.get("Female", 0))
    mal = _num(gender.get("Male", 0))
    parts: list[str] = []
    if fem >= 60:
        parts.append("Female.")
    elif mal >= 60:
        parts.append("Male.")
    else:
        parts.append("Balanced-gender.")
    # Age slant
    age = (p.get("demographics") or {}).get("age", {}) or {}
    prime = sum(_num(v) for k, v in age.items()
                if str(k).strip().replace("–", "-") in ("18-24", "25-34", "35-44"))
    if prime >= 50:
        parts.append("Prime-age.")
    # Ethnicity slant (any strong over-index other than White)
    for k in ("Black", "Black or African American", "Hispanic", "Hispanic or Latino",
              "Asian"):
        if _lookup_idx(p, "ethnicity", k) >= 140:
            parts.append("Multicultural.")
            break
    # Income slant
    inc = (p.get("demographics") or {}).get("income", {}) or {}
    high = sum(_num(v) for k, v in inc.items()
               if any(t in str(k) for t in ("$75", "$100", "$150", "$200", "$250")))
    if high >= 45:
        parts.append("Mid-income+.")
    return " ".join(parts) or "The audience, unfiltered."


def _identity_stat_rows(p: dict) -> list[tuple[str, str, float]]:
    """Return up to 4 (pct_str, label, idx_val) rows for the IDENTITY slide,
    prioritized by highest audience share within a category (top of each dim)."""
    out: list[tuple[str, str, float]] = []

    def _top(dim: str, dim_name: str):
        d = (p.get("demographics") or {}).get(dim, {}) or {}
        if not d:
            return None
        top_k = max(d.keys(), key=lambda k: _num(d[k]))
        top_v = _num(d[top_k])
        if not top_v:
            return None
        idx = _lookup_idx(p, dim, top_k)
        return (f"{top_v:.1f}%", f"{dim_name}: {top_k.upper()}", idx)

    for dim, name in [
        ("gender", "GENDER"),
        ("age", "AGE"),
        ("ethnicity", "ETHNICITY"),
        ("income", "INCOME"),
    ]:
        row = _top(dim, name)
        if row:
            out.append(row)
    return out[:4]


def _identity_why_it_matters(p: dict) -> str:
    subj = p["name"]
    idx_hits = [(k, _lookup_idx(p, "ethnicity", k))
                for k in ("Black or African American", "Hispanic or Latino", "Asian")
                if _lookup_idx(p, "ethnicity", k) >= 130]
    if idx_hits:
        top = max(idx_hits, key=lambda t: t[1])
        return (f"{subj}'s audience is anchored by strong over-representation "
                f"in {top[0]} ({int(top[1])} index) — a clear cultural anchor "
                "that shapes brand fit, media plan, and creative direction.")
    return (f"{subj}'s audience shape is an ideal fit for talent ventures, "
            "brand partnerships, and media alignment.")


def _final_headline(p: dict) -> tuple[str, str]:
    """Two-line editorial closer for the FINAL INSIGHT slide."""
    subj = p["name"]
    peak = _peak_index(p)
    if peak >= 300:
        return (f"When {subj} shows up,", "the audience reads it as taste.")
    if peak >= 150:
        return (f"{subj} isn't just an audience —",
                "it's a taste graph.")
    return (f"{subj} is a signal,", "not just a fanbase.")


def _final_body(p: dict, top) -> str:
    subj = p["name"]
    n_over = sum(1 for it in _all_behavioral_items(p)
                 if _num(it.get("index", 0)) >= 150)
    lines = [
        f"Across {n_over}+ behavioral signals, {subj}'s audience over-indexes "
        "150+ vs. the U.S. digital baseline. Not coincidence — a taste graph."
    ]
    if top:
        cat = _pretty_cat(top.get("_cat", ""))
        lines.append(
            f"The strongest single read is {top['name']} at "
            f"{int(_num(top['index']))} in {cat}. Any partner brand should "
            "read this as durable, addressable audience overlap.")
    return "  ".join(lines)


def _top_demo_index_callouts(p: dict, n: int = 4) -> list[tuple[float, str, str]]:
    """Rank demographic buckets by index vs Gen Pop, return top ``n`` for
    the KD 'Where the audience over-indexes most' slide.

    Each row: (index_value, big_label, sub_context)."""
    picks: list[tuple[float, str, str]] = []
    for dim, dim_lbl in [
        ("ethnicity", "ETHNICITY"),
        ("age", "AGE"),
        ("gender", "GENDER"),
        ("income", "INCOME"),
        ("education", "EDUCATION"),
    ]:
        d = (p.get("demographics") or {}).get(dim, {}) or {}
        for k, share in d.items():
            share_n = _num(share)
            if share_n < 5:  # skip fringe buckets
                continue
            idx = _lookup_idx(p, dim, k)
            if idx < 120:
                continue
            gp = 0.0
            if dim == "ethnicity":
                gp = _APPROX_GENPOP_ETH.get(k, 0.0)
            elif dim == "age":
                gp = _APPROX_GENPOP_AGE.get(str(k).replace("–", "-"), 0.0)
            elif dim == "gender":
                gp = _APPROX_GENPOP_GENDER.get(k, 0.0)
            sub = (f"{share_n:.1f}% of the audience"
                   + (f" vs {gp:.1f}% of Gen Pop" if gp else ""))
            picks.append((idx, f"{k.upper()}", sub))
    picks.sort(key=lambda t: -t[0])
    return picks[:n]


def _top_dmas_ranked(p: dict, n: int = 10) -> list[tuple[str, float, float]]:
    """Return top DMAs from ``p['locations']`` sorted by audience share,
    with (name, pct, idx). Handles both list-of-dicts and dict shapes."""
    locs = p.get("locations") or []
    rows: list[tuple[str, float, float]] = []
    if isinstance(locs, list):
        for item in locs:
            if isinstance(item, dict):
                name = (item.get("name") or item.get("dma")
                        or item.get("market") or "").strip()
                pct = _num(item.get("pct") or item.get("percent")
                           or item.get("share"))
                idx = _num(item.get("index"))
                if name and pct:
                    rows.append((name, pct, idx))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                name = str(item[0]).strip()
                pct = _num(item[1])
                idx = _num(item[2]) if len(item) > 2 else 0
                if name and pct:
                    rows.append((name, pct, idx))
    elif isinstance(locs, dict):
        for k, v in locs.items():
            pct = _num(v.get("pct") if isinstance(v, dict) else v)
            idx = _num(v.get("index") if isinstance(v, dict) else 0)
            if k and pct:
                rows.append((str(k).strip(), pct, idx))
    rows.sort(key=lambda r: -r[1])
    return rows[:n]


def _why_it_matters_for_category(cat: str, p: dict, top: list) -> str:
    """One-liner narrative for the category slide's WHY IT MATTERS strip."""
    if not top:
        return "No standout brand signal yet in this category."
    lead = top[0]
    name = lead.get("name", "").strip()
    idx = int(_num(lead.get("index", 0)))
    pct = _num(lead.get("pct", 0))
    cat_pretty = _pretty_cat(cat)
    if idx >= 200:
        return (f"{name} anchors this category at a {idx} index — an unusually "
                f"tight signal for {cat_pretty.lower()}. Any activation here "
                "reaches an audience that already opts in.")
    if idx >= 130:
        return (f"{name} leads with {pct:.1f}% penetration ({idx} index) — a "
                f"reliable {cat_pretty.lower()} beachhead.")
    return (f"{p['name']}'s audience distributes across mainstream "
            f"{cat_pretty.lower()} choices; scale is present, differentiation is soft.")


def _sub_section_headers(cat: str, n_cols: int) -> list[str]:
    """Return sub-section header labels for the multi-column persona-category
    slide. Some categories have well-known conceptual splits (KD banking
    slide split into 'DIGITAL BANKING & PAY', 'BANKING', 'INVESTMENTS',
    'INTEREST SIGNAL'). For unknown cats we render generic tier labels."""
    cat_key = cat.upper().replace("_", " ").replace("/", " / ")
    known = {
        "BANKING": ["DIGITAL BANKING & PAY", "BANKING", "INVESTMENTS",
                    "INTEREST SIGNAL"],
        "STREAMING": ["STREAMING / VIDEO", "SOCIAL", "SEARCH & AI",
                      "SPORTS-MEDIA NOTABLES"],
        "SOCIAL MEDIA": ["SOCIAL PLATFORMS", "MESSAGING", "VIDEO NATIVE",
                         "EMERGING"],
        "QSR": ["THE LEADERS", "CHICKEN / SPECIALTY", "DELIVERY APPS",
                "FAST CASUAL"],
        "RETAIL": ["MASS RETAIL", "SPORTS SPECIALTY", "SNEAKER RESALE",
                   "PREMIUM"],
        "APPAREL / FOOTWEAR": ["FOOTWEAR", "APPAREL", "PREMIUM & LUXURY",
                               "STREETWEAR"],
        "MPB": ["TOP 5", "6-10", "11-15", "16-20"],
        "MOST PURCHASED BRANDS": ["TOP 5", "6-10", "11-15", "16-20"],
        "TRAVEL": ["AIRLINES", "HOTELS", "OTA / BOOKING", "EXPERIENCES"],
        "AUTOMOBILE": ["MAINSTREAM", "PREMIUM", "TRUCK / UTILITY", "EV"],
        "TALENT / ACTOR": ["A-LIST", "RISING", "COMEDY", "DRAMA"],
    }
    labels = known.get(cat_key)
    if labels:
        return labels[:n_cols]
    return [f"TIER {i+1:02d}" for i in range(n_cols)]


def _pick_case_studies(p: dict, n: int = 3) -> list[dict]:
    """Pick top ``n`` distinct brand case studies — each becomes one slide.

    Selection prioritises brand recognizability (skip weird SKU-ish names)
    and spread across categories so we don't do 3 case studies all from the
    same category. Each returned dict has the shape the slide builder needs."""
    all_items = _all_behavioral_items(p)
    all_items = [it for it in all_items
                 if _num(it.get("index", 0)) >= 130
                 and _num(it.get("pct", 0)) >= 5.0
                 and len((it.get("name") or "").strip()) >= 2]
    # Sort by index desc, then pct desc
    all_items.sort(key=lambda it: (-_num(it.get("index", 0)),
                                   -_num(it.get("pct", 0))))
    picked: list[dict] = []
    seen_cats: set[str] = set()
    for it in all_items:
        if len(picked) >= n:
            break
        cat = (it.get("_cat") or "").upper()
        if cat in seen_cats and len(picked) >= 1:
            continue
        brand = (it.get("name") or "").strip()
        pct = _num(it.get("pct", 0))
        idx = int(_num(it.get("index", 0)))
        gp = _num(it.get("genPopPct", 0))
        proj = _num(it.get("projection", 0))
        if not gp and idx:
            gp = round(pct / (idx / 100), 1) if idx else 0

        # Compose a KD-style deal narrative — generic since we don't know
        # if the brand has a real partnership. Frames as taste evidence.
        narrative = (
            f"{p['name']}'s audience over-indexes {idx}x vs the national "
            f"baseline on {brand}: {pct:.1f}% engage in-window, versus "
            f"{gp:.1f}% of the general population. That gap isn't statistical "
            "noise — it's the audience's taste graph made visible.\n\n"
            f"For a brand looking to activate the {p['name']} fanbase, "
            f"{brand} is the ready-made co-sign — the endorsement they read "
            "as authentic, not as an ad."
        )

        # 3 stat callouts
        stats = [
            (f"{brand.upper()} INDEX", f"{idx}",
             f"vs Gen Pop"),
            ("% PENETRATION", f"{pct:.1f}%",
             (f"vs {gp:.1f}% Gen. Pop." if gp else "of the audience")),
            ("AUDIENCE REACH", (_fmt_int_compact(proj) or "—"),
             "U.S. digital adults engaged"),
        ]
        # Editorial headline
        if idx >= 250:
            ha, hb = f"All-in on", f"{brand}."
        elif idx >= 180:
            ha, hb = f"Wired to buy", f"{brand}."
        elif idx >= 140:
            ha, hb = f"They come pre-sold", f"on {brand}."
        else:
            ha, hb = f"Meaningful lift", f"for {brand}."

        picked.append({
            "name": brand, "category": cat,
            "pct": pct, "index": idx, "gen_pop_pct": gp,
            "projection": proj,
            "narrative": narrative,
            "headline_a": ha, "headline_b": hb,
            "stats": stats,
        })
        seen_cats.add(cat)
    return picked


def _mpb_top_brands(p: dict, n: int = 20) -> list[dict]:
    """Return top MPB brands. Prefers the 'MPB' or 'MOST PURCHASED BRANDS'
    category; falls back to a flat top-N across all behavioral items."""
    beh = p.get("behavioral") or {}
    for key in ("MPB", "MOST PURCHASED BRANDS", "MOST_PURCHASED_BRANDS",
                "Most Purchased Brands"):
        if key in beh and beh[key]:
            items = list(beh[key])
            items.sort(key=lambda it: -_num(it.get("pct", 0)))
            return items[:n]
    # Fallback: all-cat flat rank by pct
    flat = [it for it in _all_behavioral_items(p) if _num(it.get("pct", 0)) > 0]
    flat.sort(key=lambda it: -_num(it.get("pct", 0)))
    return flat[:n]


def _pick_takeaways(p: dict) -> list[tuple[str, str, str]]:
    """Return up to 6 (number, title, body) tuples for the KEY TAKEAWAYS
    slide, built dynamically from the strongest signals in the payload."""
    subj = p["name"]
    proj = p["projected_us"] or 0
    sample = p["sample_size"] or 0
    demos = p.get("demographics") or {}
    top_brand = _top_over_index_brand(p)
    picks: list[tuple[str, str, str]] = []

    # 01: Scale + demography quick-hit
    facts = []
    if proj:
        facts.append(f"{_fmt_int_compact(proj)} US digital fans")
    gender = demos.get("gender", {})
    for k in ("Male", "Female"):
        if _num(gender.get(k, 0)) >= 55:
            facts.append(f"{_num(gender[k]):.1f}% {k.lower()}")
    prime = sum(_num(v) for k, v in demos.get("age", {}).items()
                if str(k).replace("–", "-") in ("18-24", "25-34", "35-44"))
    if prime:
        facts.append(f"{prime:.1f}% 18-44")
    for k in ("Black", "Black or African American", "Hispanic", "Hispanic or Latino"):
        idx = _lookup_idx(p, "ethnicity", k)
        if idx >= 130:
            facts.append(f"{_num(demos.get('ethnicity', {}).get(k, 0)):.1f}% {k} ({int(idx)})")
            break
    picks.append(("01", "Ideal demography.",
                  "Unprecedented reach & resonance.\n\nFacts:\n- "
                  + "\n- ".join(facts[:5])))

    # 02: Geography (only if we have location data)
    dmas = _top_dmas_ranked(p, n=3)
    if dmas:
        dma_lines = [f"{name}: {pct:.1f}% ({int(idx)})" if idx
                     else f"{name}: {pct:.1f}%"
                     for name, pct, idx in dmas]
        picks.append(("02", "Big-market reach.",
                      "Over-indexes across major US metros:\n- "
                      + "\n- ".join(dma_lines)))

    # 03: Live events (ticketing / festival category if present)
    live = _first_available(p, ["TICKETING PLATFORM", "TICKETING PLATFORMS",
                                "FESTIVAL", "FESTIVALS", "LIVE EVENTS",
                                "EVENTS"])
    if live:
        top3 = live[:3]
        picks.append(("03", "They show up.",
                      f"Live event and ticketing behavior tracks "
                      f"~{int(_num(top3[0].get('index', 0)) / 100)}x national baseline.\n\n"
                      + "\n- ".join([""] + [f"{it['name']}: {_num(it.get('pct',0)):.1f}% ({int(_num(it.get('index',0)))})"
                                            for it in top3])))

    # 04: Media & digital
    media = _first_available(p, ["STREAMING/PLATFORM", "STREAMING VIDEO",
                                 "MEDIA", "SOCIAL MEDIA"])
    if media:
        top3 = media[:3]
        picks.append(("04", "Media-native.",
                      "Where the audience actually spends screen time:\n- "
                      + "\n- ".join([f"{it['name']}: {_num(it.get('pct',0)):.1f}%"
                                     f" ({int(_num(it.get('index',0)))})"
                                     for it in top3])))

    # 05: Financial (if present)
    fin = _first_available(p, ["BANKING", "DIGITAL BANKING", "INVESTMENTS",
                               "CREDIT PROVIDER"])
    if fin:
        top3 = fin[:3]
        picks.append(("05", "Financial footprint.",
                      "Money stack the audience actually uses:\n- "
                      + "\n- ".join([f"{it['name']}: {_num(it.get('pct',0)):.1f}%"
                                     f" ({int(_num(it.get('index',0)))})"
                                     for it in top3])))

    # 06: Top brand as the proof point
    if top_brand:
        picks.append(("06", "The proof is repeatable.",
                      f"The strongest single signal in the file is "
                      f"{top_brand['name']} at {int(_num(top_brand['index']))} "
                      f"index. That's the taste anchor any brand partnership "
                      f"should key off of.\n\n{subj} delivers."))

    # Renumber 01..N in order actually produced
    picks = [(f"{i+1:02d}", t, b) for i, (_, t, b) in enumerate(picks)][:6]
    return picks


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
