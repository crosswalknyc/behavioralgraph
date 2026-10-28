#!/usr/bin/env python3
"""Crosswalk brand linter.

Usage:
  python3 cw_brand_lint.py FILE [FILE ...]

Checks .pptx, .docx, .html/.htm and .md/.txt files against the Crosswalk rules a machine
can check. FAIL breaks a locked rule and must be fixed before delivery. WARN is probably
wrong: look at it and either fix it or be able to say why it is right.
Exit status 1 if anything FAILs.

What it cannot see: widowed words, crops that show sky, titles colliding with subheads,
labels clipped by a card. Render every page and look (scripts/render_preview.py).
Run scripts/cw_embed_fonts.py first: it embeds Crosswalk Inter and checks the fonts; this linter checks the rest.
"""
import io, json, math, os, re, sys, zipfile
from html import unescape

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from cw_door_copy import door_hits as _door_hits
except Exception:  # pragma: no cover
    def _door_hits(text):
        return [m.group(0) for m in re.finditer(r"\bdoors?\b", text or "", re.I)]
TOK = json.load(open(os.path.join(HERE, "..", "assets", "tokens.json")))
EMU = 914400

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

HOUSE = "Crosswalk Inter"
MONO = "Geist Mono"
SYMBOL_FONTS = {"Symbol", "Wingdings", "Wingdings 2", "Wingdings 3", "Courier New", "Arial"}  # bullet glyphs only
OLD_NAMES = re.compile(r"^(Inter( 18pt)*( (Thin|ExtraLight|Light|Medium|SemiBold|ExtraBold|Black))?)$")
PALETTE = {v.upper() for v in TOK["color"].values()}
for grp in TOK["text"].values():
    PALETTE |= {v.upper() for v in grp.values()}
for g in TOK["gradient"].values():
    if isinstance(g, dict):
        for st in g.get("stops", []):
            PALETTE.add((st if isinstance(st, str) else st[1]).upper())
for grp in TOK["surface"].values():
    PALETTE |= {v.upper() for v in grp.values() if isinstance(v, str) and v.startswith("#")}
for grp in (TOK.get("web", {}).get("dashboardDark", {}), TOK.get("web", {}).get("dashboardLight", {})):
    PALETTE |= {v.upper() for v in grp.values()}
PALETTE |= {"#B5DD38", "#A9CF33", "#D978F2", "#B554D6", "#C766E6", "#8FA84A", "#547110", "#FFFFFF", "#A8AFA9", "#9BAAAC", "#888C89",
            # Orchid Deep page, The Read and the email signatures
            "#9E45C4", "#ED9BFE", "#3B1C46", "#4A2A55", "#AEB4AF", "#5C6560", "#7C817C", "#D8D6CD", "#A2A6A0", "#A7ACA6", "#8E938D"}

BRITISH = [
    (r"\bcolour(s|ed|ful|ing)?\b", "color"), (r"\bbehaviour(s|al|ally)?\b", "behavior"),
    (r"\bfavour(ite|ites|ed|s)?\b", "favor"), (r"\bhonour(s|ed)?\b", "honor"), (r"\blabour\b", "labor"),
    (r"\bneighbour(s|hood|hoods)?\b", "neighbor"), (r"\borganis(e|ed|es|ing|ation|ations|ational)\b", "organiz-"),
    (r"\brecognis(e|ed|es|ing)\b", "recogniz-"), (r"\banalyse(d)?\b", "analyze"), (r"\banalysing\b", "analyzing"),
    (r"\boptimis(e|ed|es|ing|ation)\b", "optimiz-"), (r"\bprioritis(e|ed|es|ing|ation)\b", "prioritiz-"),
    (r"\bmonetis(e|ed|es|ing|ation)\b", "monetiz-"), (r"\bpersonalis(e|ed|es|ing|ation)\b", "personaliz-"),
    (r"\bcustomis(e|ed|es|ing|ation)\b", "customiz-"), (r"\bcategoris(e|ed|es|ing)\b", "categoriz-"),
    (r"\bvisualis(e|ed|es|ing|ation)\b", "visualiz-"), (r"\bminimis(e|ed|es|ing)\b", "minimiz-"),
    (r"\bmaximis(e|ed|es|ing)\b", "maximiz-"), (r"\bsummaris(e|ed|es|ing)\b", "summariz-"),
    (r"\bcentre(s|d)?\b", "center"), (r"\bmetre(s)?\b", "meter"), (r"\blicence(s|d)?\b", "license"),
    (r"\bdefence\b", "defense"), (r"\boffence\b", "offense"), (r"\bcatalogue(s)?\b", "catalog"),
    (r"\btravell(ing|ed|er|ers)\b", "travel-"), (r"\bmodell(ing|ed)\b", "model-"), (r"\bcancell(ing|ed)\b", "cancel-"),
    (r"\blabell(ing|ed)\b", "label-"), (r"\bprogramme(s)?\b", "program"), (r"\bgrey(s|er|ish)?\b", "gray"),
    (r"\bwhilst\b", "while"), (r"\bamongst\b", "among"), (r"\bper cent\b", "percent"), (r"\bjudgement(s)?\b", "judgment"),
    (r"\bageing\b", "aging"), (r"\bartefact(s)?\b", "artifact"), (r"\bjewellery\b", "jewelry"), (r"\bcosy\b", "cozy"),
]
SPORTS = re.compile(r"\b(fans?|NFL|NBA|MLB|NHL|WNBA|MLS|UFC|league|stadium|season ticket|touchdown|inning|playoffs?)\b", re.I)


class Report:
    def __init__(self, name):
        self.name, self.fails, self.warns = name, [], []

    def fail(self, where, msg):
        self.fails.append((where, msg))

    def warn(self, where, msg):
        self.warns.append((where, msg))

    def show(self):
        print(f"\n== {self.name}")
        counts = {}
        for kind, items in (("FAIL", self.fails), ("WARN", self.warns)):
            unique = list(dict.fromkeys(items))
            counts[kind] = len(unique)
            for where, msg in unique:
                print(f"{kind}  {('[' + where + '] ') if where else ''}{msg}")
        print(f"{counts['FAIL']} failure(s), {counts['WARN']} warning(s)")
        return not self.fails


