#!/usr/bin/env python3
"""Crosswalk PPTX font embedder. Usage: python3 cw_pptx_embed.py in.pptx out.pptx FONT_DIR
Embeds every Crosswalk Inter face the deck uses (full font, all characters) as PowerPoint embedded fonts
(Embedded OpenType, the format PowerPoint reads and preserves). FONT_DIR holds CrosswalkInter-*.ttf."""
import sys, re, glob, os, struct, zipfile
from fontTools.ttLib import TTFont

def eot(ttf_path):
    raw = open(ttf_path, "rb").read(); f = TTFont(ttf_path); os2, head, nm = f["OS/2"], f["head"], f["name"]
    p = os2.panose
    panose = bytes([p.bFamilyType, p.bSerifStyle, p.bWeight, p.bProportion, p.bContrast, p.bStrokeVariation,
                    p.bArmStyle, p.bLetterForm, p.bMidline, p.bXHeight])
    def s(i):
        v = nm.getName(i, 3, 1, 0x409) or nm.getName(i, 1, 0, 0)
        b = str(v).encode("utf-16-le"); return struct.pack("<HH", 0, len(b)) + b
    body = (panose + struct.pack("<BBIHH", 1, os2.fsSelection & 1, os2.usWeightClass, os2.fsType, 0x504C)
            + struct.pack("<IIII", os2.ulUnicodeRange1, os2.ulUnicodeRange2, os2.ulUnicodeRange3, os2.ulUnicodeRange4)
            + struct.pack("<II", os2.ulCodePageRange1, os2.ulCodePageRange2)
            + struct.pack("<I", head.checkSumAdjustment) + b"\0" * 16
            + s(1) + s(2) + s(5) + s(4) + struct.pack("<HH", 0, 0))
    total = 16 + len(body) + len(raw)
    return struct.pack("<IIII", total, len(raw), 0x00020001, 0) + body + raw

src, dst, fdir = sys.argv[1], sys.argv[2], sys.argv[3]
# index the font folder: (family name id1, style) -> file
faces = {}
for fp in glob.glob(os.path.join(fdir, "**", "*.ttf"), recursive=True):
    if "__MACOSX" in fp or os.path.basename(fp).startswith("._"): continue
    t = TTFont(fp); n = t["name"]
    fam, sub = n.getDebugName(1), n.getDebugName(2)
    slot = {"Regular": "regular", "Bold": "bold", "Italic": "italic", "Bold Italic": "boldItalic"}.get(sub)
    if fam and slot: faces[(fam, slot)] = fp
if not faces: sys.exit("No Crosswalk Inter .ttf files found in " + fdir)

zin = zipfile.ZipFile(src)
xml = "".join(zin.read(n).decode("utf8", "ignore") for n in zin.namelist()
              if re.match(r"ppt/(slides|slideLayouts|slideMasters|notesSlides)/[^/]+\.xml$", n))
used = set()
for m in re.finditer(r"<a:(?:rPr|defRPr|endParaRPr)\b([^>]*)>(.*?)</a:(?:rPr|defRPr|endParaRPr)>", xml, re.S):
    attrs, inner = m.group(1), m.group(2)
    lat = re.search(r'<a:latin typeface="([^"]*)"', inner)
    if not lat: continue
    b = ' b="1"' in attrs; i = ' i="1"' in attrs
    used.add((lat.group(1), "boldItalic" if b and i else "bold" if b else "italic" if i else "regular"))
for fam in {f for f, _ in used}:
    used.add((fam, "regular"))                      # PowerPoint needs the regular face of any family it embeds
want = sorted(u for u in used if u in faces)
missing = sorted(u for u in used if u[0].startswith("Crosswalk Inter") and u not in faces)

p = zin.read("ppt/presentation.xml").decode("utf8")
rels = zin.read("ppt/_rels/presentation.xml.rels").decode("utf8")
p = re.sub(r"<p:embeddedFontLst>.*?</p:embeddedFontLst>", "", p, flags=re.S)
rels = re.sub(r'<Relationship[^>]*relationships/font"[^>]*/>', "", rels)
nxt = max(int(x) for x in re.findall(r'Id="rId(\d+)"', rels)) + 1
lst, relx, parts = "", "", []
order = ["regular", "bold", "italic", "boldItalic"]
for fam in sorted({f for f, _ in want}):
    lst += f'<p:embeddedFont><p:font typeface="{fam}" pitchFamily="2" charset="0"/>'
    for slot in order:
        if (fam, slot) not in faces or (fam, slot) not in want: continue
        k = len(parts) + 1; rid = f"rId{nxt}"; nxt += 1
        lst += f'<p:{slot} r:id="{rid}"/>'
        relx += f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/font" Target="fonts/font{k}.fntdata"/>'
        parts.append((f"ppt/fonts/font{k}.fntdata", eot(faces[(fam, slot)])))
    lst += "</p:embeddedFont>"
a = re.search(r"<p:notesSz[^>]*/>(\s*<p:smartTags[^>]*/>)?", p)
p = p[:a.end()] + "<p:embeddedFontLst>" + lst + "</p:embeddedFontLst>" + p[a.end():]
p = re.sub(r'\s(embedTrueTypeFonts|saveSubsetFonts)="[^"]*"', "", p)
p = re.sub(r"(<p:presentation\b[^>]*?)(\s*>)", r'\1 embedTrueTypeFonts="1" saveSubsetFonts="0"\2', p, count=1)
rels = rels.replace("</Relationships>", relx + "</Relationships>")

zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
for it in zin.infolist():
    n = it.filename
    if n.startswith("ppt/fonts/"): continue
    d = zin.read(n)
    if n == "ppt/presentation.xml": d = p.encode("utf8")
    elif n == "ppt/_rels/presentation.xml.rels": d = rels.encode("utf8")
    elif n == "[Content_Types].xml":
        c = d.decode("utf8")
        if 'Extension="fntdata"' not in c:
            c = c.replace("<Default ", '<Default Extension="fntdata" ContentType="application/x-fontdata"/><Default ', 1)
        d = c.encode("utf8")
    zout.writestr(it, d)
for n, b in parts: zout.writestr(n, b)
zout.close()
print(f"embedded {len(parts)} face(s): " + ", ".join(f"{f} {s}" for f, s in want))
if missing: print("NOT FOUND in font folder:", missing)
