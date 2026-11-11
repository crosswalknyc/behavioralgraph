"""Crosswalk insights-deck renderer for Prometheus (2026-08-26).

Generalizes the reference talent-value deck implementation (the Paige
Bueckers audience-value deck) into a reusable renderer: a slide-plan
JSON in, a finished client-ready PPTX out. Every surface follows the
Crosswalk deck system exactly: Graphite Teal / Neutral Off-White
grounds, Inter everywhere (Geist Mono only on raw clickstream rows),
one accent per surface, 0.920in margins, eyebrow dot + logo chrome,
sentence-case headlines that end in a full stop.

Slide-type vocabulary (the planner picks per slide):
  cover            dark opener: headline, intro, three proof stats
  argument         2x2 numbered cards (dark slate or light card)
  tiles_facts      3-4 stat tiles + up to 6 fact rows below
  bars             ranked bar rows, optional PEN./INDEX columns, read line
  split_stats_bars two stat cards left + labeled bar list right + read
  tiles_row        three tall stat tiles with body copy + read line
  hero             full-bleed Orchid (or dark) single-figure moment
  table            column table with header hairline + read card(s)
  hero_proof       dark: 82pt figure left, three proof cards right
  paths            dark: Geist Mono clickstream rows, lit rows on slate
  close            dark 2x2 numbered next-step cards

The renderer is tolerant: unknown fields are ignored, list lengths are
clamped, missing optionals degrade to a clean layout rather than an
error. Text content is expected to be pre-scrubbed by the caller
(prometheus_analysis.enforce_insights_plan).
"""
from __future__ import annotations

import copy
import os
import re

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# ---- Crosswalk palette (deck-system.md) ------------------------------------
GRAPHITE = RGBColor(0x0C, 0x16, 0x18)
SLATE = RGBColor(0x15, 0x25, 0x2A)
OFFWHITE = RGBColor(0xE9, 0xE8, 0xE1)
CARD = RGBColor(0xE1, 0xE0, 0xD7)
SIGNAL = RGBColor(0xC7, 0xF2, 0x3E)
OLIVE = RGBColor(0x5E, 0x7E, 0x12)
ORCHID = RGBColor(0xE6, 0x82, 0xFF)
AMETHYST = RGBColor(0x8E, 0x3F, 0xA8)
WHITE = RGBColor(0xE9, 0xE8, 0xE1)
BODY_DK = RGBColor(0x9A, 0xA0, 0x9B)
MUTED_DK = RGBColor(0x5C, 0x64, 0x66)
BODY_LT = RGBColor(0x5C, 0x65, 0x60)
MUTED_LT = RGBColor(0x88, 0x8C, 0x89)
FOOTER = RGBColor(0x5C, 0x64, 0x66)
TRACK = RGBColor(0xC9, 0xC6, 0xBA)
TRACK_DK = RGBColor(0x3B, 0x3D, 0x38)
ORCHID_BODY = RGBColor(0xF4, 0xE4, 0xFA)
ORCHID_MUTED = RGBColor(0x5C, 0x2A, 0x6E)
ORCHID_FOOT = RGBColor(0x4A, 0x24, 0x58)

SW, SH = Inches(13.333), Inches(7.500)
M, BAND, GUT = Inches(0.920), Inches(11.493), Inches(0.280)
# the eyebrow may grow toward the lockup (x 11.083 in) keeping the 0.280 gutter
EYEBROW_MAX_W = Inches(11.083 - 0.280 - 1.129)
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _face(run, name):
    rPr = run._r.get_or_add_rPr()
    for tag in (A_NS + "latin", A_NS + "ea", A_NS + "cs"):
        el = rPr.find(tag)
        if el is None:
            el = etree.SubElement(rPr, tag)
        el.set("typeface", name)


def _spc(run, hundredths):
    run._r.get_or_add_rPr().set("spc", str(int(hundredths)))


# ---- text measurement (2026-09-30: no overlapping text) --------------------
# Jenna: "the deck prometheus made has overlapping text: this is a
# common issue." Every stacked element now measures the text above it
# and flows below it, and oversize headlines step down a size until
# they fit their band. Widths come from the real Inter metrics when
# reportlab is present (Render and the build server both carry it);
# the fallback deliberately overestimates so layout errs roomy.

_FONT_REG = {"done": False, "ok": False}


def _design():
    """The crosswalk-design pack resolver (2026-10-21: fonts, tokens,
    templates and the finishing checks come from the locked skill)."""
    try:
        from migration import design_skill as _ds
        return _ds
    except Exception:
        return None


def _font_file(weight):
    """The TTF for a weight: Crosswalk Inter from the design pack, the
    legacy Inter 18pt file only when the pack is absent on this machine.
    PROMETHEUS_PDF_FONT_DIR still wins when set (ops override)."""
    env = os.environ.get("PROMETHEUS_PDF_FONT_DIR")
    if env:
        for stem in ("CrosswalkInter", "Inter_18pt"):
            cand = os.path.join(env, f"{stem}-{weight}.ttf")
            if os.path.exists(cand):
                return cand
    ds = _design()
    if ds is not None:
        p = ds.font_path(weight)
        if p:
            return str(p)
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(here, "static", "fonts"),
              os.path.join(os.path.dirname(here), ".cursor", "skills",
                           "crosswalk-brand-standards", "assets", "fonts")):
        cand = os.path.join(d, f"Inter_18pt-{weight}.ttf")
        if os.path.exists(cand):
            return cand
    return None


def _font_dir():
    f = _font_file("Regular")
    return os.path.dirname(f) if f else None


def _ensure_fonts():
    if _FONT_REG["done"]:
        return _FONT_REG["ok"]
    _FONT_REG["done"] = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        reg, bold = _font_file("Regular"), _font_file("Bold")
        if not reg or not bold:
            return False
        pdfmetrics.registerFont(TTFont("CWDeckR", reg))
        pdfmetrics.registerFont(TTFont("CWDeckB", bold))
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
    """Greedy-wrap line count for text in a box width_in wide. A pair
    bound with a non-breaking space (the widow fix) is one word here, as
    it is to every renderer."""
    total = 0
    for seg in str(text or "").split("\n"):
        words = [w for w in seg.split(" ") if w.strip()]
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
    return _line_count(text, size, width_in, bold) * size \
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


# ---- widows (2026-11-10) ----------------------------------------------------
# The Las Culturistas deck reached a Lionsgate seat with twelve single words
# alone on a card's last line. Every paragraph is now measured against its
# box with the pack's own wrap model and font metrics (the model the brand
# lint checks with); when the last line would carry one word, the last two
# words are bound with a non-breaking space so they wrap together, and the
# box is widened (when the caller has room) or narrowed a little so the
# check sees two words there as well. Never a line break, never shortened
# copy. Geist Mono rows and chrome under 9pt are left alone.

