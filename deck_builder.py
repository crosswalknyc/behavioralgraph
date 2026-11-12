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


# Where a page's content has to stop: above the source line when the page
# carries one, above the footer when it does not. A two-line headline
# pushes the content band down; cards that were laid for a one-line
# headline ran over the source line (2026-11-11).
_FLOOR_SOURCE = 6.50
_FLOOR_PLAIN = 6.90


def _content_floor(sl):
    return _FLOOR_SOURCE if sl.get("source") else _FLOOR_PLAIN


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


_PACK_LINES = {}


def _pack_lines(seg, size, width_in, bold):
    """Lines the design pack's wrap model gives this paragraph (the most
    any of its renderer scales needs), or 0 without the pack. The widow
    fit in ``txt`` lays type with that model, so the layout has to count
    with it too: a headline this module measured at one line was set on
    two and ran into its subhead (2026-11-11)."""
    key = (seg, float(size), round(float(width_in), 3), bool(bold))
    if key in _PACK_LINES:
        return _PACK_LINES[key]
    n = 0
    ds = _design()
    if ds is not None and size >= 9 and width_in > 0 and seg.strip():
        try:
            _lone, n = ds.widow_lines(seg, size, width_in, bold=bold)
            if ds.lint_module() is None:
                n = 0
        except Exception:
            n = 0
    if len(_PACK_LINES) > 20000:
        _PACK_LINES.clear()
    _PACK_LINES[key] = n
    return n


def _line_count(text, size, width_in, bold=False):
    """Line count for text in a box width_in wide: the greedy wrap on the
    real font metrics, or the design pack's wrap model when that needs
    more lines (the model the widow fit and the brand lint lay type
    with). A pair bound with a non-breaking space (the widow fix) is one
    word here, as it is to every renderer."""
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
        total += max(lines, _pack_lines(seg, size, width_in, bold))
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


_TRAIL_WORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "but",
    "with", "from", "by", "as", "than", "that", "which", "is", "are", "was",
    "were", "its", "their", "this", "these", "over", "per", "vs", "into",
}


