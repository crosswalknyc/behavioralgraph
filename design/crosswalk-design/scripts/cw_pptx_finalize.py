#!/usr/bin/env python3
"""Crosswalk PPTX finalizer. Usage: python3 cw_pptx_finalize.py in.pptx out.pptx
- theme major/minor fonts -> Crosswalk Inter (so nothing inherits Calibri)
- every text run gets explicit latin/ea/cs = its Crosswalk Inter face, with Inter's PANOSE so a missing-font
  substitute is a close sans instead of Calibri
- embedTrueTypeFonts="1" saveSubsetFonts="0" (embed all characters)
- older "Inter 18pt" / "Inter" font names renamed to the Crosswalk Inter family
- bold switched on over a weight family (synthetic bold, which cannot be embedded) becomes the next real weight up
From the crosswalk-pptx-font-safety skill, extended here with the synthetic-bold fix."""
import sys, re, zipfile
HOUSE, PANOSE = "Crosswalk Inter", "02000503000000020004"
src, dst = sys.argv[1], sys.argv[2]
zin = zipfile.ZipFile(src); zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
def face_tag(tag, face):
    return f'<a:{tag} typeface="{face}" panose="{PANOSE}" pitchFamily="34" charset="0"/>'
WEIGHTS = ["Thin", "ExtraLight", "Light", "Medium", "SemiBold", "ExtraBold", "Black"]
FAUX_BOLD = {f"{HOUSE} Thin": f"{HOUSE} Light", f"{HOUSE} ExtraLight": f"{HOUSE} Light",
             f"{HOUSE} Light": f"{HOUSE} Medium", f"{HOUSE} Medium": f"{HOUSE} SemiBold",
             f"{HOUSE} SemiBold": HOUSE, f"{HOUSE} ExtraBold": f"{HOUSE} ExtraBold", f"{HOUSE} Black": f"{HOUSE} Black"}
def rename_old(x):
    # decks built before the rename ask for "Inter 18pt" (or "Inter 18pt 18pt") names; map them to the house family
    for w in WEIGHTS:
        for old in (f"Inter 18pt 18pt {w}", f"Inter 18pt {w}", f"Inter {w}"):
            x = x.replace(f'typeface="{old}"', f'typeface="{HOUSE} {w}"')
    for old in ("Inter 18pt 18pt", "Inter 18pt", "Inter"):
        x = x.replace(f'typeface="{old}"', f'typeface="{HOUSE}"')
    return x
for it in zin.infolist():
    data = zin.read(it.filename)
    n = it.filename
    if n.endswith(".xml"):
        x = rename_old(data.decode("utf8"))
        if n.startswith("ppt/theme/"):
            for slot in ("majorFont", "minorFont"):
                x = re.sub(rf"(<a:{slot}>\s*)<a:latin [^>]*/>", lambda m: m.group(1) + face_tag("latin", HOUSE), x)
        if n == "ppt/presentation.xml":
            x = re.sub(r'\s(embedTrueTypeFonts|saveSubsetFonts)="[^"]*"', "", x)
            x = re.sub(r"(<p:presentation\b[^>]*?)(\s*>)", r'\1 embedTrueTypeFonts="1" saveSubsetFonts="0"\2', x, count=1)
        if re.match(r"ppt/(slides|slideLayouts|slideMasters|notesSlides)/[^/]+\.xml$", n):
            def fix_rpr(m):
                rpr = m.group(0)
                lat = re.search(r'<a:latin typeface="([^"]*)"', rpr)
                face = lat.group(1) if lat else HOUSE
                if face.startswith("+") or face in ("Calibri", "Calibri Light", "Arial") and "buFont" not in rpr:
                    face = HOUSE
                if not face.startswith(HOUSE) and face != "Geist Mono": return rpr
                # bold switched on over a weight family is a synthetic bold PowerPoint cannot embed:
                # move one real weight heavier instead and switch bold off
                if face.startswith(HOUSE + " ") and re.search(r'\sb="1"', rpr.split(">", 1)[0]):
                    face = FAUX_BOLD.get(face, face)
                    rpr = re.sub(r'(<a:\w+\b[^>]*?)\sb="1"', r'\1', rpr, count=1)
                    if face == HOUSE:
                        rpr = re.sub(r"^(<a:\w+)", r'\1 b="1"', rpr, count=1)
                body = re.sub(r"<a:(latin|ea|cs)\b[^>]*/>", "", rpr)
                tags = face_tag("latin", face) + face_tag("ea", face) + face_tag("cs", face)
                if body.endswith("/>"):                      # <a:rPr .../>  -> open it
                    return body[:-2] + ">" + tags + "</a:" + m.group(1) + ">"
                # insert after fill/effect elements, before hlink etc. (schema order: ln, fill, effect, highlight, uLnTx.., latin, ea, cs, sym, hlinkClick)
                close = f"</a:{m.group(1)}>"
                inner = body[body.index(">") + 1:-len(close)]
                head = body[:body.index(">") + 1]
                k = re.search(r"<a:(sym|hlinkClick|hlinkMouseOver|rtl|extLst)\b", inner)
                pos = k.start() if k else len(inner)
                return head + inner[:pos] + tags + inner[pos:] + close
            x = re.sub(r"<a:(rPr|defRPr|endParaRPr)\b(?:[^>]*/>|[^>]*>.*?</a:\1>)", fix_rpr, x, flags=re.S)
        data = x.encode("utf8")
    zout.writestr(it, data)
zout.close()
print("finalized ->", dst)