def _widow_proof(text, x, w, h, size, bold, face, spc, align, max_w):
    """(text, x, w, size) for one text box, widow-proofed."""
    ds = _design()
    s = "" if text is None else str(text)
    if ds is None or face == "Geist Mono" or not s.strip():
        return s, x, w, size
    try:
        w_in = float(w) / 914400.0
        h_in = float(h) / 914400.0 if h else None
        tr = (float(spc) / 100.0) if spc else 0.0
        if "\n" in s:
            parts = []
            for part in s.split("\n"):
                fit = ds.widow_fit(part, size, w_in, face=face, bold=bold, tracking_pt=tr)
                parts.append(fit.text if fit.action != "none" else part)
            return "\n".join(parts), x, w, size
        max_w_in = (float(max_w) / 914400.0) if max_w else None
        fit = ds.widow_fit(s, size, w_in, height_in=h_in, face=face, bold=bold,
                           tracking_pt=tr, max_width_in=max_w_in)
        if fit.action == "none":
            return s, x, w, size
        new_w = w
        if abs(fit.width_in - w_in) > 1e-6:
            new_w = Emu(int(round(fit.width_in * 914400)))
            if align == PP_ALIGN.RIGHT:
                x = Emu(int(x) + (int(w) - int(new_w)))
            elif align == PP_ALIGN.CENTER:
                x = Emu(int(x) + (int(w) - int(new_w)) // 2)
        return fit.text, x, new_w, fit.size_pt
    except Exception:
        return s, x, w, size


def txt(s, x, y, w, h, text, *, size=12, bold=False, color=GRAPHITE,
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
        font="Crosswalk Inter", spc=None, light=False, max_w=None):
    face = "Crosswalk Inter Light" if light else font
    try:
        from migration.design_skill import scrub_door_metaphor
        text = scrub_door_metaphor(text)
    except Exception:
        text = text
    text, x, w, size = _widow_proof(text, x, w, h, size, bold, face, spc,
                                    align, max_w)
    box = s.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        r.font.name = face
        _face(r, face)
        if spc is not None:
            _spc(r, spc)
    return box


def slide_number(s, x, y, w, h, page, *, size=8, color=GRAPHITE, font="Crosswalk Inter"):
    """The page number as a slide-number field (the design pack fails a
    typed one): PowerPoint keeps it right when pages move."""
    box = s.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.RIGHT
    hex6 = "%02X%02X%02X" % (color[0], color[1], color[2])
    fld = etree.SubElement(para._p, A_NS + "fld")
    fld.set("id", "{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}")
    fld.set("type", "slidenum")
    rPr = etree.SubElement(fld, A_NS + "rPr")
    rPr.set("lang", "en-US")
    rPr.set("sz", str(int(size * 100)))
    rPr.set("b", "0")
    sf = etree.SubElement(rPr, A_NS + "solidFill")
    etree.SubElement(sf, A_NS + "srgbClr").set("val", hex6)
    for tag in ("latin", "ea", "cs"):
        etree.SubElement(rPr, A_NS + tag).set("typeface", font)
    etree.SubElement(fld, A_NS + "t").text = str(page)
    return box


def rect(s, x, y, w, h, fill):
    sh = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def rrect(s, x, y, w, h, fill, radius=0.14):
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    shorter = min(int(w), int(h))
    half = max(shorter / 2, 1)
    sh.adjustments[0] = min(0.5, Inches(radius) / half)
    sh.shadow.inherit = False
    return sh


def dot(s, x, y, fill=SIGNAL):
    sh = s.shapes.add_shape(MSO_SHAPE.OVAL, x, y,
                            Inches(0.092), Inches(0.092))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    return sh




# ---------------- Deck photography (2026-09-30, Jenna) -------------
# "when building decks can prometheus find cool images to put into
# the decks to mirror how they look in the designed decks we do
# manually". Full-bleed photos under a graphite scrim on the cover,
# the first big statement page, and the close - the manual deck
# grammar. Sourced through the standing image resolver
# (image_backfill: IMDb, Wikipedia, web image search). Fail-safe by
# design: any miss renders the slide text-only exactly as before.

_PHOTO_CACHE = {}
_PHOTO_SOURCES = {}   # subject -> the candidate photo urls, for the job trail
_PHOTO_MIN_W = 640
_PHOTO_MIN_H = 420


def _photos_enabled():
    return os.environ.get('PROMETHEUS_DECK_PHOTOS', '1').strip().lower() \
        not in ('0', 'false', 'no')


def _blob_px(blob):
    try:
        from pptx.parts.image import Image as _PImg
        return _PImg.from_blob(blob).size
    except Exception:
        return (0, 0)


_PHOTO_EXTS = ('jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff')


def _blob_embeddable(blob):
    """True when python-pptx can embed the image (WEBP and friends
    cannot ship in a PPTX)."""
    try:
        from pptx.parts.image import Image as _PImg
        img = _PImg.from_blob(blob)
        return (str(img.ext or '').lower() in _PHOTO_EXTS
                and img.size[0] >= _PHOTO_MIN_W
                and img.size[1] >= _PHOTO_MIN_H)
    except Exception:
        return False


def _fetch_photos(subject, kind, max_n):
    """Candidate photo bytes for the deck subject, best sources
    first. Never raises."""
    import re as _re
    import urllib.parse as _up
    try:
        import image_backfill as ib
    except Exception:
        return []
    master = {'person': 'TALENT', 'title': 'CONTENT'}.get(kind, 'BRAND')
    urls = []
    try:
        u, _src = ib.resolve_image_url(subject, master)
        if u:
            urls.append(u)
    except Exception:
        pass
    try:
        u = ib.wiki_lookup(subject)
        if u:
            urls.append(u)
    except Exception:
        pass
    # Open-web results must carry the subject in the address (host or
    # path). A generic query returns stock art of anything; a Starz
    # deck opened on a Microsoft Teams graphic (2026-10-07, Bria).
    subj_toks = [w for w in _re.sub(r'[^a-z0-9]+', ' ', subject.lower()).split()
                 if len(w) >= 4]
    try:
        data, _ct = ib._http_get(
            ib.BING_IMAGES_URL.format(q=_up.quote(f"{subject} photo")),
            ib.HEADERS_JSON, timeout=8.0)
        html = data.decode('utf-8', 'ignore') if data else ''
        for raw in _re.findall(
                r'&quot;murl&quot;:&quot;([^&"]+)&quot;', html):
            u = raw.replace('\\/', '/').strip()
            low = u.lower().split('?', 1)[0]
            if not low.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            if subj_toks and not any(t in low for t in subj_toks):
                continue
            urls.append(u)
            if len(urls) >= max_n + 5:
                break
    except Exception:
        pass
    _PHOTO_SOURCES[subject.lower()] = [u[:160] for u in urls[:max_n + 2]]
    seen, blobs = set(), []
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        try:
            got = ib.download_image(u)
        except Exception:
            got = None
        if not got:
            continue
        blob = got[0] if isinstance(got, tuple) else got
        if not blob:
            continue
        if _blob_embeddable(blob):
            blobs.append(bytes(blob))
        if len(blobs) >= max_n:
            break
    return blobs


_PERSON_CATS = {'ACTOR', 'ATHLETE', 'BUSINESS/TECH LEADERS', 'COMEDIAN', 'INFLUENCER/CREATOR', 'CREATOR/INFLUENCER',
                'EMERGING TALENT', 'HOST/PERSONALITY', 'MUSICIAN/BAND', 'PODCASTER',
                'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST'}
_TITLE_CATS = {'MOVIE', 'PODCAST', 'GAMES', 'VERTICAL SHORTS', 'GAME PLAYERS'}


def photo_kind_for_category(brand_category):
    """person / title / brand from a profile's BRAND CATEGORY, so a
    Starz deck looks for a brand image and an actor deck for a person
    (2026-10-07)."""
    cat = str(brand_category or '').strip().upper()
    if cat in _PERSON_CATS:
        return 'person'
    if cat.startswith('SERIES') or cat in _TITLE_CATS:
        return 'title'
    return 'brand'


def _deck_photos(subject, kind='person', max_n=3):
    """Up to max_n photo blobs for the subject, cached per process.
    Empty when photos are disabled, unresolvable, or too small."""
    subject = str(subject or '').strip()
    if not subject:
        return []
    key = f"{subject.lower()}|{str(kind or 'person').lower()}"
    if key in _PHOTO_CACHE:
        return list(_PHOTO_CACHE[key])
    blobs = []
    if _photos_enabled():
        try:
            blobs = _fetch_photos(subject, kind, max_n)
        except Exception:
            blobs = []
    _PHOTO_CACHE[key] = list(blobs)
    return list(blobs)


def _add_photo_fill(s, blob, x, y, w, h):
    """Place a photo to exactly fill the target box: stretch to the
    box, then crop the source so the aspect holds (cover-fill, never
    distorted)."""
    import io as _io
    pic = s.shapes.add_picture(_io.BytesIO(blob), x, y,
                               width=w, height=h)
    try:
        iw, ih = pic.image.size
        if iw and ih:
            tgt = float(w) / float(h)
            src = float(iw) / float(ih)
            if src > tgt:
                frac = 1.0 - (tgt / src)
                pic.crop_left = frac / 2
                pic.crop_right = frac / 2
            elif src < tgt:
                # Portrait into a landscape box: bias the window
                # toward the top of the image so faces stay in frame.
                frac = 1.0 - (src / tgt)
                pic.crop_top = frac * 0.22
                pic.crop_bottom = frac * 0.78
    except Exception:
        pass
    return pic


def _photo_moment(s, blob, alpha_pct):
    """Full-bleed photo under the graphite scrim, as the slide BACKGROUND
    (2026-11-10): the photo is cover-cropped to the page at 3840 x 2160
    and the scrim is baked into the pixels, so no full-page shape sits
    under the content and no image is low resolution (the design pack:
    set the image as the slide background; scrim type over photographs,
    baked in). A bad blob renders the slide text-only instead of
    failing the deck."""
    ds = _design()
    if ds is None:
        return False
    try:
        baked = ds.bake_photo_background(
            blob, scrim_hex="#%02X%02X%02X" % tuple(GRAPHITE),
            alpha=int(alpha_pct) / 100.0)
        ds.set_slide_background_image(s, baked)
    except Exception:
        return False
    return True


def _page_gradient(s, name="graphiteWash"):
    """The pack's gradient at full resolution as the slide background
    (the dark ground of the template's covers), when a cover or close
    carries no photo. Falls back to the solid page color silently."""
    ds = _design()
    if ds is None:
        return False
    try:
        data = ds.gradient_image_bytes(name)
        if not data:
            return False
        ds.set_slide_background_image(s, data)
        return True
    except Exception:
        return False


class _Deck:
    """Holds presentation-wide state: logo paths and page counter."""

    def __init__(self, prs, logo_white, logo_black):
        self.prs = prs
        self.logo_white = logo_white
        self.logo_black = logo_black
        self.page = 0
        self.content_top = 2.330

    def logo(self, s, white=True):
        path = self.logo_white if white else self.logo_black
        if path and os.path.exists(path):
            s.shapes.add_picture(str(path), Inches(11.083), Inches(0.558),
                                 width=Inches(1.330))

    def new_slide(self, dark=False, fill=None):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        # the page color is the slide background, never a full-page shape
        # (the design pack, 2026-10-21)
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = fill or (GRAPHITE if dark else OFFWHITE)
        self.page += 1
        self.content_top = 2.330
        return s

    def chrome(self, s, eyebrow, title, *, dark=False, sub=None,
               source=None, orchid=False):
        ink = WHITE if (dark or orchid) else GRAPHITE
        body = BODY_DK if dark else BODY_LT
        muted = MUTED_DK if dark else MUTED_LT
        if orchid:
            body = ORCHID_BODY
            muted = ORCHID_MUTED
            ink = WHITE
        page = self.page
        eb = f"{page:02d}  {eyebrow}" if eyebrow else f"{page:02d}"
        dot(s, M, Inches(0.540))
        txt(s, Inches(1.129), Inches(0.540), Inches(9.4), Inches(0.22),
            str(eb).upper(), size=10.5, bold=True, color=body, spc=260,
            max_w=EYEBROW_MAX_W)
        self.logo(s, white=dark or orchid)
        band_in = float(BAND) / 914400.0
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
        self.content_top = content_top
        if source:
            txt(s, M, Inches(6.620), BAND, Inches(0.28), source,
                size=9, color=muted)
        foot = ("CONFIDENTIAL" if page == 1
                else "CROSSWALK / BEHAVIORAL INTELLIGENCE ENGINE")
        foot_c = FOOTER if not orchid else ORCHID_FOOT
        txt(s, M, Inches(7.080), Inches(8.2), Inches(0.22),
            foot, size=8, color=foot_c, spc=160 if page == 1 else 200)
        slide_number(s, Inches(10.183), Inches(7.080), Inches(2.230), Inches(0.22),
                     page, size=8, color=foot_c)


def _bar(s, x, y, track_w, frac, *, accent=False, h=0.17, dark=False):
    track = TRACK_DK if dark else TRACK
    fill = ((ORCHID if accent else SIGNAL) if dark
            else (AMETHYST if accent else OLIVE))
    rrect(s, x, y, track_w, Inches(h), track, radius=0.08)
    frac = min(max(_num(frac, 0.01), 0.01), 1.0)
    bw = max(int(int(track_w) * frac), 8)
    rrect(s, x, y, Emu(bw), Inches(h), fill, radius=0.08)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _fmt_val(v, suffix):
    """Bar value display: keep the model's precision, add the suffix."""
    n = _num(v)
    if n == int(n) and abs(n) >= 10 and suffix != "x":
        disp = f"{int(n):,}"
    else:
        disp = f"{n:g}"
    return f"{disp}{suffix}" if suffix else disp


def _items(sl, key, cap):
    out = [i for i in (sl.get(key) or []) if isinstance(i, dict)]
    return out[:cap]


# ---- slide renderers --------------------------------------------------------

def _sl_cover(d, sl):
    s = d.new_slide(dark=True)
    if not (sl.get("_photo") and _photo_moment(s, sl["_photo"], 64)):
        _page_gradient(s, "graphiteWash")
    dot(s, M, Inches(0.540))
    txt(s, Inches(1.129), Inches(0.540), Inches(9.4), Inches(0.22),
        str(sl.get("eyebrow") or "CROSSWALK").upper(),
        size=10.5, bold=True, color=BODY_DK, spc=260, max_w=EYEBROW_MAX_W)
    d.logo(s, True)
    band_in = float(BAND) / 914400.0
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
            sl["intro"], size=16, color=BODY_DK)
    stats = _items(sl, "stats", 3)
    acc = sl.get("accent_index")
    x, w = M, Inches(3.644)
    for i, st in enumerate(stats):
        color = SIGNAL if (isinstance(acc, int) and i == acc) else WHITE
        txt(s, x, Inches(5.90), w, Inches(0.46),
            st.get("big") or "", size=22, bold=True, color=color)
        txt(s, x, Inches(6.40), w, Inches(0.55),
            st.get("label") or "", size=11.5, color=BODY_DK)
        x += w + GUT
    txt(s, M, Inches(7.080), Inches(8.2), Inches(0.22),
        "CONFIDENTIAL", size=8, color=FOOTER, spc=160)
    slide_number(s, Inches(10.183), Inches(7.080), Inches(2.230), Inches(0.22),
                 d.page, size=8, color=FOOTER)