def _shorten_whole(text, fits):
    """The longest opening of ``text`` that ``fits`` and still reads whole
    (2026-11-11: a table cell shipped as "Daily by design, the" and
    another as "YouTube 19.3 / TikTok"). Whole sentences first, then a
    clause join (comma, semicolon, slash), then a word boundary; never
    inside a word, never ending on a joining word or a loose comma,
    never an ellipsis. A sentence keeps its full stop."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s or fits(s):
        return s
    closed = s.endswith((".", "!", "?"))
    sents = re.split(r"(?<=[.!?])\s+", s)
    while len(sents) > 1:
        sents.pop()
        cand = " ".join(sents)
        if fits(cand):
            return cand
    s = sents[0] if sents else s
    ds = _design()
    try:
        mod = ds.copy_fix_module() if ds is not None else None
    except Exception:
        mod = None

    def _fits_closed(t):
        return fits(t + "." if closed and not t.endswith((".", "!", "?")) else t)

    if mod is not None:
        out = mod.short_cell(s.rstrip(".!?") if closed else s, _fits_closed)
    else:
        words = (s.rstrip(".!?") if closed else s).split()
        while len(words) > 1:
            words.pop()
            while len(words) > 1 and words[-1].lower().strip(",;:") in _TRAIL_WORDS:
                words.pop()
            if _fits_closed(" ".join(words).rstrip(" ,;:/(")):
                break
        out = " ".join(words).rstrip(" ,;:/(")
    if closed and out and not out.endswith((".", "!", "?")):
        out += "."
    return out


def _fit_body(text, size_opts, width_in, max_h):
    """Body copy: step the size down, then shorten at a sentence, a
    clause or a word boundary, so the block never runs past max_h and
    never ends on a dangling word. Returns (text, size)."""
    for cand in size_opts:
        if _block_h(text, cand, width_in) <= max_h:
            return str(text or ""), cand
    size = size_opts[-1]
    return _shorten_whole(
        text, lambda t: _block_h(t, size, width_in) <= max_h), size


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
    if face != "Geist Mono":
        # a range reads "25 to 34" and a label "Arts and Culture": the
        # joining words a title-caser raised come back down (2026-11-11)
        try:
            from migration.design_skill import fix_label_casing
            text = fix_label_casing(text)
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
_PHOTO_SOURCES = {}   # subject -> the candidate photo urls (one address once), for the job trail
_PHOTO_USED = {}      # subject -> the urls whose pictures the deck took
_PHOTO_REJECTS = {}   # subject -> [(url, why)] candidates turned away
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
    # A cover photograph is of someone or something. A subject made only
    # of general words ("Digital Original Content Categories") names
    # nothing to photograph: a search on it returns blog graphics and
    # whoever tops the encyclopedia search (2026-11-11). No search runs
    # and the cover takes the gradient.
    subj_toks = _subject_photo_tokens(subject)
    if not subj_toks:
        _PHOTO_SOURCES[subject.lower()] = []
        _PHOTO_USED[subject.lower()] = []
        _PHOTO_REJECTS[subject.lower()] = [(subject[:160], "the subject names no person, title or brand to photograph")]
        return []
    urls, off_subject = [], []
    try:
        u, _src = ib.resolve_image_url(subject, master)
        if u:
            urls.append(u)
    except Exception:
        pass
    try:
        u = ib.wiki_lookup(subject)
        if u:
            # an encyclopedia lookup answers with its nearest article; the
            # picture counts only when its file is named for the subject
            if _url_names_subject(u, subj_toks, need=1):
                urls.append(u)
            else:
                off_subject.append((u, "the picture is not named for the subject"))
    except Exception:
        pass
    # Open-web results must carry the subject in the address (host or
    # path). A generic query returns stock art of anything; a Starz
    # deck opened on a Microsoft Teams graphic (2026-10-07, Bria).
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
            if not _url_names_subject(u, subj_toks):
                continue
            urls.append(u)
            if len(urls) >= max_n + 5:
                break
    except Exception:
        pass
    blobs, kept, rejected = _vet_photo_candidates(
        urls, max_n, lambda u: ib.download_image(u))
    ds = _design()
    seen_keys, cands = set(), []
    for u in urls:
        k = u
        if ds is not None:
            try:
                k = ds.photo_url_key(u) or u
            except Exception:
                k = u
        if k not in seen_keys:
            seen_keys.add(k)
            cands.append(u)
    _PHOTO_SOURCES[subject.lower()] = [u[:160] for u in cands[:max_n + 2]]
    _PHOTO_USED[subject.lower()] = [u[:160] for u in kept]
    _PHOTO_REJECTS[subject.lower()] = [(u[:160], why) for u, why in off_subject + rejected]
    return blobs


# Words that describe an audience or a kind of thing, never a person, a
# title or a brand. A subject left with none of its own words after these
# come out is a topic, and a topic has no photograph.
_PHOTO_GENERIC_WORDS = {
    "digital", "original", "originals", "content", "contents", "category", "categories", "viewers", "viewer",
    "audience", "audiences", "fans", "users", "buyers", "shoppers", "customers", "consumers", "streaming",
    "streamers", "online", "video", "videos", "social", "media", "platform", "platforms", "brand", "brands",
    "profile", "profiles", "insights", "total", "universe", "avid", "casual", "potential", "enthusiasts",
    "owners", "members", "subscribers", "switchers", "renters", "players", "listeners", "watchers", "read",
    "reads", "summary", "report", "deck", "series", "shows", "show", "movies", "movie", "people", "persons",
    "american", "americans", "adults", "women", "female", "male", "with", "from", "that", "this", "their",
    "your", "what", "which", "best", "most", "types", "type", "kinds", "kind", "genres", "genre", "examples",
}
# Folders every site has; a subject word found only there names nothing.
_PHOTO_PATH_BOILERPLATE = ("wp-content", "wp-includes", "content", "contents", "uploads", "upload", "images",
                           "image", "img", "media", "static", "assets", "files", "file", "cdn", "storage",
                           "public", "resources", "thumb", "thumbs", "thumbnail", "thumbnails", "photos", "photo")


def _subject_photo_tokens(subject):
    """The subject's own words (four letters and up), without the general
    words that describe an audience or a kind of thing. Empty when the
    subject names no person, title or brand."""
    return [w for w in re.sub(r"[^a-z0-9]+", " ", str(subject or "").lower()).split()
            if len(w) >= 4 and w not in _PHOTO_GENERIC_WORDS]


def _url_names_subject(url, subj_toks, need=2):
    """True when the address carries the subject's own words outside the
    folders every site has ("wp-content/uploads"): ``need`` of them (two
    for an open-web result, one for an encyclopedia picture), or the one
    the subject has when it has only one."""
    import urllib.parse as _up
    try:
        pr = _up.urlsplit(str(url or ""))
        host = (pr.netloc or "").lower()
        segs = [seg for seg in _up.unquote(pr.path or "").lower().split("/") if seg and seg not in _PHOTO_PATH_BOILERPLATE]
    except Exception:
        return False
    words = set(re.sub(r"[^a-z0-9]+", " ", host + " " + " ".join(segs)).split())
    blob = host + "/" + "/".join(segs)
    hits = sum(1 for t in set(subj_toks) if t in words or (len(t) >= 6 and t in blob))
    return hits >= min(max(1, int(need)), len(set(subj_toks)))


def _vet_photo_candidates(urls, max_n, fetch):
    """(blobs, urls kept, [(url, why)] turned away) from candidate photo
    addresses, best first (2026-11-11: a YouTube deck took a Wikipedia
    screenshot of the search page twice, by one address written two ways,
    with a logo from a free-icon site next in line). One address counts
    once, however it is written. A logo file, an icon or clip-art site, a
    stock preview and a screenshot of an interface are turned away by the
    address; a cut-out graphic, a page that is mostly white and a flat
    graphic are turned away by the pixels; the same picture reached by two
    addresses counts once. Nothing left means no photo: the cover takes
    the gradient."""
    import hashlib as _hl
    ds = _design()
    seen, seen_blobs, blobs, kept, rejected = set(), set(), [], [], []
    for u in urls:
        if not u:
            continue
        key = u
        if ds is not None:
            try:
                key = ds.photo_url_key(u) or u
            except Exception:
                key = u
        if key in seen:
            continue
        seen.add(key)
        why = None
        if ds is not None:
            try:
                why = ds.photo_url_reject_reason(u)
            except Exception:
                why = None
        if why:
            rejected.append((u, why))
            continue
        try:
            got = fetch(u)
        except Exception:
            got = None
        if not got:
            continue
        blob = got[0] if isinstance(got, tuple) else got
        if not blob:
            continue
        blob = bytes(blob)
        digest = _hl.sha1(blob).hexdigest()
        if digest in seen_blobs:
            continue
        seen_blobs.add(digest)
        if not _blob_embeddable(blob):
            rejected.append((u, "too small, or a format a deck cannot carry"))
            continue
        if ds is not None:
            try:
                why = ds.photo_blob_reject_reason(blob)
            except Exception:
                why = None
        if why:
            rejected.append((u, why))
            continue
        blobs.append(blob)
        kept.append(u)
        if len(blobs) >= max_n:
            break
    return blobs, kept, rejected


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


# Where type sits on a photo page, in page fractions, for the scrim
# measurement: the cover carries its eyebrow, title and intro up top and
# its three stats along the bottom; a close carries its title band over
# cards that sit on their own panels; a statement page carries type
# everywhere. The dimmest type on a dark page (body grey) is the color
# that has to read.
_PHOTO_ZONES = {
    "cover": ((0.06, 0.05, 0.94, 0.50), (0.06, 0.76, 0.94, 0.95)),
    "close": ((0.06, 0.05, 0.94, 0.34),),
}
_PHOTO_ZONE_ALL = ((0.06, 0.05, 0.94, 0.95),)
_BAKED_CACHE = {}


def _photo_moment(s, blob, alpha_pct, kind=""):
    """Full-bleed photo under the graphite scrim, as the slide BACKGROUND
    (2026-11-10): the photo is cover-cropped to the page at 3840 x 2160
    and the scrim is baked into the pixels, so no full-page shape sits
    under the content and no image is low resolution (the design pack:
    set the image as the slide background; scrim type over photographs,
    baked in).

    The scrim is set by what is behind the type (2026-11-11: a light
    photo under the standing 64% scrim left a cover title unreadable).
    The luminance behind the page's text zones is read from the cropped
    photo and the scrim is deepened from ``alpha_pct`` until body type
    clears 4.5:1 against the brightest part behind it. When no scrim
    short of the cap does, this returns False and the caller sets the
    gradient page with no photo. A bad blob does the same."""
    ds = _design()
    if ds is None:
        return False
    try:
        zones = [z + (tuple(BODY_DK),) for z in _PHOTO_ZONES.get(kind, _PHOTO_ZONE_ALL)]
        key = (id(blob), len(blob), int(alpha_pct), kind)
        if key not in _BAKED_CACHE:
            if len(_BAKED_CACHE) > 12:
                _BAKED_CACHE.clear()
            _BAKED_CACHE[key] = ds.bake_photo_for_text(
                blob, zones, scrim_hex="#%02X%02X%02X" % tuple(GRAPHITE),
                alpha=int(alpha_pct) / 100.0)
        baked, _used = _BAKED_CACHE[key]
        if not baked:
            return False
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


def _fmt_index(v):
    """An index as a reader sees it: a whole number, with a thousands
    separator at four digits or more (5,548). Text that is not a number
    prints as it came."""
    if v in (None, ""):
        return ""
    try:
        from prometheus.index_format import fmt_index
        out = fmt_index(v)
    except Exception:
        try:
            out = f"{int(round(float(str(v).replace(',', '')))):,}"
        except (TypeError, ValueError):
            out = ""
    return out or str(v).strip()


def _row_decimals(rows, key="value"):
    """How many decimals a column of values is written to (0 to 2): the
    most any row carries, so 3.0 sits beside 2.4 as "3.0%", never "3%"
    (the design pack: matching numbers share precision)."""
    most = 0
    for r in rows or []:
        v = r.get(key) if isinstance(r, dict) else r
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        txt_v = repr(v) if isinstance(v, float) else str(v)
        frac = txt_v.split(".", 1)[1] if "." in txt_v and "e" not in txt_v.lower() else ""
        frac = frac.rstrip("0") if isinstance(v, str) is False else frac
        if f != int(f) or (isinstance(v, str) and frac):
            most = max(most, min(len(frac) or 1, 2))
    return most


def _fmt_val(v, suffix, decimals=0):
    """Bar value display: keep the model's precision, add the suffix.
    ``decimals`` is the column's precision (``_row_decimals``); every
    row in the column is written to it."""
    n = _num(v)
    if decimals and decimals > 0:
        disp = f"{n:,.{int(decimals)}f}"
    elif n == int(n) and abs(n) >= 10 and suffix != "x":
        disp = f"{int(n):,}"
    else:
        disp = f"{n:g}"
    return f"{disp}{suffix}" if suffix else disp


def _items(sl, key, cap):
    out = [i for i in (sl.get(key) or []) if isinstance(i, dict)]
    return out[:cap]


def _one_line_eyebrow(text):
    """The cover eyebrow on one line: a long one ("SUBJECT · AUDIENCE READ
    · NOV 10 2025 TO NOV 10 2026") gives up its last segments until it
    fits before the lockup, since a second line sits on top of the
    headline (the window is in the intro line as well)."""
    parts = [p.strip() for p in re.split(r"\s*[\u00b7|]\s*", str(text or "").upper()) if p.strip()]
    if not parts:
        return str(text or "").upper()
    room = float(EYEBROW_MAX_W) / 914400.0

    def wide(t):
        return _str_w_in(t, 10.5, bold=True) + len(t) * 2.6 / 72.0

    while len(parts) > 1 and wide("  \u00b7  ".join(parts)) > room:
        parts.pop()
    return "  \u00b7  ".join(parts)


# ---- slide renderers --------------------------------------------------------

def _sl_cover(d, sl):
    s = d.new_slide(dark=True)
    if not (sl.get("_photo") and _photo_moment(s, sl["_photo"], 64, "cover")):
        _page_gradient(s, "graphiteWash")
    dot(s, M, Inches(0.540))
    txt(s, Inches(1.129), Inches(0.540), Inches(9.4), Inches(0.22),
        _one_line_eyebrow(sl.get("eyebrow") or "CROSSWALK"),
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


def _cards_2x2(d, s, cards, dark, read=None, floor=_FLOOR_PLAIN):
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
    # under a two-line headline the rows give up a little height so the
    # cards (and the line under them) stop above the floor
    room = floor - top - (rows - 1) * 0.22 - ((read_h + 0.24) if read_h else 0.0)
    ch_in = max(1.42, min(1.82, room / max(rows, 1)))
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
            f"Index {_fmt_index(idx)}", size=11.5, bold=True, color=ink)
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
    _cards_2x2(d, s, cards, dark, read=read, floor=_content_floor(sl))


# Tile heights and fact-row settings, roomiest first. A universe page
# keeps every fact row its plan carries: when the title and subhead push
# the content down, the tiles get shorter and then the rows get tighter,
# and past that the rows run in two columns. A row is never dropped
# (2026-11-11: a six-category page printed four of its six categories
# and the missing two had to be asked for by hand).
_TF_TILES = ((1.58, 30, 11.5), (1.38, 26, 11.0), (1.20, 23, 10.5), (1.06, 20, 10.0))
_TF_ROWS = ((11.5, 0.32), (10.5, 0.29), (10.0, 0.27))


def _sl_tiles_facts(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Universe", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    top = d.content_top
    tiles = _items(sl, "tiles", 4)
    facts = _items(sl, "facts", 6)
    acc = sl.get("accent_index")
    n = max(len(tiles), 1)
    w = Emu(int((int(BAND) - int(GUT) * (n - 1)) / n))
    band_in = float(BAND) / 914400.0
    read_h = _block_h(sl["read"], 11.5, band_in) if sl.get("read") else 0.0
    # the read ends above the source line (or the footer); the facts end above the read
    read_y = (_content_floor(sl) + 0.06 - read_h) if sl.get("read") else None
    floor_y = (min(6.20, read_y - 0.10) if sl.get("read")
               else (_content_floor(sl) if sl.get("source") else 6.88))
    # The middle column holds a figure, and sometimes words ("Oct 21 2025
    # to Oct 21 2026", "People, once each"): it is as wide as its longest
    # entry needs to sit on one line, up to 3.4 in., and the note column
    # starts after it. A wrapped entry used to run into the row below.
    fig_w = 1.80
    for cand_w in (1.80, 2.40, 2.90, 3.40):
        fig_w = cand_w
        if all(_line_count(str(f.get("fig") or ""), 11.5, cand_w, bold=True) <= 1 for f in facts):
            break
    note_x = 3.50 + fig_w + 0.20
    note_w = max(band_in - note_x, 2.2)

    def row_heights(rp, pt_):
        return [max(_block_h(str(f.get("label") or ""), rp, 3.40),
                    _block_h(str(f.get("fig") or ""), rp, fig_w, bold=True),
                    min(_block_h(str(f.get("note") or ""), rp, note_w), 2 * rp * 1.22 / 72.0),
                    pt_ - 0.085) + 0.085 for f in facts]

    # the roomiest setting that holds every row
    tile_h, big_pt, lab_pt = _TF_TILES[0]
    row_pt, pitch, cols = _TF_ROWS[0][0], _TF_ROWS[0][1], 1
    if facts:
        fit = None
        for th, bp, lp in _TF_TILES:
            for rp, pt_ in _TF_ROWS:
                if top + th + 0.21 + sum(row_heights(rp, pt_)) <= floor_y + 0.001:
                    fit = (th, bp, lp, rp, pt_, 1)
                    break
            if fit:
                break
        if fit is None:
            th, bp, lp = _TF_TILES[-1]
            rp, pt_ = _TF_ROWS[-1]
            fit = (th, bp, lp, rp, pt_, 2)
        tile_h, big_pt, lab_pt, row_pt, pitch, cols = fit
    x = M
    big_h = big_pt * 1.2 / 72.0 + 0.10
    for i, t in enumerate(tiles):
        rrect(s, x, Inches(top), w, Inches(tile_h), CARD)
        color = OLIVE if (isinstance(acc, int) and i == acc) else GRAPHITE
        pad = 0.15 if tile_h >= 1.5 else 0.11
        txt(s, x + Inches(0.18), Inches(top + pad), w - Inches(0.32),
            Inches(big_h), t.get("big") or "", size=big_pt, bold=True,
            color=color)
        ly = top + pad + big_h + (0.04 if tile_h >= 1.5 else 0.0)
        txt(s, x + Inches(0.18), Inches(ly), w - Inches(0.32),
            Inches(max(0.24, top + tile_h - ly - 0.06)), t.get("label") or "", size=lab_pt,
            color=BODY_LT)
        x += w + GUT
    y = top + tile_h + 0.21
    if facts and cols == 1:
        budget = max((floor_y - y) / len(facts), pitch)
        for f in facts:
            label_t, _ = _fit_body(f.get("label") or "", (row_pt,),
                                   3.40, budget - 0.085)
            note_t, _ = _fit_body(f.get("note") or "", (row_pt,),
                                  note_w, max(budget - 0.085, 2 * row_pt * 1.22 / 72.0))
            fig_t = str(f.get("fig") or "")
            row_h = max(_block_h(label_t, row_pt, 3.40),
                        _block_h(fig_t, row_pt, fig_w, bold=True),
                        _block_h(note_t, row_pt, note_w),
                        pitch - 0.085) + 0.085
            txt(s, M, Inches(y), Inches(3.40), Inches(row_h - 0.04),
                label_t, size=row_pt)
            txt(s, M + Inches(3.50), Inches(y), Inches(fig_w),
                Inches(row_h - 0.04), fig_t, size=row_pt,
                bold=True, color=OLIVE)
            txt(s, M + Inches(note_x), Inches(y), Inches(note_w),
                Inches(row_h - 0.04), note_t, size=row_pt,
                color=BODY_LT)
            y += row_h
    elif facts:
        # two columns, filled down then across, every row kept
        col_w = (band_in - float(GUT) / 914400.0) / 2.0
        per = (len(facts) + 1) // 2
        lw_, fw_, nw_ = 2.05, 1.25, col_w - 2.05 - 1.25 - 0.20
        y0 = y
        for k, f in enumerate(facts):
            cx = (0 if k < per else 1) * (col_w + float(GUT) / 914400.0)
            ry = y0 + (k if k < per else k - per) * pitch
            label_t, _ = _fit_body(f.get("label") or "", (row_pt,), lw_, pitch - 0.06)
            note_t, _ = _fit_body(f.get("note") or "", (row_pt,), nw_, pitch - 0.06)
            txt(s, M + Inches(cx), Inches(ry), Inches(lw_), Inches(pitch - 0.03), label_t, size=row_pt)
            txt(s, M + Inches(cx + lw_ + 0.10), Inches(ry), Inches(fw_), Inches(pitch - 0.03),
                f.get("fig") or "", size=row_pt, bold=True, color=OLIVE)
            txt(s, M + Inches(cx + lw_ + fw_ + 0.20), Inches(ry), Inches(nw_), Inches(pitch - 0.03),
                note_t, size=row_pt, color=BODY_LT)
        y = y0 + per * pitch
    if sl.get("read"):
        ry = max(min(6.30, read_y), y + 0.10)
        txt(s, M, Inches(ry), BAND, Inches(max(0.30, read_h + 0.04)), sl["read"],
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
    dec = _row_decimals(rows)
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
                Inches(0.28), _fmt_val(r.get("value"), suffix, dec),
                size=11, bold=True, color=val_c,
                align=PP_ALIGN.RIGHT)
            txt(s, M + Inches(10.30), Inches(y), Inches(1.19),
                Inches(0.28), _fmt_index(r.get("index")), size=11,
                color=body, align=PP_ALIGN.RIGHT)
        else:
            txt(s, bx + tw + Inches(0.12), Inches(y), Inches(1.00),
                Inches(0.28), _fmt_val(r.get("value"), suffix, dec),
                size=11, bold=True, color=val_c)
        y += row_step
    if sl.get("read"):
        ry = max(6.02, y + 0.06)
        txt(s, M, Inches(ry), BAND, Inches(0.56), sl["read"],
            size=11.5, color=body)


def _stat_card(s, x, y, card, *, big_color=GRAPHITE, h=1.82):
    """One stat card, ``h`` inches tall. Under 1.80 the figure steps down
    a size and the lines sit closer, so the card still holds its label."""
    tight = h < 1.80
    rrect(s, x, y, Inches(3.644), Inches(h), CARD)
    txt(s, x + Inches(0.24), y + Inches(0.16 if tight else 0.19), Inches(3.16), Inches(0.24),
        str(card.get("kicker") or "").upper(), size=10.5, bold=True,
        color=MUTED_LT, spc=100)
    txt(s, x + Inches(0.24), y + Inches(0.41 if tight else 0.49), Inches(3.16),
        Inches(0.56 if tight else 0.7),
        card.get("big") or "", size=28 if tight else 34, bold=True,
        color=OLIVE if card.get("accent") else big_color)
    ly = 1.00 if tight else 1.23
    txt(s, x + Inches(0.24), y + Inches(ly), Inches(3.16), Inches(max(0.30, h - ly - 0.06)),
        card.get("label") or "", size=11.5, color=BODY_LT)


def _sl_split_stats_bars(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Read", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    top = d.content_top
    cards = _items(sl, "stat_cards", 2)
    # two cards and the gap between them stop above the source line
    card_h = max(1.45, min(1.82, (_content_floor(sl) - top - 0.25) / 2.0))
    if cards:
        _stat_card(s, M, Inches(top), cards[0], h=card_h)
    if len(cards) > 1:
        _stat_card(s, M, Inches(top + card_h + 0.25), cards[1], h=card_h)
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
    dec = _row_decimals(rows)
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
            _fmt_val(r.get("value"), suffix, dec), size=11, bold=True,
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
    band_in = float(BAND) / 914400.0
    read_h = _block_h(sl["read"], 11.5, band_in) if sl.get("read") else 0.0
    # the tiles (and the read under them) stop above the source line
    tile_h = max(2.40, min(3.30, _content_floor(sl) + 0.06 - top
                           - ((read_h + 0.18) if sl.get("read") else 0.0)))
    for i, t in enumerate(tiles):
        rrect(s, x, Inches(top), w, Inches(tile_h), CARD)
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
                                      top + tile_h - body_y - 0.14)
        txt(s, x + Inches(0.24), Inches(body_y), w - Inches(0.48),
            Inches(max(0.28, top + tile_h - body_y - 0.14)), body_txt,
            size=body_sz, color=BODY_LT)
        x += w + GUT
    if sl.get("read"):
        ry = max(min(6.10, _content_floor(sl) + 0.06 - read_h), top + tile_h + 0.18)
        txt(s, M, Inches(ry), BAND, Inches(max(0.30, read_h + 0.04)), sl["read"],
            size=11.5, color=BODY_LT)


def _sl_hero(d, sl):
    orchid = str(sl.get("ground") or "accent").lower() != "dark"
    s = d.new_slide(dark=not orchid, fill=ORCHID if orchid else None)
    if not orchid and sl.get("_photo"):
        _photo_moment(s, sl["_photo"], 68, "hero")
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


_TABLE_SIZES = (14, 12.5, 11.5, 10.5)     # the cell type scale, largest first
_TABLE_PAD = 0.28                         # the gutter a column carries on its left


def _table_layout(cols, rows, band_in=11.493):
    """Column widths (inches) and which columns are words rather than
    figures, from what the cells hold (2026-11-11: five equal columns cut
    "YouTube 19.3 / TikTok 13.3" and "Daily by design, the stickiest" in
    half). A column of figures takes the width of its widest cell; the
    columns of words share what is left in proportion to their longest
    cell, so words get the room and wrap inside it."""
    n = len(cols)
    cells = [[str(r[i]) if i < len(r) else "" for r in rows] for i in range(n)]
    wordy = [i > 0 and any(len(c.split()) > 3 for c in cells[i]) for i in range(n)]
    nat = []
    for i in range(n):
        widest = max([_str_w_in(c, 12.5) for c in cells[i]] + [_str_w_in(str(cols[i]).upper(), 10.5, bold=True) * 1.12])
        nat.append(widest + (0.0 if i == 0 else _TABLE_PAD) + 0.06)
    floor = [1.60 if i == 0 else 1.05 for i in range(n)]
    nat = [max(a, f) for a, f in zip(nat, floor)]
    if sum(nat) <= band_in:
        spare = band_in - sum(nat)
        share = [(1.0 if (i == 0 or wordy[i]) else 0.35) for i in range(n)]
        return [a + spare * sh / sum(share) for a, sh in zip(nat, share)], wordy
    # too wide on one line each: figures keep their width, words wrap in the rest
    fixed = [i for i in range(n) if not wordy[i] and i != 0]
    flex = [i for i in range(n) if i not in fixed]
    left = band_in - sum(nat[i] for i in fixed)
    if left < 1.6 * len(flex):
        return [band_in * a / sum(nat) for a in nat], wordy
    widths = list(nat)
    tot = sum(nat[i] for i in flex)
    for i in flex:
        widths[i] = max(floor[i], left * nat[i] / tot)
    scale = (band_in - sum(widths[i] for i in fixed)) / max(sum(widths[i] for i in flex), 0.01)
    for i in flex:
        widths[i] *= scale
    return widths, wordy


def _sl_table(d, sl):
    s = d.new_slide()
    d.chrome(s, sl.get("eyebrow") or "Table", sl.get("title") or "",
             sub=sl.get("sub"), source=sl.get("source"))
    cols = [str(c) for c in (sl.get("columns") or [])][:6]
    # every row the plan carries, up to eight: a six-item read is six rows
    # (the cap was five, so a sixth category fell off the table unsaid)
    rows = [r for r in (sl.get("rows") or []) if isinstance(r, list)][:8]
    if not cols:
        return
    n = len(cols)
    widths, wordy = _table_layout(cols, rows)
    xs = [float(M) / 914400.0]
    for i in range(1, n):
        xs.append(xs[i - 1] + widths[i - 1])

    def box(i):
        """(x, width) of a cell's text box: every column after the first
        keeps a gutter on its left."""
        pad = 0.0 if i == 0 else _TABLE_PAD
        return xs[i] + pad, max(widths[i] - pad, 0.6)

    def align(i):
        return PP_ALIGN.LEFT if (i == 0 or wordy[i]) else PP_ALIGN.RIGHT

    top = d.content_top
    for i, c in enumerate(cols):
        bx, bw = box(i)
        txt(s, Inches(bx), Inches(top), Inches(bw),
            Inches(0.24), c.upper(), size=10.5, bold=True,
            color=MUTED_LT, spc=100, align=align(i))
    rect(s, M, Inches(top + 0.25), BAND, Pt(0.75), TRACK)
    acc_row = sl.get("accent_row")
    acc_col = sl.get("accent_col")
    y = top + 0.39
    has_read = bool(sl.get("read") or sl.get("read2"))
    # the read cards end above the source line (or the footer); the rows end above the cards
    card_h = 1.10 if (sl.get("read") and sl.get("read2")) else 0.92
    card_end = 6.56 if sl.get("source") else 6.98
    floor_y = (card_end - card_h - 0.12) if has_read else (
        _content_floor(sl) if sl.get("source") else 6.88)
    # the row pitch closes up before a row is ever lost: 0.42 in. by
    # default, down to 0.31 when the table is long or the page starts low
    min_h = next((h for h in (0.42, 0.36, 0.31) if h * max(len(rows), 1) <= floor_y - y + 1e-6), 0.31)
    avail = max(floor_y - y, min_h * max(len(rows), 1))
    grid = [[str(row[ci]) if ci < len(row) else "" for ci in range(n)]
            for row in rows]

    def heights(size):
        return [max([min_h] + [_block_h(grid[ri][ci], size, box(ci)[1]) + (0.10 if min_h >= 0.40 else 0.06)
                               for ci in range(n)])
                for ri in range(len(grid))]

    # one size for the whole table: the largest on the scale at which every
    # cell wraps inside its column and the rows fit above the read
    c_size = _TABLE_SIZES[-1]
    for cand in _TABLE_SIZES:
        if sum(heights(cand)) <= avail + 1e-6:
            c_size = cand
            break
    else:
        # still too tall at the smallest size: the longest cells are
        # shortened at a clause or a word, never inside one
        budget = avail / max(len(grid), 1)
        for ri in range(len(grid)):
            for ci in range(n):
                bw = box(ci)[1]
                grid[ri][ci] = _shorten_whole(
                    grid[ri][ci],
                    lambda t, _w=bw: _block_h(t, c_size, _w) <= budget - 0.10)
    row_hs = heights(c_size)
    for ri in range(len(grid)):
        row_acc = isinstance(acc_row, int) and ri == acc_row
        row_h = row_hs[ri]
        for ci in range(n):
            cell = grid[ri][ci]
            cell_acc = row_acc and isinstance(acc_col, int) \
                and ci == acc_col
            bx, bw = box(ci)
            txt(s, Inches(bx), Inches(y), Inches(bw),
                Inches(row_h - 0.06), cell, size=c_size,
                bold=(ci == 0 and row_acc) or cell_acc,
                color=OLIVE if cell_acc else GRAPHITE,
                align=align(ci))
        y += row_h
    reads = [t for t in [sl.get("read"), sl.get("read2")] if t]
    ry = max(min(5.50, card_end - card_h), y + 0.12)
    if len(reads) == 2:
        rrect(s, M, Inches(ry), Inches(5.607), Inches(1.10), CARD)
        txt(s, M + Inches(0.24), Inches(ry + 0.16), Inches(5.12),
            Inches(0.86), reads[0], size=13, color=BODY_LT)
        rrect(s, M + Inches(5.607) + GUT, Inches(ry), Inches(5.607),
              Inches(1.10), CARD)
        # the second card's copy sits inside its card like the first
        # (it was set flush on the card's left edge)
        txt(s, M + Inches(5.607) + GUT + Inches(0.24), Inches(ry + 0.16),
            Inches(5.12), Inches(0.86), reads[1], size=13, color=BODY_LT)
    elif reads:
        rrect(s, M, Inches(ry), BAND, Inches(0.92), CARD)
        txt(s, M + Inches(0.24), Inches(ry + 0.16), BAND - Inches(0.48),
            Inches(0.64), reads[0], size=14, color=BODY_LT)


def _sl_hero_proof(d, sl):
    s = d.new_slide(dark=True)
    if sl.get("_photo"):
        _photo_moment(s, sl["_photo"], 70, "hero")
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
    proofs = _items(sl, "proofs", 3)
    pitch = max(1.12, min(1.32, (_content_floor(sl) - top + 0.14) / max(len(proofs), 1)))
    p_h = pitch - 0.14
    for p in proofs:
        rrect(s, rx, Inches(y), Inches(5.607), Inches(p_h), SLATE)
        txt(s, rx + Inches(0.24), Inches(y + 0.16), Inches(5.12),
            Inches(0.44), p.get("fig") or "", size=22, bold=True,
            color=WHITE)
        txt(s, rx + Inches(0.24), Inches(y + min(0.64, p_h - 0.40)), Inches(5.12),
            Inches(0.36), p.get("label") or "", size=11.5,
            color=BODY_DK)
        y += pitch


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
    if not (sl.get("_photo") and _photo_moment(s, sl["_photo"], 70, "close")):
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


def _name_marked(text):
    """The line with each protected name (a title, a brand or a person's
    name from the read's own data) swapped for one token that carries a
    digit: a name said on three pages is a name, not a reused phrase."""
    ds = _design()
    mod = ds.names_module() if ds is not None and hasattr(ds, "names_module") else None
    if mod is None or not text:
        return text
    try:
        sp = mod.spans(str(text))
    except Exception:
        return text
    if not sp:
        return text
    t, out, last = str(text), [], 0
    for a, b in sp:
        out.append(t[last:a])
        out.append(" n4me ")
        last = b
    out.append(t[last:])
    return "".join(out)


def _grams(text):
    toks = _ptoks(_name_marked(text))
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
        dec = _row_decimals(rows)

        def val(r):
            return _fmt_val(r.get("value"), suffix, dec)
        top, sec, bot = rows[0], (rows[1] if len(rows) > 1 else None), (rows[-1] if len(rows) > 1 else None)
        idx = _fmt_index(top.get("index")) if sl.get("show_index") else ""
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
    t = str(sl.get("type") or "").lower()
    if t == "table":
        cols = [str(c).strip() for c in (sl.get("columns") or []) if str(c).strip()]
        if len(cols) >= 2:
            first, rest = _plain_label(cols[0]), [_plain_label(c) for c in cols[1:]]
            listed = rest[0] if len(rest) == 1 else ", ".join(rest[:-1]) + " and " + rest[-1]
            variants = [f"{listed[0].upper()}{listed[1:]} for each {first}.",
                        f"Each {first} by {listed}."]
            return variants[k % len(variants)]
    if t == "paths":
        kinds = []
        for r in (sl.get("rows") or []):
            kd = str((r or {}).get("kind") or "").strip().lower() if isinstance(r, dict) else ""
            if kd and kd not in kinds:
                kinds.append(kd)
        if len(kinds) >= 2:
            variants = [f"Example sessions from the window: {', then '.join(kinds)}.",
                        f"What a session looks like, step by step: {', '.join(kinds)}."]
            return variants[k % len(variants)]
        return "Example sessions from the window, one step per row."
    # nothing on the slide to name: no subhead reads better than a
    # one-word fragment ("Size.", "Paths."), so the caller drops it
    return ""


def _plain_label(label):
    """A column header as running words: "US VIEWERS" -> "US viewers",
    "Share Of Viewing" -> "share of viewing". Short all-caps tokens (US,
    CTR, AOV) and names with a capital inside (YouTube) keep their case."""
    out = []
    for w in str(label or "").split():
        core = re.sub(r"[^A-Za-z]", "", w)
        if (core.isupper() and len(core) <= 3 and core not in ("OF", "TO", "AND", "THE", "OR", "VS", "PER", "BY", "IN", "ON")) \
                or re.search(r"[a-z][A-Z]", w):
            out.append(w)
        else:
            out.append(w.lower())
    return " ".join(out)


def _is_fragment_sub(text):
    """A subhead that is not one: under three words ("Size.", "Paths.",
    "Buy: beauty."). A subhead is a plain sentence or a content label."""
    return len(_words_of(text)) < 3


def retire_fragment_subs(slides):
    """Replace a fragment subhead with one that names the slide's own
    content, or drop it when the slide has nothing to name (2026-11-11:
    "Size." and "Paths." shipped as subheads). Returns notes."""
    notes = []
    for i, sl in enumerate(slides):
        sub = sl.get("sub")
        if not isinstance(sub, str) or not sub.strip() or not _is_fragment_sub(sub):
            continue
        lead = _sub_lead(sub, sl)
        new = _content_sub(sl, lead, i)
        if new and not _is_fragment_sub(new):
            sl["sub"] = new
            notes.append(f"slide {i + 1}: one-word subhead replaced with the slide's own content")
        else:
            sl.pop("sub", None)
            notes.append(f"slide {i + 1}: one-word subhead dropped")
    return notes


def _map_plan_strings(slides, fn, skip_keys=("url", "type", "ground", "kind", "value_suffix")):
    """Apply ``fn`` to every written string of the plan (titles, subs,
    reads, card and tile copy, table cells, column heads), in place.
    Addresses, slide types and other machine fields are left alone.
    Returns how many strings changed."""
    n = 0

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if k in skip_keys or str(k).startswith("_"):
                    continue
                if isinstance(v, str):
                    new = fn(v)
                    if isinstance(new, str) and new != v:
                        o[k] = new
                        n += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for j, v in enumerate(o):
                if isinstance(v, str):
                    new = fn(v)
                    if isinstance(new, str) and new != v:
                        o[j] = new
                        n += 1
                else:
                    walk(v)
    walk(slides)
    return n


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


def dedupe_plan_copy(slides, thin_phrases=True):
    """Retire repeated phrases in a slide plan, in place. Returns notes
    (one line per rewrite) for the job record. ``thin_phrases=False``
    stops after the subhead templates."""
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
            new = _content_sub(slides[idx], _sub_lead(old, slides[idx]), k)
            if new and not _is_fragment_sub(new):
                slides[idx]["sub"] = new
                notes.append(f"slide {idx + 1}: subhead template retired, names its own content")
            else:
                slides[idx].pop("sub", None)
                notes.append(f"slide {idx + 1}: subhead template retired, nothing of its own to name, dropped")
    if not thin_phrases:
        return notes
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
                    if new is not None and _is_fragment_sub(new):
                        new = ""        # dropped: never a one-word subhead
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


# ---- shares and the counts beside them (2026-11-11) -------------------------
# A page printed "Comedy 6,579,974 ... 3.0% penetration" under "of US
# people": 6,579,974 over the US is 2.0%, and the 3.0% came from a base
# nowhere on the page. Before a page is written, every share that sits
# beside a count is checked against the base its own label names:
#
#   * "of US people", "US penetration", "against the US": the count over
#     the US population the projections use (329.9M).
#   * "of the audience", "of viewers": the count over the deck's projected
#     US audience (the universe page, else the cover).
#   * any other share ("of viewing", "of sessions"): the rows must agree
#     on one base, and that base must be a count printed in the deck.
#
# A share whose base is named and does not match its count is recomputed
# from the count, to the precision it was written at, and the same figure
# is corrected wherever the deck repeats it for the same row. A share
# whose base cannot be told is left as written and noted for the job
# record. Counts are never changed: the count is what was measured.

US_PEOPLE = 329_900_000
_BASE_COUNT_RX = re.compile(r"(?<![\d.,$%])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s?[MK](?![A-Za-z]))(?![\d,]*\.?\d*%)")
_BASE_PCT_RX = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)%")
_US_WORDS_RX = re.compile(r"\b(U\.?S\.?|american|americans|national|nationwide|country|population)\b", re.I)
_US_CASE_RX = re.compile(r"\bUS\b|\bU\.S\.")
_AUD_WORDS_RX = re.compile(
    r"\bof (?:the |this |these |all |its |their )?(?:total )?(?:audience|viewers|fans|users|engagers|listeners|"
    r"players|subscribers|shoppers|buyers|universe|file)\b", re.I)
_PEN_WORDS_RX = re.compile(r"\b(penetration|reach|reaches|reached)\b", re.I)


def _parse_count(text):
    """(value, relative precision) of a count as written: '6,579,974' is
    exact, '6.6M' is good to half its last digit. None when not a count."""
    t = str(text or "").strip().replace("\u00a0", " ")
    m = re.fullmatch(r"(\d{1,3}(?:,\d{3})+|\d{4,})", t)
    if m:
        return float(m.group(1).replace(",", "")), 0.0
    m = re.fullmatch(r"(\d+(?:\.(\d+))?)\s?([MK])", t)
    if m:
        mult = 1_000_000.0 if m.group(3) == "M" else 1_000.0
        num = float(m.group(1))
        half = 0.5 * 10 ** (-len(m.group(2) or ""))
        return num * mult, (half / num if num else 0.0)
    return None


def _pct_decimals(txt_pct):
    return len(txt_pct.split(".", 1)[1]) if "." in txt_pct else 0


def _fmt_pct(value, decimals):
    return f"{value:.{int(decimals)}f}"


def _label_key(label):
    return [w for w in re.sub(r"[^a-z0-9]+", " ", str(label or "").lower()).split()
            if w not in ("the", "a", "an", "and", "of", "for", "us")]


def _labels_match(a, b):
    ka, kb = _label_key(a), _label_key(b)
    if not ka or not kb:
        return False
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return short == long_[:len(short)] or (len(short) >= 2 and all(w in long_ for w in short))


def _slide_context(sl):
    return " ".join(str(sl.get(k) or "") for k in ("eyebrow", "title", "sub", "read", "bars_title", "source"))


_US_CTX_RX = re.compile(
    r"\bUS (?:penetration|people|population|adults|reach)\b|\bof (?:the )?US\b|\bagainst the US\b|"
    r"\bof Americans\b|\bof the country\b")


def _share_bases(descriptor, context, audience, count_label=""):
    """The bases a share could be measured against, from the words that
    follow the percent (and, for a bare 'penetration', the slide around
    it and the label on the count beside it): [(base, name, explicit)].
    Explicit means the words name the base ("of US people", "of the
    audience"); a bare 'penetration' could be the deck's audience or,
    on a page about the US or beside a count of US people, the US."""
    d = str(descriptor or "")
    if _AUD_WORDS_RX.search(d):
        return [(audience[0], "the deck's audience", True)] if audience else []
    if _US_CASE_RX.search(d) or re.search(r"\b(americans?|national|nationwide|country|population)\b", d, re.I):
        return [(float(US_PEOPLE), "the US", True)]
    us_ctx = bool(_US_CTX_RX.search(context or "")) or bool(_US_CASE_RX.search(str(count_label or "")))
    pen = bool(re.search(r"\bpenetration\b", d, re.I))
    out = []
    if pen or (not d.strip() and us_ctx and _PEN_WORDS_RX.search(context or "")):
        if audience and pen:
            out.append((audience[0], "the deck's audience", False))
        if us_ctx:
            out.append((float(US_PEOPLE), "the US", False))
    return out


def _resolve_share(pct_txt, count, bases, aud_prec=0.0):
    """None when the written share agrees with a base it could be measured
    against, or when no base can be told. Otherwise (corrected percent
    text, base name); the text is '' when the base is only implied and the
    written figure is too far from it to call a miscount (left as
    written, noted)."""
    import math
    try:
        pct = float(pct_txt)
    except (TypeError, ValueError):
        return None
    if not bases or pct <= 0:
        return None
    best = None
    for base, name, explicit in bases:
        prec = count[1] + (aud_prec if "audience" in name else 0.0)
        exp, off = _share_off(count[0], prec, base, pct_txt)
        if not off:
            return None
        if exp <= 0 or exp >= 100.5:
            continue
        gap = abs(math.log(pct / exp))
        if best is None or gap < best[0]:
            best = (gap, exp, name, explicit)
    if best is None:
        return None
    gap, exp, name, explicit = best
    if not explicit and gap > math.log(2.5):
        return "", name
    return _fmt_pct(exp, max(_pct_decimals(pct_txt), 1)), name


def _deck_audience(slides):
    """The deck's projected US audience as a count: the first figure on
    the universe page (tiles_facts), else the first cover stat that is a
    count. (value, relative precision) or None."""
    for want in ("tiles_facts", "cover"):
        for sl in slides:
            if str(sl.get("type") or "").lower() != want:
                continue
            for it in (sl.get("tiles") or sl.get("stats") or []):
                if isinstance(it, dict):
                    got = _parse_count(it.get("big"))
                    if got:
                        return got
    return None


def _deck_ledger(slides):
    """{label: (count, precision)} from every row that pairs a label with
    a count: fact rows, table rows, bars of counts."""
    led = []
    for sl in slides:
        for f in (sl.get("facts") or []):
            if isinstance(f, dict) and f.get("label"):
                got = _parse_count(f.get("fig"))
                if got:
                    led.append((str(f["label"]), got))
        t = str(sl.get("type") or "").lower()
        if t == "table":
            for row in (sl.get("rows") or []):
                if isinstance(row, list) and len(row) >= 2:
                    for cell in row[1:]:
                        got = _parse_count(cell)
                        if got:
                            led.append((str(row[0]), got))
                            break
        if t in ("bars", "split_stats_bars") and str(sl.get("value_suffix") or "%") == "":
            for r in (sl.get("rows") or []):
                if isinstance(r, dict) and r.get("label") and _num(r.get("value")) >= 1000:
                    led.append((str(r["label"]), (_num(r.get("value")), 0.0)))
    return led


def _ledger_count(ledger, label):
    hits = {c for lab, c in ledger if _labels_match(lab, label)}
    exact = [c for lab, c in ledger if _label_key(lab) == _label_key(label)]
    if exact:
        return sorted(exact, key=lambda c: c[1])[0]
    return hits.pop() if len(hits) == 1 else None


def _share_off(count, prec, base, pct_txt):
    """(expected percent, True when the written percent is off) for a
    count over a base, within the rounding the figures were written to."""
    pct = float(pct_txt)
    exp = count / base * 100.0
    slack = 0.5 * 10 ** (-_pct_decimals(pct_txt)) + exp * (prec + 0.004) + 1e-9
    return exp, abs(exp - pct) > slack


def reconcile_page_bases(slides):
    """Check every share that sits beside a count against the base its
    label names, and correct the ones that do not match, in place. Returns
    (notes, changes): one note per correction or unverified share, and
    ``changes`` as [(slide index, label, old percent, new percent)]."""
    notes, changes = [], []
    audience = _deck_audience(slides)
    aud_prec = audience[1] if audience else 0.0
    ledger = _deck_ledger(slides)

    def settle(i, label, pct_txt, count, descriptor, ctx, count_label="", droppable=False):
        """The corrected percent text for one share; None to leave it; ''
        when it matches no base it could be measured against and the
        caller can take it off the page (``droppable``)."""
        got = _resolve_share(pct_txt, count, _share_bases(descriptor, ctx, audience, count_label), aud_prec)
        if got is None:
            return None
        new, name = got
        if not new:
            if droppable:
                notes.append(f"slide {i + 1}: {label}: {pct_txt}% matches no base beside {int(count[0]):,} "
                             "(taken off the page)")
                return ""
            notes.append(f"slide {i + 1}: {label}: {pct_txt}% does not match {int(count[0]):,} over {name} "
                         "and names no base (left as written)")
            return None
        if new == pct_txt:
            return None
        changes.append((i, label, pct_txt, new))
        notes.append(f"slide {i + 1}: {label}: {pct_txt}% recomputed to {new}% ({int(count[0]):,} over {name})")
        return new

    def fix_text(text, count, ctx, label, i, count_label=""):
        """Correct the shares in one string that sit beside ``count``; a
        share that matches no base comes out with its clause when the
        string has another clause to stand on."""
        out, pos = "", 0
        clauses = [c for c in re.split(r",\s+", text) if c.strip()]
        for m in _BASE_PCT_RX.finditer(text):
            tail = re.split(r"[,.;)]|\s+(?:and|while|but|against|versus|vs)\s", text[m.end():], 1)[0]
            new = settle(i, label, m.group(1), count, tail, ctx, count_label, droppable=len(clauses) >= 2)
            if new is None:
                continue
            if new == "":
                # the clause runs between the clause commas on either side
                # (a comma inside a figure, "188,901", is not one)
                before = [x.start() for x in re.finditer(r",\s", text[:m.start()])]
                after = re.search(r",\s", text[m.end():])
                a = before[-1] if before else -1
                b = m.end() + after.start() if after else len(text)
                if a >= max(pos, 0):
                    out += text[pos:a]
                    pos = b
                else:
                    pos = min(b + 2, len(text)) if after else len(text)
                continue
            out += text[pos:m.start()] + new + "%"
            pos = m.end()
        res = (out + text[pos:]) if pos else text
        if res == text:
            return text
        res = re.sub(r"\s{2,}", " ", res).strip(" ,")
        if text.rstrip().endswith(".") and res and not res.endswith("."):
            res += "."
        return res[:1].upper() + res[1:] if text[:1].isupper() else res

    for i, sl in enumerate(slides):
        ctx = _slide_context(sl)
        t = str(sl.get("type") or "").lower()
        # fact rows: the figure is the count, the note carries its shares
        for f in (sl.get("facts") or []):
            if not isinstance(f, dict):
                continue
            count = _parse_count(f.get("fig"))
            if count and isinstance(f.get("note"), str):
                f["note"] = fix_text(f["note"], count, ctx, str(f.get("label") or "this row"), i,
                                     count_label=str(f.get("label") or ""))
        # tiles and stat cards: a count with its share in the label
        for key in ("tiles", "stat_cards", "stats", "proofs"):
            for it in (sl.get(key) or []):
                if not isinstance(it, dict):
                    continue
                count = _parse_count(it.get("big") or it.get("fig"))
                if count and isinstance(it.get("label"), str):
                    it["label"] = fix_text(it["label"], count, ctx, it["label"][:40], i, count_label=it["label"])
        # tables: a count column and share columns named by their headers
        if t == "table":
            cols = [str(c) for c in (sl.get("columns") or [])]
            drop_cols = set()
            for row in (sl.get("rows") or []):
                if not isinstance(row, list) or len(row) < 3:
                    continue
                cc = next((k for k in range(1, len(row)) if _parse_count(row[k])), None)
                if cc is None:
                    continue
                count = _parse_count(row[cc])
                for ci in range(1, min(len(row), len(cols))):
                    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", str(row[ci]).strip())
                    if not m:
                        continue
                    new = settle(i, str(row[0]), m.group(1), count, cols[ci], ctx,
                                 count_label=cols[cc] if cc < len(cols) else "", droppable=len(cols) >= 4)
                    if new == "":
                        drop_cols.add(ci)
                    elif new is not None:
                        row[ci] = new + "%"
            for ci in sorted(drop_cols, reverse=True):      # a share column with no base: the column comes off
                for row in (sl.get("rows") or []):
                    if isinstance(row, list) and ci < len(row):
                        del row[ci]
                if ci < len(sl.get("columns") or []):
                    del sl["columns"][ci]
                if isinstance(sl.get("accent_col"), int):
                    sl["accent_col"] = None if sl["accent_col"] == ci else sl["accent_col"] - (1 if sl["accent_col"] > ci else 0)
        # bars of shares: the counts come from the rows of the same name elsewhere in the deck
        if t in ("bars", "split_stats_bars") and str(
                sl.get("value_suffix") if sl.get("value_suffix") is not None else "%") == "%":
            rows = [r for r in (sl.get("rows") or []) if isinstance(r, dict) and r.get("label")]
            dec = max(_row_decimals(rows), 1)
            for r in rows:
                count = _ledger_count(ledger, r["label"])
                if not count:
                    continue
                new = settle(i, str(r["label"]), _fmt_pct(_num(r.get("value")), dec), count, "", ctx)
                if new is not None:
                    r["value"] = float(new)
    # the same figure, wherever the deck repeats it for the same row
    if changes:
        touched = {i for i, _l, _o, _n in changes}
        by_pair = {}
        for _i, label, old, new in changes:
            by_pair.setdefault((tuple(_label_key(label)[:1]), old), new)

        def repaint(text, on_touched, about=()):
            """``about`` is the one corrected row a whole page is about
            (its header names that row and no other): a share on that page
            that speaks of the US or of penetration is that row's share."""
            if not isinstance(text, str) or "%" not in text:
                return text
            low = re.sub(r"[^a-z0-9]+", " ", text.lower())
            us_words = bool(_US_CASE_RX.search(text) or _PEN_WORDS_RX.search(text))
            if not (on_touched or us_words):
                return text
            out = text
            for (lab, old), new in by_pair.items():
                if lab and (re.search(rf"\b{re.escape(lab[0])}\b", low) or (us_words and lab == tuple(about))):
                    out = re.sub(rf"(?<![\d.,]){re.escape(old)}%", new + "%", out)
            return out

        labs = {lab for (lab, _old) in by_pair if lab}
        for i, sl in enumerate(slides):
            on = i in touched
            head = re.sub(r"[^a-z0-9]+", " ", " ".join(str(sl.get(k) or "") for k in ("eyebrow", "title", "sub")).lower())
            named = [lab for lab in labs if re.search(rf"\b{re.escape(lab[0])}\b", head)]
            about = named[0] if len(named) == 1 else ()
            for f in (sl.get("facts") or []):
                if isinstance(f, dict) and isinstance(f.get("fig"), str) and f["fig"].strip().endswith("%"):
                    ctx_f = " ".join(str(f.get(x) or "") for x in ("label", "note"))
                    f["fig"] = repaint(f["fig"].strip() + " " + ctx_f, on, about).split(" ", 1)[0]
            for k in ("title", "sub", "intro", "line", "support", "read", "read2"):
                if isinstance(sl.get(k), str):
                    sl[k] = repaint(sl[k], on, about)
            for key in ("tiles", "stat_cards", "stats", "proofs", "cards"):
                for it in (sl.get(key) or []):
                    if not isinstance(it, dict):
                        continue
                    lab_txt = " ".join(str(it.get(x) or "") for x in ("label", "kicker", "head", "body"))
                    for fld in ("label", "body"):
                        if isinstance(it.get(fld), str):
                            it[fld] = repaint(it[fld], on, about)
                    for fld in ("big", "fig"):
                        v = it.get(fld)
                        if isinstance(v, str) and v.strip().endswith("%"):
                            new = repaint(v.strip() + " " + lab_txt, on, about)
                            it[fld] = new.split(" ", 1)[0]
    # shares with a base the deck does not print: said for the record, left as written
    for i, sl in enumerate(slides):
        groups = {}
        for f in (sl.get("facts") or []):
            if not isinstance(f, dict):
                continue
            count = _parse_count(f.get("fig"))
            if not count or not isinstance(f.get("note"), str):
                continue
            for m in _BASE_PCT_RX.finditer(f["note"]):
                tail = re.split(r"[,.;)]", f["note"][m.end():], 1)[0].strip()
                if not _share_bases(tail, _slide_context(sl), audience, str(f.get("label") or "")) \
                        and float(m.group(1)) > 0:
                    groups.setdefault(tail.lower(), []).append(count[0] / (float(m.group(1)) / 100.0))
        printed = [c[0] for _lab, c in ledger]
        for it in (sl.get("tiles") or []):
            if isinstance(it, dict) and _parse_count(it.get("big")):
                printed.append(_parse_count(it.get("big"))[0])
        if audience:
            printed.append(audience[0])
        for tail, bases in groups.items():
            if len(bases) < 2:
                continue
            mid = sorted(bases)[len(bases) // 2]
            if max(bases) > mid * 1.04 or min(bases) < mid * 0.96:
                notes.append(f"slide {i + 1}: shares '{tail}' do not share one base across the rows (left as written)")
            elif not any(abs(c - mid) <= mid * 0.03 for c in printed):
                notes.append(f"slide {i + 1}: shares '{tail}' use a base of about {int(mid):,} "
                             f"that the deck does not print (left as written)")
    return notes, changes


def assert_page_bases(slides):
    """The check after the correction: [] when every share that names its
    base agrees with the count beside it. Run on a copy; nothing changes."""
    probe = copy.deepcopy(slides)
    _notes, changes = reconcile_page_bases(probe)
    return [f"slide {i + 1}: {label}: {old}% should read {new}%" for i, label, old, new in changes]


# ---- parts and the total printed for them (2026-11-11) ------------------------
# A deck printed 28.2% and 22.2% and said the two hold 50.5%. The read's
# 50.5 was a rounded sum of unrounded parts; on a page that prints the
# parts to one decimal the reader adds 50.4. The printed total is the
# sum of the printed parts.
_SUM_PCT_RX = re.compile(r"(?<![\d.,$])(\d{1,3}(?:\.\d+)?)%")
_REST_RX = re.compile(r"\b(?:remaining|the rest|the other|the others|everything else|the balance|what is left)\b", re.I)
_SERIES_STOP = {"the", "a", "an", "and", "of", "for", "us", "arts", "culture", "all", "other", "others", "total"}


def _share_series(slides):
    """Every set of labelled shares the deck prints side by side:
    [{label: (percent text, decimals)}]. Percent bars, a percent column
    of a table, fact rows whose figure is a percent."""
    out = []
    for sl in slides:
        t = str(sl.get("type") or "").lower()
        if t in ("bars", "split_stats_bars") and str(
                sl.get("value_suffix") if sl.get("value_suffix") is not None else "%") == "%":
            rows = [r for r in (sl.get("rows") or []) if isinstance(r, dict) and r.get("label")]
            dec = max(_row_decimals(rows), 1)
            ser = {}
            for r in rows:
                try:
                    ser[str(r["label"])] = (_fmt_pct(float(r.get("value")), dec), dec)
                except (TypeError, ValueError):
                    continue
            if len(ser) >= 2:
                out.append(ser)
        if t == "table":
            rows = [r for r in (sl.get("rows") or []) if isinstance(r, list) and len(r) >= 2]
            width = max((len(r) for r in rows), default=0)
            for ci in range(1, width):
                ser = {}
                for r in rows:
                    m = re.fullmatch(r"(\d{1,3}(?:\.(\d+))?)%", str(r[ci]).strip()) if ci < len(r) else None
                    if m:
                        ser[str(r[0])] = (m.group(1), len(m.group(2) or ""))
                if len(ser) >= 2 and len(ser) == len(rows):
                    out.append(ser)
        ser = {}
        for f in (sl.get("facts") or []):
            if isinstance(f, dict) and f.get("label"):
                m = re.fullmatch(r"(\d{1,3}(?:\.(\d+))?)%", str(f.get("fig") or "").strip())
                if m:
                    ser[str(f["label"])] = (m.group(1), len(m.group(2) or ""))
        if len(ser) >= 2:
            out.append(ser)
    return out


def _series_names(ser):
    """{label: the words that name it in running text}: the whole label,
    and its first word when no other row of the set opens on it."""
    firsts = {}
    for lab in ser:
        key = [w for w in re.sub(r"[^a-z0-9]+", " ", lab.lower()).split() if w not in _SERIES_STOP]
        if key:
            firsts.setdefault(key[0], []).append(lab)
    names = {}
    for lab in ser:
        norm = " ".join(re.sub(r"[^a-z0-9]+", " ", lab.lower()).split())
        key = [w for w in norm.split() if w not in _SERIES_STOP]
        forms = [norm] if norm else []
        if key and len(key[0]) >= 4 and len(firsts.get(key[0]) or []) == 1:
            forms.append(key[0])
        names[lab] = forms
    return names


def _named_in(text, names):
    low = " " + " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()) + " "
    return [lab for lab, forms in names.items() if any(f" {f} " in low for f in forms)]


def _sum_fix(text, pct_txt, series):
    """The total ``pct_txt`` should print as, given the labels ``text``
    names: the sum of those parts as printed (or, for "the remaining",
    the sum of the parts it does not name). None when the figure is not a
    total of printed parts, or already agrees with them."""
    try:
        val = float(pct_txt)
    except (TypeError, ValueError):
        return None
    dec = len(pct_txt.split(".", 1)[1]) if "." in pct_txt else 0
    found = set()
    for ser in series:
        if any(d != dec for _v, d in ser.values()):
            continue
        named = _named_in(text, _series_names(ser))
        if len(named) < 2 and not (named and _REST_RX.search(text)):
            continue
        if any(ser[lab][0] == pct_txt for lab in named):
            continue          # the figure is one of the parts, not their total
        from itertools import combinations
        groups = []
        for k in range(2, min(len(named), 6) + 1):
            groups += [list(c) for c in combinations(named, k)]
        if _REST_RX.search(text):
            rest = [lab for lab in ser if lab not in named]
            if len(rest) >= 2:
                groups.append(rest)
        for g in groups:
            total = sum(float(ser[lab][0]) for lab in g)
            tol = 0.5 * (10 ** -dec) * len(g) + 0.011 * (10 ** -dec)
            if abs(total - val) <= tol:
                found.add((_fmt_pct(total, dec), tuple(g), " + ".join(f"{ser[lab][0]}%" for lab in g)))
    if not found:
        return None
    totals = {t for t, _g, _p in found}
    if len(totals) != 1 or pct_txt in totals:
        return None           # it agrees with its parts, or two readings disagree: leave it
    new, _g, parts = sorted(found)[0]
    return new, parts


def reconcile_printed_sums(slides):
    """Shares shown side by side add to the total printed for them, in
    place: a total that names its parts is the sum of those parts as the
    deck prints them. Returns notes, one per corrected total."""
    series = _share_series(slides)
    notes = []
    if not series:
        return notes

    for i, sl in enumerate(slides):
        def fix(text, _i=i):
            if not isinstance(text, str) or "%" not in text:
                return text
            out, pos = "", 0
            for m in _SUM_PCT_RX.finditer(text):
                got = _sum_fix(text, m.group(1), series)
                if not got:
                    continue
                new, parts = got
                out += text[pos:m.start()] + new + "%"
                pos = m.end()
                notes.append(f"slide {_i + 1}: {m.group(1)}% set to {new}% ({parts} as printed)")
            return (out + text[pos:]) if pos else text
        _map_plan_strings([sl], fix)
        # the page's own large figure, read against the lines that say what it is
        for fk in ("big",):
            m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)%", str(sl.get(fk) or "").strip())
            if m:
                got = _sum_fix(" ".join(str(sl.get(k) or "") for k in ("title", "sub", "line", "support")), m.group(1), series)
                if got:
                    sl[fk] = got[0] + "%"
                    notes.append(f"slide {i + 1}: {m.group(1)}% set to {got[0]}% ({got[1]} as printed)")
        # a figure set large over a label that names the parts
        for key in ("tiles", "stat_cards", "stats", "proofs"):
            for it in (sl.get(key) or []):
                if not isinstance(it, dict):
                    continue
                fk = "big" if "big" in it else ("fig" if "fig" in it else None)
                m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)%", str(it.get(fk) or "").strip()) if fk else None
                if not m:
                    continue
                got = _sum_fix(" ".join(str(it.get(k) or "") for k in ("label", "kicker", "body")), m.group(1), series)
                if got:
                    it[fk] = got[0] + "%"
                    notes.append(f"slide {i + 1}: {m.group(1)}% set to {got[0]}% ({got[1]} as printed)")
    return notes


def assert_printed_sums(slides):
    """[] when every total that names its parts adds from them as printed.
    Run on a copy; nothing changes."""
    return reconcile_printed_sums(copy.deepcopy(slides))


# ---- a deck carries only what the read holds (2026-11-11) ---------------------
def _addr_key(text):
    """An address or a search as it is compared: lower case, no scheme, no
    www, no trailing slash, spaces and plus signs alike."""
    t = str(text or "").strip().lower()
    t = re.sub(r"^[a-z]+://", "", t)
    t = re.sub(r"^www\.", "", t)
    t = t.replace("%20", " ").replace("+", " ")
    return re.sub(r"\s+", " ", t).strip(" /.")


def hold_paths_to_read(slides, read_text):
    """A page of web addresses carries only addresses the read's own data
    holds, in place. A row whose address is not in ``read_text`` comes
    off; a page left with fewer than three rows comes off whole (the
    builder used to add a standard page of example addresses that were
    in no read). Returns notes."""
    notes = []
    src = _addr_key(read_text)
    src = re.sub(r"(?<=[\s(])www\.", "", src)
    keep = []
    for i, sl in enumerate(slides):
        if str(sl.get("type") or "").strip().lower() != "paths":
            keep.append(sl)
            continue
        rows = [r for r in (sl.get("rows") or []) if isinstance(r, dict)]
        held = [r for r in rows if len(_addr_key(r.get("url"))) >= 6 and _addr_key(r.get("url")) in src]
        if len(held) >= 3:
            if len(held) != len(rows):
                notes.append(f"slide {i + 1}: {len(rows) - len(held)} address row(s) the read does not hold came off")
            sl["rows"] = held
            keep.append(sl)
        else:
            notes.append(f"slide {i + 1}: the addresses page came off (its addresses are not in the read)")
    if len(keep) != len(slides):
        slides[:] = keep
    return notes


def _ensure_titles(slides, deck_title=""):
    """Every page carries a headline (2026-11-11: a plan came back with a
    close page that had none, and the page was laid with a blank band).
    A page without a title takes its subhead when that is a short
    sentence; a cover or a close takes the deck's own title; any other
    page says what leads its own rows. Returns notes."""
    notes = []
    deck_title = str(deck_title or "").strip()
    for i, sl in enumerate(slides):
        if str(sl.get("title") or "").strip():
            continue
        t = str(sl.get("type") or "").lower()
        sub = str(sl.get("sub") or "").strip()
        title = ""
        if t == "cover" and deck_title:
            title = deck_title
        elif sub and 3 <= len(sub.split()) <= 12 and ":" not in sub:
            title = sub
            sl.pop("sub", None)
        elif t == "close":
            title = deck_title or "What to take from this read."
        else:
            f = _lead_fact(sl)
            if f and f[0] == "ranked":
                title = f"{f[1]} leads at {f[2]}."
            elif f and f[0] == "tile":
                title = f"{f[1]} reads {f[2]}."
            elif f and f[0] == "card":
                title = f"{f[1]}."
        if title:
            sl["title"] = title if title.endswith((".", "!", "?")) else title + "."
            notes.append(f"slide {i + 1}: the page had no headline; it takes one from its own content")
    return notes


# ---- plan copy, cleaned before layout (2026-11-11) ---------------------------
def _clean_plan_copy(slides):
    """Stock filler out ("does the work"), joining words a title-caser
    raised back down ("25 To 34"), fragment subheads replaced or dropped.
    Returns notes for the job record."""
    notes = []
    ds = _design()
    if ds is not None:
        try:
            n = _map_plan_strings(slides, ds.scrub_stock_phrases)
            if n:
                notes.append(f"stock filler replaced in {n} line(s)")
            n = _map_plan_strings(slides, ds.fix_label_casing, skip_keys=("url", "type", "ground", "kind",
                                                                         "value_suffix", "eyebrow", "bars_title"))
            if n:
                notes.append(f"label casing corrected in {n} line(s)")
        except Exception:
            pass
    try:
        notes += retire_fragment_subs(slides)
    except Exception:
        pass
    return notes


def _norm_copy(text):
    return re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip()


def _apply_copy_edits_to_plan(slides, edits):
    """Write the copy repairs made on the rendered deck back into the plan
    (so the deck is laid out again on the repaired words): each edit is
    (label, old text, new text) as the check read it. Returns how many
    plan strings changed."""
    if not edits:
        return 0
    table = {}
    for _lab, old, new in edits:
        table[_norm_copy(old)] = new
    ds = _design()

    def as_rendered(v):
        t = v
        if ds is not None:
            try:
                t = ds.fix_label_casing(ds.scrub_door_metaphor(t))
            except Exception:
                t = v
        return _norm_copy(t)

    def fn(v):
        key = as_rendered(v)
        hops = 0
        out = None
        while key in table and hops < 6:        # a line repaired twice: follow the chain
            out = table[key]
            key = _norm_copy(out)
            hops += 1
        return out if out is not None else v

    return _map_plan_strings(slides, fn)


def render_insights_deck(plan, out_path, static_dir=None,
                        photo_subject='', photo_kind='', copy_rewriter=None,
                        protected_names=None, read_text=None):
    """Render a slide-plan dict to a finished PPTX at out_path.
    Returns the number of slides rendered.

    ``protected_names`` are the titles, brands and people's names from
    the read's own data: in force for the whole build, so no spelling,
    casing or word-ban pass reads inside one or rewrites one, and written
    into the finished file for the mail gate. ``read_text`` is the read's
    own data as text: a page of web addresses keeps only the addresses
    found in it (with no ``read_text`` the page is left as the plan has
    it; the deck lane always passes it)."""
    ds = _design()
    prev = None
    if ds is not None:
        try:
            prev = ds.protected_names()
            ds.set_protected_names(list(protected_names or []) + list(prev or []))
        except Exception:
            prev = None
    try:
        return _render_insights_deck(plan, out_path, static_dir, photo_subject, photo_kind, copy_rewriter,
                                     list(protected_names or []), read_text)
    finally:
        if ds is not None and prev is not None:
            try:
                ds.set_protected_names(prev)
            except Exception:
                pass


def _render_insights_deck(plan, out_path, static_dir, photo_subject, photo_kind, copy_rewriter,
                          protected_names, read_text):
    """The build behind ``render_insights_deck``.

    Photography (2026-09-30): the plan's image_subject (or the
    caller's photo_subject fallback) resolves to real photos placed
    under a graphite scrim on the cover, the first hero or
    hero_proof, and the close - the manual deck grammar. A slide
    carrying photo: false opts out; no photos means text-only.

    The deck comes out clean with no hand repair (2026-11-11). Before
    layout the plan's copy is cleaned (stock filler, label casing,
    fragment subheads, repeated phrases) and every share that sits
    beside a count is checked against the base its label names. After
    the first layout the plain-English check reads the deck; whatever it
    flags is repaired on the words (``copy_rewriter``, a model pass over
    only the flagged text, then the fixed repairs), the repairs go back
    into the plan and the deck is laid out again on them. The finisher
    then embeds, lints and checks the file as it stands."""
    lw, lk = _resolve_logos(static_dir)
    # Render from copies: the caller's plan is never mutated, so a
    # repeat render of the same plan is deterministic.
    _slides = [copy.deepcopy(sl) for sl in (plan.get("slides") or [])
               if isinstance(sl, dict)]
    notes = []
    if read_text is not None:
        try:
            notes += hold_paths_to_read(_slides, read_text)
        except Exception:
            pass
    try:
        notes += _clean_plan_copy(_slides)
    except Exception:
        pass
    # Repeated phrases come off before anything is laid out (2026-11-10):
    # one subhead template across slides, a closer reused three times.
    ds = _design()
    _rewriter = copy_rewriter
    if _rewriter is None and ds is not None:
        try:
            _rewriter = ds.copy_rewriter()
        except Exception:
            _rewriter = None
    try:
        notes += _ensure_titles(_slides, str(plan.get("title") or ""))
    except Exception:
        pass
    # With no model pass at hand the fixed rewrites run now. With one, the
    # flagged lines go to it after the first layout, and these run only on
    # what it leaves (a subhead rebuilt by rule from column names reads
    # worse than the same line reworded).
    if _rewriter is None:
        try:
            notes += dedupe_plan_copy(_slides)
        except Exception:
            pass
    # Shares agree with the counts beside them before a page is written.
    base_notes, base_left = [], []
    try:
        base_notes, _changes = reconcile_page_bases(_slides)
        base_left = assert_page_bases(_slides)
    except Exception:
        pass
    # ... and the parts printed side by side add to the total printed for them.
    sum_notes, sum_left = [], []
    try:
        sum_notes = reconcile_printed_sums(_slides)
        sum_left = assert_printed_sums(_slides)
    except Exception:
        pass
    plan['_copy_notes'] = list(notes)
    try:
        _p_subj = str(plan.get("image_subject")
                      or photo_subject or '').strip()
        _p_kind = str(plan.get("image_kind")
                      or photo_kind or 'person').strip().lower()
        photos = _deck_photos(_p_subj, _p_kind) if _p_subj else []
        plan['_photo_subject'] = _p_subj
        plan['_photo_sources'] = list(_PHOTO_SOURCES.get(_p_subj.lower()) or [])
        plan['_photo_used'] = list(_PHOTO_USED.get(_p_subj.lower()) or [])
        plan['_photo_rejected'] = [f"{u}: {why}" for u, why in (_PHOTO_REJECTS.get(_p_subj.lower()) or [])][:8]
        print(f"[deck] photo subject {_p_subj!r} ({_p_kind}); "
              f"{len(photos)} photo(s) from {(plan['_photo_used'] or plan['_photo_sources'])[:3]}"
              + (f"; turned away {len(plan['_photo_rejected'])}" if plan['_photo_rejected'] else ""))
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
    # The pack asks every deck for one alternative color page. A plan
    # whose only statement pages are dark gets its first one that carries
    # no photograph on the accent ground (the hero page's own default).
    try:
        heroes = [sl for sl in _slides if str(sl.get("type") or "").lower() == "hero"]
        if heroes and all(str(sl.get("ground") or "accent").lower() == "dark" for sl in heroes):
            free = next((sl for sl in heroes if not sl.get("_photo")), None)
            if free is not None:
                free["ground"] = "accent"
                notes.append("a statement page set on the accent ground (every deck carries one color page)")
    except Exception:
        pass

    def _build(path):
        prs = Presentation()
        prs.slide_width = SW
        prs.slide_height = SH
        d = _Deck(prs, lw, lk)
        n = 0
        for sl in _slides:
            fn = _RENDERERS.get(str(sl.get("type") or "").strip().lower())
            if fn is None:
                continue
            fn(d, sl)
            n += 1
        prs.save(path)
        _apply_theme_faces(path)
        return n

    rendered = _build(out_path)
    # The plain-English check reads the laid-out deck; its repairs go back
    # into the plan and the deck is laid out again on the repaired words,
    # so the type is fitted to what ships.
    if ds is not None:
        probe = f"{out_path}.copyfix.pptx"
        try:
            mod = ds.copy_fix_module()
            if mod is not None:
                import shutil as _sh
                _sh.copyfile(out_path, probe)
                if _rewriter is not None:
                    res = mod.fix_file(probe, _rewriter, rounds=0)      # the model pass alone
                else:
                    res = mod.fix_file(probe, None)
                if res.get("before"):
                    n_plan = _apply_copy_edits_to_plan(_slides, res.get("edits") or [])
                    notes.append(f"plain-English check: {res.get('before')} flag(s) on the first layout, "
                                 f"{int(res.get('rewritten') or 0)} unit(s) rewritten, "
                                 f"{int(res.get('fallback') or 0)} repaired by rule, "
                                 f"{n_plan} line(s) changed in the plan")
                    n_rule = 0
                    if _rewriter is not None and res.get("after"):
                        # what the model pass left: the fixed rewrites (subhead
                        # templates, a closer on three pages), then the finisher
                        try:
                            rule_notes = dedupe_plan_copy(_slides)
                            n_rule = len(rule_notes)
                            notes += rule_notes
                        except Exception:
                            pass
                    if n_plan or n_rule:
                        try:
                            notes += retire_fragment_subs(_slides)
                        except Exception:
                            pass
                        rendered = _build(out_path)
        except Exception as e:
            notes.append(f"copy repair before layout skipped: {type(e).__name__}")
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass
    plan['_copy_notes'] = list(notes)
    # 2026-10-21 (Jenna: the design skill is always used): every delivered
    # deck carries Crosswalk Inter embedded and passes the pack's checks.
    # The finish fixes in place and never holds the file. What it ran,
    # what it changed and the check counts ride on the plan for the job
    # record (2026-11-10).
    plan['_finish'] = {'ready': None, 'copy_notes': list(plan.get('_copy_notes') or []),
                       'base_notes': list(base_notes), 'base_check': 'ok' if not base_left else list(base_left),
                       'sum_notes': list(sum_notes), 'sum_check': 'ok' if not sum_left else list(sum_left),
                       'photo_used': list(plan.get('_photo_used') or []),
                       'photo_rejected': list(plan.get('_photo_rejected') or [])}
    if ds is not None:
        try:
            rep = ds.finish(out_path, copy_repair=True, copy_rewriter=_rewriter, protected_names=protected_names)
            plan['_finish'].update(rep.job_record())
        except Exception as e:
            plan['_finish']['error'] = f"{type(e).__name__}: {e}"[:200]
    return rendered