# ---------------------------------------------------------------- copy checks
PLACEHOLDER = re.compile(r"\[[A-Z][^\]]{0,40}\](?!\()|^TEMPLATE \d{1,2}\b.*|\b(IMAGE|HALF BLEED|FULL BLEED)\s+·.*|\bXX+\b|\bLorem ipsum\b", re.M)

def check_copy(text, where, rep, allow_eyebrow=False):
    t = text.strip()
    if not t:
        return
    body = t
    if allow_eyebrow and re.match(r"^([A-Z]+ )?\d{1,2} — ", t):
        body = t.split(" — ", 1)[1]
    body = re.sub(r"(^|\n)[ \t]*—([ \t]+|$)", r"\1", body)  # an em-dash used as a list marker at the start of a line is a bullet, not punctuation
    for m in re.finditer("—", body):
        rep.fail(where, f"em-dash in copy: “{snip(body, m.start())}”. Use a period, comma, colon or parentheses.")
    for snippet in _door_hits(body):
        rep.fail(where, f"door metaphor: “{snippet}”. Decks never use the word door. State the claim.")
    for m in re.finditer("–", body):
        if re.match(r"\d", body[m.start() + 1:m.start() + 2] or "") and re.search(r"\d$", body[:m.start()]):
            rep.warn(where, f"en dash in a range: “{snip(body, m.start())}”. In copy write “to” (2026 to 2032).")
        else:
            rep.fail(where, f"en dash in copy: “{snip(body, m.start())}”. Use a period, comma, colon or parentheses.")
    for m in PLACEHOLDER.finditer(t):
        rep.fail(where, f"template placeholder or annotation left in: “{m.group(0)}”. Replace it with real content, or flag it to the person if they asked for placeholders.")
    # a count of households (or "households" as the unit); "household income" as an attribute is fine
    if re.search(r"\d[\d,.]*\s*(?:[MKB]|million|thousand)?\s+(?:\w+\s+)?households?\b|\bhouseholds\b", body, re.I):
        rep.fail(where, f"“household” as a count: Crosswalk counts accounts (or people, viewers, fans in description), never households")
    for m in re.finditer(r"\b(modeled|modelled|estimated|synthetic|synth)\b", body, re.I):
        rep.warn(where, f"“{m.group(0)}” in copy: client copy never says modeled, estimated or synthetic (projected is fine)")
    for m in re.finditer(r"\w\s-\s\w", body):
        rep.warn(where, f"spaced hyphen used as a dash? “{snip(body, m.start())}”")
    for pat, us in BRITISH:
        for m in re.finditer(pat, body, re.I):
            rep.fail(where, f"British spelling “{m.group(0)}” (American: {us})")
    for m in re.finditer(r"\bdo(es|ing)? (the|any) work\b", body, re.I):
        rep.warn(where, f"“{m.group(0)}” is on the avoid list; say it the way a person would")
    if re.search(r"\bdistilled into\b", body, re.I):
        rep.warn(where, "“distilled into”: house preference is “transformed into”")
    for m in re.finditer(r"\bTV\b", body):
        before = body[:m.start()]
        prev = re.search(r"(\S+)\s*$", before)
        if before.endswith(".") or body[m.end():m.end() + 1] == "+":
            continue  # MLB.TV, Apple TV+
        if prev and (prev.group(1).lower() == "connected" or prev.group(1)[:1].isupper()):
            continue  # connected TV, or a proper name like YouTube TV / Pluto TV
        rep.warn(where, f"plain “TV”: Crosswalk says “connected TV” or “CTV”: “{snip(body, m.start())}”")
    if re.search(r"\b(behavioral|data|intelligence|signal) layer\b", body, re.I):
        rep.warn(where, "“layer” was removed from Crosswalk positioning copy")
    for m in re.finditer(r"\b30\s?M\+?(?:[^.\n]{0,40}?)\b(daily|active|each day|every day|today|right now)\b", body, re.I):
        if not re.search(r"\b10\s?M\b", m.group(0)):
            rep.warn(where, f"“{m.group(0)[:50]}”: 30M is the panel to date; the daily panel is 10M")
    if re.search(r"\bviewers\b", body, re.I) and re.search(r"\d", body):
        rep.warn(where, "counts labeled “viewers”: Crosswalk measures accounts")
    for m in re.finditer(r"CROSSWALK\s*/\s*BEHAVIORAL INTELLIGENCE(?!\s+ENGINE)", body):
        rep.fail(where, "footer lockup must read CROSSWALK / BEHAVIORAL INTELLIGENCE ENGINE")


def check_doc_level_copy(all_text, rep):
    n = len(re.findall(r",\s+not\s+(a|an|the|just|only|\w+)\b", all_text))
    if n >= 2:
        rep.warn("", f"the “X, not Y” construction appears {n} times; vary it")
    n = len(re.findall(r"\bactually\b", all_text, re.I))
    if n >= 2:
        rep.warn("", f"“actually” appears {n} times; once at most, and only where it overturns something the reader believes")
    if len(SPORTS.findall(all_text)) >= 6 and re.search(r"\baudiences?\b", all_text, re.I):
        rep.warn("", "sports material uses “audience”: in sports work the people are “fans”")


def snip(s, i, n=36):
    a, b = max(0, i - n), min(len(s), i + n)
    return ("…" if a else "") + s[a:b].replace("\n", " ") + ("…" if b < len(s) else "")


# ---------------------------------------------------------------- pptx
def lum(hexv):
    h = hexv.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def image_mean(data):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        return im.resize((1, 1), Image.BOX).getpixel((0, 0))
    except Exception:
        return None