def _cards_2x2(d, s, cards, dark, read=None):
    top = d.content_top
    cards = cards[:4]
    # A card that is a group of brands carries its category reach and
    # index (2026-11-11, Armine: "the overall percentage and index for
    # each category"). The figures come from the shared rollup
    # (migration.category_rollup via prometheus.category_reach); the
    # renderer only places them. All four cards or none, so the boxes in
    # the row match.
    figs = bool(cards) and all(str(c.get("reach") or "").strip()
                               for c in cards)
    cw, ch_in = Inches(5.607), 1.82
    rows = (len(cards) + 1) // 2
    band_in = float(BAND) / 914400.0
    read_h = _block_h(read, 11.5, band_in) if (figs and read) else 0.0
    for i, c in enumerate(cards):
        x = M + (i % 2) * (cw + GUT)
        y = top + (i // 2) * (ch_in + 0.22)
        rrect(s, x, Inches(y), cw, Inches(ch_in),
              SLATE if dark else CARD)
        if figs:
            _reach_card(s, x, y, ch_in, c, dark)
            continue
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
            size=body_sz, color=BODY_DK if dark else BODY_LT)
    if read_h:
        y_end = top + rows * ch_in + (rows - 1) * 0.22
        txt(s, M, Inches(y_end + 0.24), BAND, Inches(read_h), read,
            size=11.5, color=BODY_DK if dark else BODY_LT)


# the figure column of a group-of-brands card, and the copy column beside it
_REACH_COL_W = 1.78
_REACH_COPY_X = 0.26 + _REACH_COL_W + 0.20
_REACH_COPY_W = 5.607 - _REACH_COPY_X - 0.26


def _reach_card(s, x, y, ch_in, c, dark):
    """One group-of-brands card: the share of the audience with at least
    one brand in the group (the one accent), the index and its plain
    comparison under it, the group's name and copy beside them."""
    ink = WHITE if dark else GRAPHITE
    body = BODY_DK if dark else BODY_LT
    fx = x + Inches(0.26)
    txt(s, fx, Inches(y + 0.22), Inches(_REACH_COL_W), Inches(0.46),
        str(c.get("reach") or ""), size=26, bold=True,
        color=SIGNAL if dark else OLIVE)
    idx = c.get("index")
    if idx not in (None, ""):
        txt(s, fx, Inches(y + 0.76), Inches(_REACH_COL_W), Inches(0.24),
            f"Index {int(_num(idx))}", size=11.5, bold=True, color=ink)
    if c.get("times"):
        txt(s, fx, Inches(y + 1.02), Inches(_REACH_COL_W), Inches(0.40),
            str(c.get("times")), size=10, color=body)
    cx = x + Inches(_REACH_COPY_X)
    head_h = max(0.36, _block_h(c.get("head") or "", 14, _REACH_COPY_W,
                                bold=True))
    txt(s, cx, Inches(y + 0.28), Inches(_REACH_COPY_W), Inches(head_h),
        c.get("head") or "", size=14, bold=True, color=ink)
    body_top = max(0.68, 0.28 + head_h + 0.08)
    room = max(0.28, ch_in - body_top - 0.12)
    body_txt, body_sz = _fit_body(c.get("body") or "", (11.5, 10.5, 10),
                                  _REACH_COPY_W, room)
    txt(s, cx, Inches(y + body_top), Inches(_REACH_COPY_W), Inches(room),
        body_txt, size=body_sz, color=body)


def _chrome_top(title, sub):
    """Where the content band will start under this title and subhead
    (the same arithmetic _Deck.chrome uses), so a slide can decide its
    layout before the chrome is drawn."""
    band_in = float(BAND) / 914400.0
    t_size = _fit_size(str(title or ""), (34, 31, 28, 26), band_in, 2)
    t_h = max(0.50, _block_h(title or "", t_size, band_in, bold=True))
    top = max(2.330, 1.050 + t_h + 0.30)
    if sub:
        sub_y = max(1.846, 1.050 + t_h + 0.14)
        s_h = max(0.30, _block_h(sub, 16, band_in))
        top = max(2.330, sub_y + s_h + 0.22)
        if _line_count(sub, 16, band_in) >= 2:
            top = max(top, 2.750)
    return top