def is_accent_image(data):
    """True when a background image is a Signal or Orchid gradient page (any cell strongly accent-colored)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((4, 4), Image.BOX)
    except Exception:
        return False
    for r, g, b in list(im.convert("RGB").getdata()) if not hasattr(im, "get_flattened_data") else im.get_flattened_data():
        if g > 200 and b < 140 and g - b > 80:      # Signal Green family
            return True
        if r > 200 and b > 220 and g < min(r, b) - 25:  # Orchid family
            return True
    return False


# ---------------------------------------------------------------- widows (pptx)
METRICS = json.load(open(os.path.join(HERE, "..", "assets", "font-metrics.json")))
# Renderers disagree by a percent or two on how much fits on a line (kerning, hinting): PowerPoint
# tends to fit a little less than the LibreOffice preview, Keynote and Google Slides a little more.
# A last line is only safe when it holds at least two words across that whole range, so the check
# measures at a slightly narrower and a slightly wider box as well as the exact one.
WRAP_SCALES = (0.985, 1.0, 1.02)
WRAP_SAFETY = WRAP_SCALES[0]


def _weight_key(face, bold):
    f = (face or HOUSE).lower()
    for k in ("extralight", "extrabold", "semibold", "thin", "light", "medium", "black"):
        if f.endswith(" " + k):
            return k
    return "bold" if bold else "regular"


def _char_w(ch, wt):
    return METRICS["weights"].get(wt, METRICS["weights"]["regular"]).get(ch, METRICS["fallback"])


def _wrap(chars, avail_pt):
    """chars: [(ch, weight, size_pt, tracking_pt)] for one line-break segment. Returns [(words, width_pt)]."""
    words, cur, curw, space = [], "", 0.0, 0.0
    for ch, wt, sz, tr in chars + [(" ", "regular", 10.0, 0.0)]:
        if ch in " \t":
            if cur:
                words.append((cur, curw, space))
            cur, curw = "", 0.0
            space = _char_w(" ", wt) * sz + tr
        else:
            cur += ch
            curw += _char_w(ch, wt) * sz + tr
    lines, line, lw = [], [], 0.0
    for w, wd, sp in words:
        if not line:
            line, lw = [w], wd
        elif lw + sp + wd <= avail_pt:
            line.append(w); lw += sp + wd
        else:
            lines.append((line, lw)); line, lw = [w], wd
    if line:
        lines.append((line, lw))
    return lines


def room_right(sx, sy, cx, cy, sw, portrait, rects):
    """How far right this text box could grow: the content band, a card it sits in (keeping the same
    padding on the right as on the left), or the next block to its right (keeping a 0.28 in gutter)."""
    limit = sw - (0.85 if portrait else 0.92) * EMU
    for (x, y, w, h, filled) in rects:
        if (x, y, w, h) == (sx, sy, cx, cy) or w >= 0.95 * sw:
            continue
        inside = filled and x <= sx and y <= sy and x + w >= sx + cx - 9525 and y + h >= sy + cy * 0.5
        if inside:
            limit = min(limit, x + w - (sx - x))
        elif x >= sx + cx - 9525 and y < sy + cy and y + h > sy:
            limit = min(limit, x - 0.28 * EMU)
    return limit


def check_widows(txbody, cx, sx, sw, portrait, where, rep, limit=None):
    bp = txbody.find(f"{A}bodyPr")
    if bp is not None and (bp.get("wrap") == "none" or bp.get("vert") not in (None, "horz")):
        return
    lins = int(bp.get("lIns", 91440)) if bp is not None else 91440
    rins = int(bp.get("rIns", 91440)) if bp is not None else 91440
    width_pt = (cx - lins - rins) / EMU * 72
    if width_pt <= 0:
        return
    band_right = (limit if limit is not None else sw - (0.85 if portrait else 0.92) * EMU) / EMU
    for p_ in txbody.iter(f"{A}p"):
        segs = [[]]
        for el in p_:
            if el.tag == f"{A}br":
                segs.append([]); continue
            if el.tag not in (f"{A}r", f"{A}fld"):
                continue
            rpr = el.find(f"{A}rPr"); t = el.find(f"{A}t")
            txt = (t.text or "") if t is not None else ""
            sz = int(rpr.get("sz")) / 100 if rpr is not None and rpr.get("sz") else 18.0
            tr = int(rpr.get("spc")) / 100 if rpr is not None and rpr.get("spc") else 0.0
            lat = rpr.find(f"{A}latin") if rpr is not None else None
            face = lat.get("typeface") if lat is not None else None
            if face == MONO:
                segs[-1] = None
                break
            wt = _weight_key(face, rpr is not None and rpr.get("b") == "1")
            if segs[-1] is not None:
                segs[-1].extend((ch, wt, sz, tr) for ch in txt.replace("\v", " "))
        for seg in segs:
            if not seg or max(c[2] for c in seg) < 10:
                continue  # footnotes, labels and chrome
            text = "".join(c[0] for c in seg).strip()
            if len(text.split()) < 4:
                continue
            def widowed(w):
                ls = _wrap(seg, w)
                return ls if len(ls) >= 2 and len(ls[-1][0]) == 1 else None
            hits = [widowed(width_pt * k) for k in WRAP_SCALES]
            if not any(hits):
                continue
            lines = next(h for h in hits if h)
            certain = hits[1] is not None
            # width that would bring the lone word up (or at least give the last line two words)
            need = None
            for extra in range(1, 400):
                w_try = width_pt + extra * 0.72          # 0.01 in steps
                if not any(widowed(w_try * k) for k in WRAP_SCALES):
                    need = w_try
                    break
            fix = "edit the copy so the last line carries at least two words"
            if need is not None:
                box_w = math.ceil((need / 72 + (lins + rins) / EMU) * 100) / 100 + 0.02   # a hair of slack
                if sx / EMU + box_w <= band_right + 0.005:
                    fix = (f"widen the box from {cx / EMU:.2f} to {box_w:.2f} in (there is room), "
                           f"or edit the copy")
            if len(text.split()) < 8:
                rep.warn(where, f"short label ends on one word: “{lines[-1][0][0]}” alone under “{text[:40]}…”. "
                                f"Fine to leave if the column is fixed, but better rebalanced: {fix}.")
            elif certain:
                rep.fail(where, f"widow: “{lines[-1][0][0]}” sits alone on the last line of “{text[:45]}…”. "
                                f"Fix: {fix}. Never a manual break.")
            else:
                rep.warn(where, f"possible widow: “{lines[-1][0][0]}” is on the edge, so in some renderers it sits alone "
                                f"on the last line (“{text[:40]}…”). Fix: {fix}.")


def lint_pptx(path, rep):
    from lxml import etree
    z = zipfile.ZipFile(path)
    names = z.namelist()
    pres = z.read("ppt/presentation.xml").decode("utf8")
    if 'embedTrueTypeFonts="1"' not in pres:
        rep.warn("deck", "fonts are not set to embed. Run scripts/cw_embed_fonts.py on the file before delivery.")
    if 'saveSubsetFonts="1"' in pres:
        rep.fail("deck", "font embedding is set to subset characters (saveSubsetFonts=1). Run scripts/cw_embed_fonts.py so all characters embed.")
    sw = int(re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres).group(1))
    sh = int(re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres).group(2))

    theme_ok = True
    for n in names:
        if n.startswith("ppt/theme/") and n.endswith(".xml"):
            x = z.read(n).decode("utf8", "ignore")
            for slot in ("majorFont", "minorFont"):
                m = re.search(rf"<a:{slot}>\s*<a:latin typeface=\"([^\"]*)\"", x)
                if m and not m.group(1).startswith(HOUSE):
                    theme_ok = False
                    rep.fail("theme", f"theme {slot} is “{m.group(1)}”; set it to {HOUSE} so nothing inherits it")

    # slide order
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf8")
    rid2t = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
    rid2t.update({a: b for b, a in re.findall(r'Target="([^"]+)"[^>]*Id="(rId\d+)"', rels)})
    order = [rid2t[r] for r in re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres)]
    slides = ["ppt/" + t.lstrip("/").replace("ppt/", "") for t in order]

    surfaces, mono_slides, all_text, one_weight_titles = [], [], [], []
    for si, sp in enumerate(slides, 1):
        where = f"slide {si}"
        x = z.read(sp)
        root = etree.fromstring(x)
        srels_name = sp.replace("slides/", "slides/_rels/") + ".rels"
        srels = z.read(srels_name).decode("utf8") if srels_name in names else ""
        rmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', srels))
        rmap.update({a: b for b, a in re.findall(r'Target="([^"]+)"[^>]*Id="(rId\d+)"', srels)})

        def media(rid):
            t = rmap.get(rid)
            if not t:
                return None
            p = os.path.normpath(os.path.join(os.path.dirname(sp), t)).replace("\\", "/")
            return z.read(p) if p in names else None

        # ---- background
        surface, alt = "dark", False
        bg = root.find(f".//{P}bg")
        if bg is not None:
            clr = bg.find(f".//{A}srgbClr")
            blip = bg.find(f".//{A}blip")
            if bg.find(f".//{A}gradFill") is not None:
                stops = [c.get("val").upper() for c in bg.findall(f".//{A}gradFill//{A}srgbClr")]
                alt = any(s in ("C7F23E", "9FD628", "E682FF", "B554D6", "CE6FE6") for s in stops)
                surface = "light" if stops and lum(stops[0]) > 0.55 else "dark"
            elif blip is not None:
                data = media(blip.get(f"{R}embed")) or b""
                mean = image_mean(data)
                if mean:
                    r_, g_, b_ = mean
                    alt = is_accent_image(data)
                    surface = "light" if (0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_) / 255 > 0.55 else "dark"
            elif clr is not None:
                v = clr.get("val").upper()
                alt = v in ("C7F23E", "E682FF")
                surface = "light" if lum(v) > 0.55 else "dark"
        surfaces.append(("alt" if alt else surface))

        has_field = b'type="slidenum"' in x
        typed_number = False
        # dark panels on this slide (text inside one is judged as if on a dark page)
        dark_boxes = []
        for shp in root.iter(f"{P}sp"):
            off, ext = shp.find(f".//{A}xfrm/{A}off"), shp.find(f".//{A}xfrm/{A}ext")
            sp_ = shp.find(f"{P}spPr")
            if off is None or ext is None or sp_ is None:
                continue
            cols = [c.get("val", "") for c in sp_.findall(f"{A}solidFill/{A}srgbClr")] + \
                   [c.get("val", "") for c in sp_.findall(f"{A}gradFill//{A}srgbClr")]
            if cols and all(re.fullmatch(r"[0-9A-Fa-f]{6}", c) and lum(c) < 0.25 for c in cols):
                dark_boxes.append((int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))))

        rects = []
        for shp in root.iter(f"{P}sp", f"{P}pic", f"{P}graphicFrame"):
            off = shp.find(f".//{A}xfrm/{A}off") if shp.tag != f"{P}graphicFrame" else shp.find(f"{P}xfrm/{A}off")
            ext = shp.find(f".//{A}xfrm/{A}ext") if shp.tag != f"{P}graphicFrame" else shp.find(f"{P}xfrm/{A}ext")
            if off is None or ext is None:
                continue
            sp_ = shp.find(f"{P}spPr")
            filled = shp.tag == f"{P}pic" or (sp_ is not None and (sp_.find(f"{A}solidFill") is not None or sp_.find(f"{A}gradFill") is not None))
            tb = shp.find(f"{P}txBody")
            has_txt = tb is not None and "".join(t.text or "" for t in tb.iter(f"{A}t")).strip() != ""
            if filled or has_txt or shp.tag != f"{P}sp":
                rects.append((int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")), filled and not has_txt))

        def on_dark(bx, by, bw, bh):
            mx, my = bx + bw / 2, by + bh / 2
            return any(x0 <= mx <= x0 + w0 and y0 <= my <= y0 + h0 for x0, y0, w0, h0 in dark_boxes)

        for shp in root.iter(f"{P}sp", f"{P}pic"):
            off = shp.find(f".//{A}xfrm/{A}off")
            ext = shp.find(f".//{A}xfrm/{A}ext")
            if off is None or ext is None:
                continue
            sx, sy, cx, cy = (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
            top_in = sy / EMU

            if shp.tag == f"{P}pic":
                blip = shp.find(f".//{A}blip")
                data = media(blip.get(f"{R}embed")) if blip is not None else None
                if data and cx and cy:
                    try:
                        from PIL import Image
                        im = Image.open(io.BytesIO(data))
                        iw, ih = im.size
                        src = shp.find(f".//{A}srcRect")
                        l = t = r_ = b_ = 0
                        if src is not None:
                            l, t, r_, b_ = (int(src.get(k, 0)) / 100000 for k in ("l", "t", "r", "b"))
                        native = (iw * (1 - l - r_)) / max(ih * (1 - t - b_), 1)
                        shown = cx / cy
                        if abs(shown / native - 1) > 0.02:
                            nm = shp.find(f".//{P}cNvPr").get("name", "picture")
                            rep.fail(where, f"image “{nm}” is stretched or squished ({native:.3f} native vs {shown:.3f} placed). Scale at its own proportions or crop.")
                    except Exception:
                        pass
                continue

            geom = shp.find(f".//{A}prstGeom")
            prst = geom.get("prst") if geom is not None else None
            sppr = shp.find(f"{P}spPr")
            filled = sppr is not None and (sppr.find(f"{A}solidFill") is not None or sppr.find(f"{A}gradFill") is not None or sppr.find(f"{A}blipFill") is not None)
            txbody = shp.find(f"{P}txBody")
            has_text = txbody is not None and "".join(t.text or "" for t in txbody.iter(f"{A}t")).strip() != ""

            if sx >= sw or sy >= sh or sx + cx <= 0 or sy + cy <= 0:
                nm = shp.find(f".//{P}cNvPr").get("name", "shape")
                rep.warn(where, f"“{nm}” is parked off the page (a template helper or note?). Delete it before delivery.")
                continue
            if filled and cx >= 0.95 * sw and cy >= 0.95 * sh:
                if sppr.find(f"{A}gradFill") is not None:
                    rep.warn(where, "a full-page gradient shape sits over the page. Over a photo, bake the gradient into the image (bake_photo.py); for a gradient page, set it as the slide background.")
                else:
                    rep.fail(where, "a full-page shape is acting as the background. Set the color or image as the slide background instead.")
            elif filled and prst == "rect" and cx / EMU > 0.3 and cy / EMU > 0.3 and not (cx >= 0.95 * sw or cy >= 0.95 * sh):
                rep.warn(where, f"square-cornered filled shape at ({sx/EMU:.2f}, {top_in:.2f}). Crosswalk shapes are rounded.")
            if prst == "ellipse" and cx / EMU < 0.2 and shp.find(f".//{A}outerShdw") is not None:
                rep.fail(where, "drop shadow on the eyebrow dot")

            if txbody is None:
                continue
            ptexts = []
            runs = []
            for p_ in txbody.iter(f"{A}p"):
                ptexts.append("".join((" " if ch.tag == f"{A}br" else "".join(t.text or "" for t in ch.iter(f"{A}t")))
                                      for ch in p_ if ch.tag in (f"{A}r", f"{A}fld", f"{A}br")))
                for r in list(p_.iter(f"{A}r")) + list(p_.iter(f"{A}fld")):
                    rpr = r.find(f"{A}rPr")
                    t = r.find(f"{A}t")
                    runs.append((rpr, (t.text or "") if t is not None else "", r.tag))
            text = "\n".join(ptexts).strip()
            if not text:
                continue
            all_text.append(text)
            check_copy(text, where, rep, allow_eyebrow=True)
            check_widows(txbody, cx, sx, sw, sh > sw, where, rep,
                         limit=room_right(sx, sy, cx, cy, sw, sh > sw, rects))
            if (re.fullmatch(r"\d{1,3}", text) and sx > 0.6 * sw and sy > 0.88 * sh
                    and txbody.find(f".//{A}fld") is None):
                typed_number = True

            sizes = []
            for rpr, rt, tag in runs:
                if not rt.strip():
                    continue
                if rpr is not None and rpr.get("i") == "1":
                    rep.fail(where, f"italic text: “{rt.strip()[:40]}”")
                lat = rpr.find(f"{A}latin") if rpr is not None else None
                facename = lat.get("typeface") if lat is not None else None
                if facename is None or facename.startswith("+"):
                    if not theme_ok:
                        rep.fail(where, f"“{rt.strip()[:30]}” inherits the theme font, which is not {HOUSE}")
                elif OLD_NAMES.match(facename):
                    rep.fail(where, f"old font name “{facename}”; use the Crosswalk Inter family names")
                elif facename == MONO:
                    mono_slides.append(si)
                elif not facename.startswith(HOUSE):
                    rep.fail(where, f"off-system font “{facename}” on “{rt.strip()[:30]}”")
                sz = rpr.get("sz") if rpr is not None else None
                if sz:
                    pt = int(sz) / 100
                    sizes.append(pt)
                    caps = rt.strip() == rt.strip().upper()
                    if top_in < 6.45 and (facename or "") != MONO and not caps and re.search(r"[a-z]", rt):
                        if pt < 9:
                            rep.fail(where, f"{pt:g}pt copy in the content area: “{rt.strip()[:40]}” (body is never below 10pt; notes run at 9pt)")
                        elif pt < 10 and len(rt.split()) >= 12:
                            rep.warn(where, f"{pt:g}pt sentence in the content area: “{rt.strip()[:40]}”. Body copy is 10pt minimum; only footnotes and method notes run at 9pt.")
                fill = rpr.find(f"{A}solidFill/{A}srgbClr") if rpr is not None else None
                if (fill is not None and fill.get("val", "").upper() == "C7F23E" and surface == "light" and not alt
                        and not on_dark(sx, sy, cx, cy)):
                    rep.fail(where, f"Signal Green text on an off-white page: “{rt.strip()[:30]}”. Use Signal Olive #5E7E12.")

            # eyebrow
            if re.match(r"^\d{1,2} [—-] ", text) and top_in < 0.9:
                if any(rpr is not None and rpr.get("b") == "1" for rpr, rt, _ in runs if rt.strip()):
                    rep.fail(where, "eyebrow is bold; it is regular weight")
                bp = txbody.find(f"{A}bodyPr")
                if bp is not None and bp.get("anchor") not in ("ctr",):
                    rep.warn(where, "eyebrow text is not vertically centered in its box (it should sit on the dot's centerline)")
                if " - " in text[:6]:
                    rep.warn(where, "eyebrow should use an em-dash after the number: “01 — SECTION”")
            # title
            if sizes and max(sizes) >= 28 and 0.8 < top_in < 1.6:
                if not re.search(r"[.?!]$", text):
                    rep.warn(where, f"title does not end with a period: “{text[:50]}”")
                faces = {((rpr.find(f'{A}latin').get('typeface') if rpr is not None and rpr.find(f'{A}latin') is not None else HOUSE),
                          (rpr is not None and rpr.get('b') == '1')) for rpr, rt, _ in runs if rt.strip()}
                heavy = any(b or any(k in f for k in ("Bold", "Black", "SemiBold")) for f, b in faces)
                light = any(any(k in f for k in ("Light", "Thin")) or (not b and f == HOUSE) for f, b in faces)
                if not (heavy and light):
                    one_weight_titles.append(si)

        if typed_number:
            rep.fail(where, "page number is typed. Insert it as a slide-number field.")
        elif not has_field and si > 1:
            rep.warn(where, "no slide-number field on this page")

    if one_weight_titles:
        rep.warn("deck", f"titles in a single weight on slide(s) {one_weight_titles}. House preference: pair a bold run with a light run in the headline.")
    if mono_slides:
        rep.warn("deck", f"Geist Mono used on slide(s) {sorted(set(mono_slides))}: confirm these are raw clickstream rows")
    portrait = sh > sw   # SOWs, POCs and other portrait documents: the deck sequence rules do not apply
    if not portrait:
        if len(slides) >= 6 and "alt" not in surfaces:
            rep.fail("deck", "no Signal Green or Orchid gradient page. Every deck needs at least one alternative color page.")
        elif len(slides) >= 3 and "alt" not in surfaces:
            rep.warn("deck", "no Signal Green or Orchid gradient page yet")
        run, longest, at = 0, 0, 0
        for i, s in enumerate(surfaces, 1):
            run = run + 1 if s == "dark" else 0
            if run > longest:
                longest, at = run, i
        if longest > 3:
            rep.warn("deck", f"{longest} dark pages in a row ending at slide {at}; switch surface by the fourth")
        adj = [i for i in range(2, len(surfaces) + 1) if surfaces[i - 1] == "alt" and surfaces[i - 2] == "alt"]
        if adj:
            rep.warn("deck", f"accent pages next to each other at slide(s) {adj}. Accent pages are punctuation; put a Graphite or Off-White page between them (a gradient closing page needs a non-accent page before it).")
    check_image_resolution(z, names, slides, sw, rep)
    check_photo_edges(z, names, slides, sw, sh, rep)
    # the slash mark (make_slash_mark.py tags its PNGs) is occasional and never on more than one page
    marked = {n for n in names if n.startswith("ppt/media/") and b"cw-slash-mark" in z.read(n)}
    if marked:
        on = []
        for si, sp in enumerate(slides, 1):
            rp = sp.replace("slides/", "slides/_rels/") + ".rels"
            if rp in names:
                targets = re.findall(r'Target="\.\./media/([^"]+)"', z.read(rp).decode("utf8", "ignore"))
                if any("ppt/media/" + t in marked for t in targets):
                    on.append(si)
        if len(on) > 1:
            rep.warn("deck", f"the slash mark appears on slides {on}. It is occasional (most decks skip it) and never on more than one page: keep the one that matters most (the cover, a divider or statement, or the closing).")
    check_doc_level_copy("\n".join(all_text), rep)


def check_image_resolution(z, names, slides, sw, rep):
    """High-res rule: pictures and backgrounds at about 288 ppi (3840 px across a 16:9 page). Warn under 150."""
    import hashlib
    from PIL import Image
    try:
        sys.path.insert(0, HERE)
        from cw_backgrounds import KNOWN
    except Exception:
        KNOWN = {}
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    Aa = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    Rr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    from lxml import etree
    stale, seen = set(), {}
    for si, sp in enumerate(slides, 1):
        rp = sp.replace("slides/", "slides/_rels/") + ".rels"
        if rp not in names:
            continue
        rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="\.\./media/([^"]+)"', z.read(rp).decode("utf8", "ignore")))
        rels.update({a: b for b, a in re.findall(r'Target="\.\./media/([^"]+)"[^>]*Id="(rId\d+)"', z.read(rp).decode("utf8", "ignore"))})
        root = etree.fromstring(z.read(sp))
        placed = []
        for pic in root.iter(P + "pic"):
            blip = pic.find(".//" + Aa + "blip")
            ext = pic.find(".//" + Aa + "xfrm/" + Aa + "ext")
            if blip is not None and ext is not None:
                placed.append((blip.get(Rr + "embed"), int(ext.get("cx")) / 914400))
        bgblip = root.find(".//" + P + "bg//" + Aa + "blip")
        if bgblip is not None:
            placed.append((bgblip.get(Rr + "embed"), sw / 914400))
        for rid, win in placed:
            media = rels.get(rid)
            if not media or win < 2.0:
                continue
            key = "ppt/media/" + media
            if key not in seen:
                b = z.read(key)
                try:
                    seen[key] = (Image.open(io.BytesIO(b)).size[0], hashlib.md5(b).hexdigest())
                except Exception:
                    seen[key] = (None, None)
            px, md5 = seen[key]
            if md5 in KNOWN:
                stale.add(si)
            elif px and px / win < 150:
                rep.warn(f"slide {si}", f"low-resolution image: {px} px across {win:.2f} in ({px / win:.0f} ppi). Keep everything high res: about 288 ppi (3840 px across a full 16:9 page). Use a larger original, or rebuild a background with make_gradients.py / make_slash_mark.py.")
    if stale:
        rep.warn("deck", f"low-resolution Crosswalk backgrounds on slide(s) {sorted(stale)} (template or older-skill files, which can show banding). Fix: python3 scripts/cw_backgrounds.py upgrade FILE")


def _part_bg(z, names, part):
    """Solid background color of a slide, falling back to its layout and master; None for an image or gradient."""
    seen = 0
    while part and part in names and seen < 3:
        x = z.read(part).decode("utf8", "ignore")
        m = re.search(r"<p:bg>(.*?)</p:bg>", x, re.S)
        if m:
            c = re.search(r'<a:solidFill>\s*<a:srgbClr val="([0-9A-Fa-f]{6})"', m.group(1))
            return c.group(1).upper() if c and "<a:blip" not in m.group(1) and "<a:gradFill" not in m.group(1) else None
        rp = part.replace("/slides/", "/slides/_rels/").replace("/slideLayouts/", "/slideLayouts/_rels/") + ".rels"
        if "/_rels/" not in rp or rp not in names:
            return None
        r = z.read(rp).decode("utf8", "ignore")
        t = re.search(r'Target="\.\./(slideLayouts/[^"]+|slideMasters/[^"]+)"', r)
        part = "ppt/" + t.group(1) if t else None
        seen += 1
    return None


def check_photo_edges(z, names, slides, sw, sh, rep):
    """A photo beside a flat panel (half bleed) must dissolve into the page color through a long, eased fade.
    Flags a hard edge (the photo's inner edge is not the page color), a quick fade, and a gradient shape laid
    over the join instead of a fade baked into the photo."""
    from lxml import etree
    from PIL import Image
    import numpy as np
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    Aa = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    Rr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    tol = 0.03 * 914400
    for si, sp in enumerate(slides, 1):
        rp = sp.replace("slides/", "slides/_rels/") + ".rels"
        if rp not in names:
            continue
        rtxt = z.read(rp).decode("utf8", "ignore")
        rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="\.\./media/([^"]+)"', rtxt))
        rels.update({a: b for b, a in re.findall(r'Target="\.\./media/([^"]+)"[^>]*Id="(rId\d+)"', rtxt)})
        root = etree.fromstring(z.read(sp))
        bg = _part_bg(z, names, sp)
        tree = root.find(".//" + P + "spTree")
        if tree is None:
            continue
        grads = []
        for shp in tree.findall(P + "sp"):
            sppr = shp.find(P + "spPr")
            off, ext = shp.find(".//" + Aa + "xfrm/" + Aa + "off"), shp.find(".//" + Aa + "xfrm/" + Aa + "ext")
            if sppr is not None and sppr.find(Aa + "gradFill") is not None and off is not None and ext is not None:
                grads.append((int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))))
        for pic in tree.findall(P + "pic"):                       # top level only: grouped pictures have their own frame
            blip = pic.find(".//" + Aa + "blip")
            off, ext = pic.find(".//" + Aa + "xfrm/" + Aa + "off"), pic.find(".//" + Aa + "xfrm/" + Aa + "ext")
            if blip is None or off is None or ext is None:
                continue
            x, y, w, h = int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))
            at_left, at_right = x <= tol, x + w >= sw - tol
            if not (h >= 0.9 * sh and y <= 0.05 * sh and 0.2 * sw <= w <= 0.9 * sw and at_left != at_right):
                continue
            inner = "right" if at_left else "left"                # the photo's side that faces the copy
            edge_x = x + w if at_left else x
            where = f"slide {si}"
            for gx, gy, gw, gh in grads:
                if gx < edge_x < gx + gw and min(gy + gh, y + h) - max(gy, y) > 0.5 * h:
                    rep.warn(where, f"a gradient shape is laid over the edge of the half-bleed photo. Bake the fade into the photo instead, long and into the exact page color: bake_photo.py --blend {inner} --page {'#' + bg if bg else 'dark|light'}")
                    break
            media = rels.get(blip.get(Rr + "embed"))
            if not media or not bg:
                continue
            try:
                im = Image.open(io.BytesIO(z.read("ppt/media/" + media))).convert("RGB")
            except Exception:
                continue
            src = blip.getparent().find(Aa + "srcRect")
            if src is not None:                                   # honor any crop set in PowerPoint
                l, t, r, b = (int(src.get(k, "0")) / 100000 for k in ("l", "t", "r", "b"))
                W0, H0 = im.size
                im = im.crop((round(l * W0), round(t * H0), round(W0 * (1 - r)), round(H0 * (1 - b))))
            im = im.resize((400, max(1, round(400 * im.size[1] / max(im.size[0], 1)))))
            a = np.asarray(im, dtype=np.float64)
            if inner == "left":
                a = a[:, ::-1]                                    # read every photo from its outer edge toward the panel
            page = np.array([int(bg[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)
            d = np.abs(a - page).mean(axis=(0, 2))[::-1]          # per column, from the inner edge outward
            edge = np.abs(a[:, -6:].mean(axis=(0, 1)) - page).max()
            far = np.percentile(d[len(d) // 2:], 75)
            if edge > 12:
                rep.warn(where, f"the half-bleed photo meets the #{bg} page at a visible edge (its {inner} edge is about {edge:.0f} levels off the page color). Fade it into the page over about half its width: bake_photo.py --blend {inner} --page #{bg}")
            elif far >= 30:
                reach = int(np.argmax(d >= 0.5 * far)) / len(d)
                if reach < 0.2:
                    rep.warn(where, f"the half-bleed photo fades into the page too quickly (half strength within {reach * w / 914400:.1f} in). Make the fade long and eased, about half the photo's width: bake_photo.py --blend {inner} --blend-width 0.5")


# ---------------------------------------------------------------- docx
def lint_docx(path, rep):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    for n in names:
        if n.startswith("word/theme/") and n.endswith(".xml"):
            x = z.read(n).decode("utf8", "ignore")
            for slot in ("majorFont", "minorFont"):
                m = re.search(rf"<a:{slot}>\s*<a:latin typeface=\"([^\"]*)\"", x)
                if m and not m.group(1).startswith(HOUSE):
                    rep.fail("theme", f"theme {slot} is “{m.group(1)}”; run cw_docx_finalize.py")
    settings = z.read("word/settings.xml").decode("utf8", "ignore") if "word/settings.xml" in names else ""
    if "<w:embedTrueTypeFonts" not in settings:
        rep.warn("document", "fonts are not set to embed; run cw_docx_finalize.py")
    if re.search(r"<w:saveSubsetFonts(?! w:val=\"(0|false)\")", settings):
        rep.fail("document", "font embedding is subsetting characters; embed all characters")

    parts = [n for n in names if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes|styles|numbering)\.xml$", n)]
    all_text, footer_has_field, footer_typed = [], False, False
    for n in parts:
        x = z.read(n).decode("utf8", "ignore")
        where = n.replace("word/", "").replace(".xml", "")
        for m in re.finditer(r'<w:rFonts\b([^>]*)/>', x):
            for attr in ("ascii", "hAnsi"):
                fm = re.search(rf'w:{attr}="([^"]+)"', m.group(1))
                if fm:
                    f = fm.group(1)
                    if OLD_NAMES.match(f):
                        rep.fail(where, f"old font name “{f}”; use the Crosswalk Inter family names")
                    elif f.startswith(HOUSE) or f == MONO or (where == "numbering" and f in SYMBOL_FONTS):
                        pass
                    elif where == "styles":
                        rep.warn(where, f"a style uses “{f}” (fine if that style is never applied)")
                    else:
                        rep.fail(where, f"off-system font “{f}”")
        for m in re.finditer(r"<w:(i|iCs)(?: w:val=\"([^\"]*)\")?\s*/>", x):
            if m.group(2) in (None, "1", "true", "on"):
                rep.fail(where, "italic formatting present (style or run)")
                break
        if n == "word/styles.xml":
            continue
        if where.startswith("footer"):
            if re.search(r"(fldSimple[^>]*instr=\"\s*PAGE|<w:instrText[^>]*>\s*PAGE)", x):
                footer_has_field = True
            elif re.search(r"<w:t[^>]*>\s*\d{1,3}\s*</w:t>", x):
                footer_typed = True
        if where == "document":
            for m in re.finditer(r"<w:sz w:val=\"(\d+)\"", x):
                if int(m.group(1)) < 18:
                    rep.warn(where, f"{int(m.group(1))/2:g}pt text in the body; body copy is never below 10pt (footnotes and sources 9pt)")
                    break
        for para in re.findall(r"<w:p[ >].*?</w:p>", x, re.S):
            t = unescape("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)))
            if t.strip():
                all_text.append(t)
                check_copy(t, where, rep)
    if footer_typed and not footer_has_field:
        rep.fail("footer", "page number is typed; insert a PAGE field")
    elif not footer_has_field:
        rep.warn("footer", "no PAGE field found in any footer")
    check_doc_level_copy("\n".join(all_text), rep)


# ---------------------------------------------------------------- html / text
ALLOWED_FAMILIES = {"crosswalk inter", "inter", "inter 18pt", "geist mono", "system-ui", "-apple-system", "blinkmacsystemfont",
                    "segoe ui", "roboto", "helvetica neue", "helvetica", "arial", "sans-serif", "ui-monospace",
                    "sfmono-regular", "menlo", "monospace", "ui-sans-serif", "inherit", "var(--cw-font)", "var(--cw-font-mono)"}


def lint_html(path, rep):
    src = open(path, encoding="utf8", errors="ignore").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", src, re.S | re.I)) + " " + " ".join(re.findall(r'style="([^"]*)"', src))
    for m in re.finditer(r"font-family\s*:\s*([^;}\"]+)", css, re.I):
        fams = [f.strip().strip("'\"").lower() for f in m.group(1).split(",")]
        mono = {"ui-monospace", "sfmono-regular", "menlo", "monaco", "consolas", "courier new", "monospace", "geist mono"}
        if fams and all(f in mono for f in fams):
            rep.warn("css", f"monospace stack “{fams[0]}”: Crosswalk sets raw data in Geist Mono; fine for a code snippet on a helper page")
        elif fams and fams[0] not in ("crosswalk inter", "inter", "inter 18pt", "geist mono", "inherit") and not fams[0].startswith("var("):
            rep.fail("css", f"font stack starts with “{fams[0]}”; lead with “Crosswalk Inter”, “Inter”")
        for f in fams:
            if f in ("serif",) or any(k in f for k in ("georgia", "times", "garamond", "playfair", "serif display", "instrument serif")):
                rep.fail("css", f"serif face in a font stack: “{f}”")
            elif f not in ALLOWED_FAMILIES and f not in ("consolas", "monaco", "courier new") and not f.startswith("var("):
                rep.warn("css", f"unexpected font family “{f}”")
    if re.search(r"font-style\s*:\s*(italic|oblique)", css, re.I):
        rep.fail("css", "italic font-style in CSS")
    tag_normal = re.search(r"(^|[\s,}])(em|i)\s*[,{][^}]*font-style\s*:\s*normal", css, re.I)
    markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", src, flags=re.S | re.I)
    for m in re.finditer(r"<(em|i)\b([^>]*)>(.*?)</\1>", markup, re.I | re.S):
        inner = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if not inner or tag_normal:
            continue  # empty decorative element, or em/i reset to normal
        classes = re.findall(r'class="([^"]*)"', m.group(2))
        if classes and any(re.search(r"\." + re.escape(c) + r"\b[^{]*{[^}]*font-style\s*:\s*normal", css)
                           for c in classes[0].split()):
            continue
        rep.fail("html", f"<{m.group(1)}> renders italic: “{inner[:40]}”. Use weight or the accent color instead.")
    off = sorted({h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", css)} - PALETTE)
    if off:
        more = " \u2026" if len(off) > 12 else ""
        rep.warn("css", "colors outside the Crosswalk palette: " + ", ".join(off[:12]) + more)
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", src, flags=re.S | re.I)
    body = re.sub(r"<(br|/p|/h\d|/li|/div|/td|/th|/section|/header|/footer)[^>]*>", "\n", body, flags=re.I)
    body = unescape(re.sub(r"<[^>]+>", " ", body))
    blocks = [re.sub(r"[ \t]+", " ", b).strip() for b in body.split("\n")]
    for b in blocks:
        check_copy(b, "text", rep, allow_eyebrow=True)
    check_doc_level_copy("\n".join(blocks), rep)


def lint_text(path, rep):
    src = open(path, encoding="utf8", errors="ignore").read()
    src = re.sub(r"```.*?```", " ", src, flags=re.S)
    for m in re.finditer(r"(?<![*_])([*_])(?![*_\s])[^*_\n]+(?<![\s*_])\1(?![*_])", src):
        rep.warn("text", f"markdown italics: {m.group(0)[:40]}")
    for line in src.splitlines():
        check_copy(line, "text", rep, allow_eyebrow=True)
    check_doc_level_copy(src, rep)


def main():
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not files:
        sys.exit(__doc__)
    ok = True
    for f in files:
        rep = Report(f)
        ext = os.path.splitext(f)[1].lower()
        if ext == ".pptx":
            lint_pptx(f, rep)
        elif ext == ".docx":
            lint_docx(f, rep)
        elif ext in (".html", ".htm"):
            lint_html(f, rep)
        elif ext in (".md", ".txt"):
            lint_text(f, rep)
        else:
            print(f"skip {f}: unsupported type")
            continue
        ok = rep.show() and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