def _sl_argument(d, sl):
    dark = str(sl.get("ground") or "dark").lower() != "light"
    s = d.new_slide(dark=dark)
    cards = _items(sl, "cards", 4)
    sub, read = sl.get("sub"), None
    if sl.get("_reach") and sl.get("read") and cards and all(
            str(c.get("reach") or "").strip() for c in cards):
        # The line that says what the figures mean sits under the cards
        # when the page has room for it; otherwise it is the subhead.
        read = str(sl.get("read"))
        band_in = float(BAND) / 914400.0
        rows = (len(cards) + 1) // 2
        end = (_chrome_top(sl.get("title"), sub) + rows * 1.82
               + (rows - 1) * 0.22 + 0.24 + _block_h(read, 11.5, band_in))
        floor = 6.50 if sl.get("source") else 6.92
        if end > floor:
            sub, read = read, None
    d.chrome(s, sl.get("eyebrow") or "Argument", sl.get("title") or "",
             dark=dark, sub=sub, source=sl.get("source"))
    _cards_2x2(d, s, cards, dark, read=read)


def _sl_tiles_facts(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Universe", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    top = d.content_top
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
    facts = _items(sl, "facts", 6)
    if facts:
        floor_y = 6.20 if sl.get("read") else 6.88
        # A pushed-down top plus a full fact list cannot always fit:
        # drop trailing facts rather than render over the footer
        # (the renderer's contract is that list lengths clamp).
        n_fit = max(1, int((floor_y - y) / 0.32 + 0.001))
        facts = facts[:n_fit]
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
            size=11.5, color=BODY_LT)


def _sl_bars(d, sl):
    dark = str(sl.get("ground") or "light").lower() == "dark"
    s = d.new_slide(dark=dark)
    d.chrome(s, sl.get("eyebrow") or "Read", sl.get("title") or "",
             dark=dark, sub=sl.get("sub"), source=sl.get("source"))
    rows = _items(sl, "rows", 9)
    suffix = str(sl.get("value_suffix") if sl.get("value_suffix")
                 is not None else "%")
    show_index = bool(sl.get("show_index")) and any(
        r.get("index") not in (None, "") for r in rows)
    ink = WHITE if dark else GRAPHITE
    body = BODY_DK if dark else BODY_LT
    muted = MUTED_DK if dark else MUTED_LT
    val_accent = ORCHID if dark else AMETHYST
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
    floor_y = 6.02 if sl.get("read") else 6.60
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
            labels[ri], size=11.5, bold=accent, color=ink)
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
            size=11.5, color=body)


def _stat_card(s, x, y, card, *, big_color=GRAPHITE):
    rrect(s, x, y, Inches(3.644), Inches(1.82), CARD)
    txt(s, x + Inches(0.24), y + Inches(0.19), Inches(3.16), Inches(0.24),
        str(card.get("kicker") or "").upper(), size=10.5, bold=True,
        color=MUTED_LT, spc=100)
    txt(s, x + Inches(0.24), y + Inches(0.49), Inches(3.16), Inches(0.7),
        card.get("big") or "", size=34, bold=True,
        color=OLIVE if card.get("accent") else big_color)
    txt(s, x + Inches(0.24), y + Inches(1.23), Inches(3.16), Inches(0.56),
        card.get("label") or "", size=11.5, color=BODY_LT)


def _sl_split_stats_bars(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Read", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    top = d.content_top
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
    floor_y = 5.80
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
            labels[ri], size=11.5, bold=accent)
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
            size=11.5, color=BODY_LT)


def _sl_tiles_row(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Read", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    top = d.content_top
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
            size=11.5, color=BODY_LT)


def _sl_hero(d, sl):
    orchid = str(sl.get("ground") or "accent").lower() != "dark"
    s = d.new_slide(dark=not orchid, fill=ORCHID if orchid else None)
    if not orchid and sl.get("_photo"):
        _photo_moment(s, sl["_photo"], 68)
    d.chrome(s, sl.get("eyebrow") or "Signal", sl.get("title") or "",
             dark=not orchid, orchid=orchid, sub=sl.get("sub"),
             source=sl.get("source"))
    ink = WHITE if orchid else SIGNAL
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
            sl["support"], size=16, color=support_c)


def _sl_table(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Table", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    cols = [str(c) for c in (sl.get("columns") or [])][:6]
    rows = [r for r in (sl.get("rows") or []) if isinstance(r, list)][:5]
    if not cols:
        return
    n = len(cols)
    first_w = 3.60 if n >= 5 else 4.20
    rest_w = (11.493 - first_w) / max(n - 1, 1)
    xs = [float(M) / 914400.0]
    for i in range(1, n):
        xs.append(xs[0] + first_w + (i - 1) * rest_w)
    top = d.content_top
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
            cell_acc = row_acc and isinstance(acc_col, int) \
                and ci == acc_col
            txt(s, Inches(xs[ci]), Inches(y),
                Inches(first_w if ci == 0 else rest_w),
                Inches(row_h - 0.06), cell, size=c_size,
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
            Inches(0.64), reads[0], size=14, color=BODY_LT)


def _sl_hero_proof(d, sl):
    s = d.new_slide(dark=True)
    if sl.get("_photo"):
        _photo_moment(s, sl["_photo"], 70)
    d.chrome(s, sl.get("eyebrow") or "Proof", sl.get("title") or "",
             dark=True, sub=sl.get("sub"), source=sl.get("source"))
    top = d.content_top
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
        y += 1.32


_CLICKSTREAM_ROW = re.compile(
    r"(^[a-z0-9.-]+\.[a-z]{2,}(/|$|\?))|(^https?://)|(/[a-z0-9_\-]+)|(\?[a-z0-9_]+=)", re.I)


def _is_clickstream_row(url):
    """True for a raw clickstream address (a host, a path, a query); a
    brand name or a figure is not one and is set in Inter (Geist Mono
    is for raw clickstream rows only, the design pack)."""
    u = str(url or "").strip()
    return bool(u) and bool(_CLICKSTREAM_ROW.search(u))


def _sl_paths(d, sl):
    s = d.new_slide(dark=True)
    d.chrome(s, sl.get("eyebrow") or "Paths", sl.get("title") or "",
             dark=True, sub=sl.get("sub"), source=sl.get("source"))
    y = d.content_top
    for r in _items(sl, "rows", 13):
        lit = bool(r.get("lit"))
        if lit:
            rrect(s, M, Inches(y), BAND, Inches(0.245), SLATE,
                  radius=0.06)
        c = SIGNAL if lit else MUTED_DK
        mono = _is_clickstream_row(r.get("url"))
        face = "Geist Mono" if mono else "Crosswalk Inter"
        # a raw address sits in 8.5pt mono; a row that is a name or a
        # figure is body copy and never drops below 10pt
        size = 8.5 if mono else 10
        txt(s, M + Inches(0.16), Inches(y), Inches(1.1),
            Inches(0.245), str(r.get("kind") or "").upper(),
            size=size, color=c, font=face,
            anchor=MSO_ANCHOR.MIDDLE, spc=80)
        txt(s, M + Inches(1.36), Inches(y), BAND - Inches(1.52),
            Inches(0.245), r.get("url") or "", size=size, color=c,
            font=face, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.28


def _sl_close(d, sl):
    s = d.new_slide(dark=True)
    if not (sl.get("_photo") and _photo_moment(s, sl["_photo"], 70)):
        _page_gradient(s, "graphiteWash")
    d.chrome(s, sl.get("eyebrow") or "Close", sl.get("title") or "",
             dark=True, sub=sl.get("sub"))
    _cards_2x2(d, s, _items(sl, "cards", 4), dark=True)


_RENDERERS = {
    "cover": _sl_cover,
    "argument": _sl_argument,
    "tiles_facts": _sl_tiles_facts,
    "bars": _sl_bars,
    "split_stats_bars": _sl_split_stats_bars,
    "tiles_row": _sl_tiles_row,
    "hero": _sl_hero,
    "table": _sl_table,
    "hero_proof": _sl_hero_proof,
    "paths": _sl_paths,
    "close": _sl_close,
}

SLIDE_TYPES = tuple(_RENDERERS.keys())


def _resolve_logos(static_dir):
    """The real lockups from the crosswalk-design pack first (transparent,
    4736 x 600; 2026-10-21), then the copies in static/, then the older
    skill assets when running from the repo root (local tests)."""
    cands_w, cands_k = [], []
    ds = _design()
    if ds is not None:
        try:
            pw, pk = ds.logo_path("white"), ds.logo_path("black")
            if pw:
                cands_w.append(str(pw))
            if pk:
                cands_k.append(str(pk))
        except Exception:
            pass
    if static_dir:
        cands_w.append(os.path.join(static_dir,
                                    "crosswalk-logo-brand-white.png"))
        cands_k.append(os.path.join(static_dir,
                                    "crosswalk-logo-brand-black.png"))
    here = os.path.dirname(os.path.abspath(__file__))
    skill = os.path.join(os.path.dirname(here), ".cursor", "skills",
                         "crosswalk-brand-standards", "assets")
    cands_w += [os.path.join(here, "static",
                             "crosswalk-logo-brand-white.png"),
                os.path.join(skill, "crosswalk-logo-white.png")]
    cands_k += [os.path.join(here, "static",
                             "crosswalk-logo-brand-black.png"),
                os.path.join(skill, "crosswalk-logo-black.png")]
    lw = next((p for p in cands_w if os.path.exists(p)), None)
    lk = next((p for p in cands_k if os.path.exists(p)), None)
    return lw, lk


def _apply_theme_faces(pptx_path, face="Crosswalk Inter"):
    """Set the theme's major and minor Latin faces to the Crosswalk
    Inter family (crosswalk-design skill, 2026-09-25: 'set the theme's
    major and minor Latin faces to Crosswalk Inter'). Rewrites the
    saved package's theme XML in place; any placeholder text that
    inherits from the theme then resolves to the brand face instead of
    the python-pptx default Calibri."""
    import io as _io
    import re as _re
    import zipfile as _zf
    try:
        with open(pptx_path, "rb") as fh:
            blob = fh.read()
        src = _zf.ZipFile(_io.BytesIO(blob))
        out = _io.BytesIO()
        with _zf.ZipFile(out, "w", _zf.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename.startswith("ppt/theme/") and \
                        item.filename.endswith(".xml"):
                    xml = data.decode("utf-8")
                    xml = _re.sub(
                        r'(<a:(?:major|minor)Font>\s*<a:latin[^>]*?typeface=")[^"]*(")',
                        r"\g<1>" + face + r"\g<2>", xml)
                    data = xml.encode("utf-8")
                dst.writestr(item, data)
        with open(pptx_path, "wb") as fh:
            fh.write(out.getvalue())
    except Exception:
        # The run-level faces are already set on every run; a theme
        # patch failure never blocks the deliverable.
        pass


# ---- repeated phrases (2026-11-10) ------------------------------------------
# The planner wrote one subhead template on four slides of the Las
# Culturistas deck ("..., penetration of the audience with index against
# the average American") and reused a closer's phrase three times; the
# pack's readability check flags a phrase of five words on three or more
# units as a template. Before rendering, the plan is read the way that
# check reads it. A subhead template is retired: the first slide that
# uses index and penetration defines them once, in plain words, and every
# later slide gets a subhead that names its own content from its own
# rows. A phrase still carried by three or more units is thinned on the
# later closers (the read line, rebuilt from the slide's own figures) and
# stat labels (the card's kicker and figure); titles and card bodies are
# never rewritten. Everything is deterministic and comes from the plan
# itself, so a repeat render stays identical.

_DEDUPE_STOP = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "but",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "that", "this", "these", "those", "who", "which", "what", "her",
    "his", "their", "she", "he", "they", "them", "we", "our", "you", "your",
    "not", "no", "than", "then", "so", "if", "into", "over", "under", "about",
    "after", "before", "up", "down", "out", "off", "all", "any", "each",
    "every", "more", "most", "much", "many", "some", "such", "same", "other",
    "one", "two", "has", "have", "had", "do", "does", "did", "can", "will",
    "would", "should", "could", "there", "here", "when", "where", "while",
    "how", "also", "just", "only", "very", "per", "vs", "within", "between",
}
_PHRASE_N = 5
_PHRASE_FLAG_MIN = 3
_UNIT_MIN_WORDS = 8


def _words_of(text):
    return [w for w in re.findall(r"[A-Za-z0-9$%'.,-]+", str(text or ""))
            if re.search(r"[A-Za-z0-9]", w)]


def _ptoks(text):
    return [t for t in (re.sub(r"[^a-z0-9']", "", w.lower()) for w in _words_of(text)) if t]


def _grams(text):
    toks = _ptoks(text)
    out = set()
    for i in range(len(toks) - _PHRASE_N + 1):
        g = tuple(toks[i:i + _PHRASE_N])
        if any(re.search(r"\d", t) for t in g):
            continue
        if sum(1 for t in g if t not in _DEDUPE_STOP) < 2:
            continue
        out.add(g)
    return out


def _is_chrome_unit(text):
    u = str(text or "").strip()
    return (u.lower().startswith("source")
            or bool(re.search(r"CROSSWALK\s*/\s*BEHAVIORAL INTELLIGENCE|CONFIDENTIAL", u, re.I)))


_LIST_FIELDS = {
    "cards": ("head", "body"), "tiles": ("label", "body"), "facts": ("label", "note"),
    "stat_cards": ("kicker", "label"), "rows": ("label", "url"), "proofs": ("label",),
    "stats": ("label",),
}
_TEXT_FIELDS = ("sub", "intro", "line", "support", "read", "read2", "bars_title")


def _plan_units(slides):
    """Every written unit the readability check compares, with its
    address: (slide index, path, text). Titles always; other text when it
    runs eight words or more and is not chrome; a line repeated near
    verbatim on two or more slides (a window line) does not count."""
    raw = []
    for i, sl in enumerate(slides):
        if str(sl.get("title") or "").strip():
            raw.append((i, ("title",), str(sl["title"]).strip(), True))
        for f in _TEXT_FIELDS:
            v = sl.get(f)
            if isinstance(v, str) and v.strip():
                raw.append((i, (f,), v.strip(), False))
        for lf, keys in _LIST_FIELDS.items():
            items = sl.get(lf)
            if not isinstance(items, list):
                continue
            for j, it in enumerate(items):
                if isinstance(it, dict):
                    for k in keys:
                        v = it.get(k)
                        if isinstance(v, str) and v.strip():
                            raw.append((i, (lf, j, k), v.strip(), False))
                elif isinstance(it, list):      # table rows
                    for k, cell in enumerate(it):
                        if isinstance(cell, str) and cell.strip():
                            raw.append((i, (lf, j, k), cell.strip(), False))
    running = {}
    for i, path, text, is_title in raw:
        key = re.sub(r"\d+", "#", " ".join(_ptoks(text)))
        running.setdefault(key, set()).add(i)
    units = []
    for i, path, text, is_title in raw:
        if is_title:
            units.append((i, path, text))
            continue
        if len(_words_of(text)) < _UNIT_MIN_WORDS or _is_chrome_unit(text):
            continue
        if len(running.get(re.sub(r"\d+", "#", " ".join(_ptoks(text))), ())) >= 2:
            continue
        units.append((i, path, text))
    return units


def _sub_lead(sub, sl):
    """The slide's own subject in its subhead: the words before the first
    comma, colon or period ('Purchased categories'); the eyebrow when the
    subhead has no such lead."""
    head = re.split(r"[,:;.]", str(sub or ""), 1)[0].strip()
    if 0 < len(head.split()) <= 6 and len(head) < len(str(sub).strip()):
        return head[0].upper() + head[1:]
    eb = str(sl.get("eyebrow") or "").strip()
    if eb:
        return eb[0].upper() + eb[1:].lower()
    return "This read"


def _lead_fact(sl):
    """What the slide itself leads with, for a subhead or closer rebuilt
    from its own data: ('ranked', top, value, index, second, value2,
    bottom, value_b) for ranked rows, ('tile', label, big) for tiles and
    stat cards, ('card', head) for numbered cards, or None."""
    t = str(sl.get("type") or "").lower()
    rows = [r for r in (sl.get("rows") or []) if isinstance(r, dict) and r.get("label")]
    if t in ("bars", "split_stats_bars") and rows:
        suffix = str(sl.get("value_suffix") if sl.get("value_suffix") is not None else "%")
        def val(r):
            return _fmt_val(r.get("value"), suffix)
        top, sec, bot = rows[0], (rows[1] if len(rows) > 1 else None), (rows[-1] if len(rows) > 1 else None)
        idx = str(top.get("index") or "").strip() if sl.get("show_index") else ""
        return ("ranked", str(top["label"]).strip(), val(top), idx,
                str(sec["label"]).strip() if sec else "", val(sec) if sec else "",
                str(bot["label"]).strip() if bot else "", val(bot) if bot else "", suffix)
    tiles = [x for x in (sl.get("tiles") or sl.get("stat_cards") or sl.get("stats") or sl.get("proofs") or [])
             if isinstance(x, dict) and (x.get("label") or x.get("kicker")) and (x.get("big") or x.get("fig"))]
    if tiles:
        x = tiles[0]
        lab = str(x.get("label") or x.get("kicker") or "").strip().rstrip(".")
        big = str(x.get("big") or x.get("fig") or "").strip()
        second = tiles[1] if len(tiles) > 1 else None
        return ("tile", lab, big,
                str(second.get("label") or second.get("kicker") or "").strip().rstrip(".") if second else "",
                str(second.get("big") or second.get("fig") or "").strip() if second else "")
    cards = [c for c in (sl.get("cards") or []) if isinstance(c, dict) and c.get("head")]
    if cards:
        return ("card", str(cards[0]["head"]).strip().rstrip("."))
    return None


def _define_index_sub(lead):
    return f"{lead}: penetration is the share of this audience, index 100 = the average American."


def _content_sub(sl, lead, k):
    """A subhead naming this slide's own content, varied by position so
    no two read as one template."""
    f = _lead_fact(sl)
    if f and f[0] == "ranked":
        _, top, v, idx, sec, v2, _bot, _vb, suffix = f
        of_aud = " of the audience" if suffix == "%" else ""
        # no verb agrees with an unknown label (singular or plural), so
        # every variant is built without one
        variants = [
            f"{lead}: {top} at {v}" + (f", index {idx}." if idx else "."),
            f"{lead}, led by {top} at {v}" + (f" and index {idx}." if idx else "."),
            f"{lead}. {top}: {v}{of_aud}" + (f", index {idx}." if idx else "."),
            f"{lead}: {top} first at {v}" + (f", then {sec} at {v2}." if sec else "."),
            f"{lead}, with {top} on top at {v}" + (f" and {sec} next at {v2}." if sec else "."),
        ]
        return variants[k % len(variants)]
    if f and f[0] == "tile":
        _, lab, big, lab2, big2 = f
        variants = [
            f"{lead}: {lab} at {big}.",
            f"{lead}, starting with {lab} at {big}.",
            f"{lead}. {lab}: {big}" + (f"; {lab2}: {big2}." if lab2 else "."),
        ]
        return variants[k % len(variants)]
    if f and f[0] == "card":
        return f"{lead}: {f[1]}."
    return f"{lead}."


def _content_closer(sl, k):
    """A read line rebuilt from the slide's own figures; '' when the slide
    has none to read from (the field is then dropped)."""
    f = _lead_fact(sl)
    if f and f[0] == "ranked":
        _, top, v, idx, sec, v2, bot, vb, suffix = f
        variants = [
            f"{top} at {v}" + (f" against {bot} at {vb}." if bot else "."),
            f"The range runs from {top} at {v}" + (f" down to {bot} at {vb}." if bot else "."),
            f"Top of the list: {top} at {v}" + (f", then {sec} at {v2}." if sec else "."),
        ]
        return variants[k % len(variants)]
    if f and f[0] == "tile":
        _, lab, big, lab2, big2 = f
        return f"{lab} at {big}" + (f"; {lab2} at {big2}." if lab2 else ".")
    return ""


def _set_path(sl, path, value):
    if len(path) == 1:
        if value:
            sl[path[0]] = value
        else:
            sl.pop(path[0], None)
        return
    lf, j, k = path
    sl[lf][j][k] = value


def dedupe_plan_copy(slides):
    """Retire repeated phrases in a slide plan, in place. Returns notes
    (one line per rewrite) for the job record."""
    notes = []
    # A. a subhead template shared across slides
    subs = [(i, sl["sub"]) for i, sl in enumerate(slides)
            if isinstance(sl.get("sub"), str) and len(_words_of(sl["sub"])) >= 4]
    gram_of = {i: _grams(t) for i, t in subs}
    parent = {i: i for i, _t in subs}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, _ta in subs:
        for b, _tb in subs:
            if a < b and gram_of[a] & gram_of[b]:
                parent[find(a)] = find(b)
    groups = {}
    for i, _t in subs:
        groups.setdefault(find(i), []).append(i)
    for members in groups.values():
        members.sort()
        if len(members) < 2:
            continue
        first = members[0]
        sub0 = slides[first]["sub"]
        lead0 = _sub_lead(sub0, slides[first])
        if re.search(r"\b(index|penetration)\b", sub0, re.I):
            slides[first]["sub"] = _define_index_sub(lead0)
            notes.append(f"slide {first + 1}: subhead defines index and penetration once")
        for k, idx in enumerate(members[1:]):
            old = slides[idx]["sub"]
            slides[idx]["sub"] = _content_sub(slides[idx], _sub_lead(old, slides[idx]), k)
            notes.append(f"slide {idx + 1}: subhead template retired, names its own content")
    # B. any phrase still carried by three or more units: thin the later
    #    closers and stat labels until it reads on two at most
    closer_k = {}
    for _round in range(3):
        units = _plan_units(slides)
        counts = {}
        for ui, (i, path, text) in enumerate(units):
            for g in _grams(text):
                counts.setdefault(g, set()).add(ui)
        offenders = sorted((g for g, s in counts.items() if len(s) >= _PHRASE_FLAG_MIN),
                           key=lambda g: (-len(counts[g]), g))
        if not offenders:
            break
        changed = False
        for g in offenders:
            members = sorted(counts[g])
            # the first occurrence stays; later ones give way, closers
            # first (the read line), then a subhead, then a stat label
            def _prio(ui):
                f = units[ui][1][0]
                return {"read": 0, "read2": 0, "sub": 1, "stat_cards": 2}.get(f, 9), ui
            for ui in sorted(members[1:], key=_prio):
                if len(counts[g]) < _PHRASE_FLAG_MIN:
                    break
                i, path, text = units[ui]
                sl = slides[i]
                new = None
                if path[0] in ("read", "read2"):
                    k = closer_k.get(i, 0)
                    new = _content_closer(sl, k)
                    closer_k[i] = k + 1
                elif path[0] == "stat_cards" and path[2] == "label":
                    card = sl["stat_cards"][path[1]]
                    kicker = str(card.get("kicker") or "").strip().lower()
                    big = str(card.get("big") or "").strip()
                    if kicker and big:
                        new = f"{big} {kicker}"
                elif path[0] == "sub":
                    new = _content_sub(sl, _sub_lead(text, sl), closer_k.get(i, 0))
                    closer_k[i] = closer_k.get(i, 0) + 1
                if new is None or new == text:
                    continue
                _set_path(sl, path, new)
                counts[g].discard(ui)
                changed = True
                what = "/".join(str(x) for x in path)
                notes.append(f"slide {i + 1}: {what} rewritten from the slide's own figures "
                             f"(phrase '{' '.join(g)}' on {len(members)} units)")
        if not changed:
            break
    return notes


def render_insights_deck(plan, out_path, static_dir=None,
                        photo_subject='', photo_kind=''):
    """Render a slide-plan dict to a finished PPTX at out_path.
    Returns the number of slides rendered.

    Photography (2026-09-30): the plan's image_subject (or the
    caller's photo_subject fallback) resolves to real photos placed
    under a graphite scrim on the cover, the first hero or
    hero_proof, and the close - the manual deck grammar. A slide
    carrying photo: false opts out; no photos means text-only."""
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    lw, lk = _resolve_logos(static_dir)
    d = _Deck(prs, lw, lk)
    # Render from copies: the caller's plan is never mutated, so a
    # repeat render of the same plan is deterministic.
    _slides = [copy.deepcopy(sl) for sl in (plan.get("slides") or [])
               if isinstance(sl, dict)]
    # Repeated phrases come off before anything is laid out (2026-11-10):
    # one subhead template across slides, a closer reused three times.
    try:
        plan['_copy_notes'] = dedupe_plan_copy(_slides)
    except Exception:
        plan['_copy_notes'] = []
    try:
        _p_subj = str(plan.get("image_subject")
                      or photo_subject or '').strip()
        _p_kind = str(plan.get("image_kind")
                      or photo_kind or 'person').strip().lower()
        photos = _deck_photos(_p_subj, _p_kind) if _p_subj else []
        plan['_photo_subject'] = _p_subj
        plan['_photo_sources'] = list(_PHOTO_SOURCES.get(_p_subj.lower()) or [])
        print(f"[deck] photo subject {_p_subj!r} ({_p_kind}); "
              f"{len(photos)} photo(s) from {plan['_photo_sources'][:3]}")
        if photos:
            moments = []
            for want in (('cover',), ('hero', 'hero_proof'),
                         ('close',)):
                hit = next(
                    (sl for sl in _slides
                     if str(sl.get("type") or '').strip().lower()
                     in want and sl.get("photo") is not False), None)
                if hit is not None:
                    moments.append(hit)
            for i, sl in enumerate(moments):
                sl["_photo"] = photos[i % len(photos)]
    except Exception:
        pass
    rendered = 0
    for sl in _slides:
        fn = _RENDERERS.get(str(sl.get("type") or "").strip().lower())
        if fn is None:
            continue
        fn(d, sl)
        rendered += 1
    prs.save(out_path)
    _apply_theme_faces(out_path)
    # 2026-10-21 (Jenna: the design skill is always used): every delivered
    # deck carries Crosswalk Inter embedded and passes the pack's checks.
    # The finish fixes in place and never holds the file. What it ran,
    # what it changed and the check counts ride on the plan for the job
    # record (2026-11-10).
    ds = _design()
    plan['_finish'] = {'ready': None, 'copy_notes': list(plan.get('_copy_notes') or [])}
    if ds is not None:
        try:
            rep = ds.finish(out_path)
            plan['_finish'].update(rep.job_record())
        except Exception as e:
            plan['_finish']['error'] = f"{type(e).__name__}: {e}"[:200]
    return rendered
