#!/usr/bin/env python3
"""Crosswalk Word finalizer.

Usage:
  python3 cw_docx_finalize.py in.docx out.docx

What it does:
- theme major/minor Latin fonts -> Crosswalk Inter (so nothing inherits Calibri or Aptos)
- document defaults and every style's rFonts -> Crosswalk Inter (weight families kept)
- older names ("Inter 18pt 18pt", "Inter 18pt", "Inter", with weight suffixes) and stray
  Calibri/Aptos/Arial/Helvetica runs renamed to the Crosswalk Inter families
- italics removed from styles and runs (the brand uses none)
- settings: embed TrueType fonts, embed all characters (no subsetting)
- prints any typed page numbers it finds in footers (they should be PAGE fields)

Word writes the font data itself the next time the file is saved on a machine with
Crosswalk Inter installed. For anything leaving Crosswalk, also send a PDF.
"""
import re
import sys
import zipfile

HOUSE = "Crosswalk Inter"
WEIGHTS = ["Thin", "ExtraLight", "Light", "Medium", "SemiBold", "ExtraBold", "Black"]
STRAY = ["Calibri Light", "Calibri", "Aptos Display", "Aptos", "Arial", "Helvetica Neue", "Helvetica"]


def house_name(old):
    for w in WEIGHTS:
        for prefix in ("Inter 18pt 18pt ", "Inter 18pt ", "Inter "):
            if old == prefix + w:
                return f"{HOUSE} {w}"
    if old in ("Inter 18pt 18pt", "Inter 18pt", "Inter") or old in STRAY:
        return HOUSE
    return None


def fix_rfonts(x):
    def rep(m):
        tag = m.group(0)
        def sub(a):
            v = a.group(2)
            if a.group(1) in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
                return ""  # drop theme pointers so the explicit face wins
            new = house_name(v)
            return f' w:{a.group(1)}="{new}"' if new else a.group(0)
        tag = re.sub(r'\s+w:(ascii|hAnsi|eastAsia|cs|asciiTheme|hAnsiTheme|eastAsiaTheme|cstheme)="([^"]*)"', sub, tag)
        if "w:ascii=" not in tag:
            tag = tag.replace("<w:rFonts", f'<w:rFonts w:ascii="{HOUSE}" w:hAnsi="{HOUSE}" w:cs="{HOUSE}"', 1)
        return tag
    return re.sub(r"<w:rFonts\b[^>]*/>", rep, x)


def drop_italics(x):
    x = re.sub(r"<w:i(Cs)?(\s+w:val=\"(1|true|on)\")?\s*/>", "", x)
    return x


def ensure_doc_defaults(styles):
    if "<w:docDefaults>" not in styles:
        styles = styles.replace("<w:latentStyles", "<w:docDefaults><w:rPrDefault><w:rPr></w:rPr></w:rPrDefault></w:docDefaults><w:latentStyles", 1) \
            if "<w:latentStyles" in styles else re.sub(r"(<w:styles\b[^>]*>)", r"\1<w:docDefaults><w:rPrDefault><w:rPr></w:rPr></w:rPrDefault></w:docDefaults>", styles, 1)
    m = re.search(r"<w:rPrDefault>\s*(<w:rPr>|<w:rPr/>)", styles)
    if m and m.group(1) == "<w:rPr/>":
        styles = styles.replace("<w:rPrDefault><w:rPr/>", "<w:rPrDefault><w:rPr></w:rPr>", 1)
    block = re.search(r"<w:rPrDefault>\s*<w:rPr>(.*?)</w:rPr>", styles, re.S)
    if block and "<w:rFonts" not in block.group(1):
        styles = styles.replace(block.group(0), block.group(0).replace("<w:rPr>", f'<w:rPr><w:rFonts w:ascii="{HOUSE}" w:hAnsi="{HOUSE}" w:eastAsia="{HOUSE}" w:cs="{HOUSE}"/>', 1), 1)
    return styles


def fix_settings(s):
    s = re.sub(r"<w:embedTrueTypeFonts\b[^>]*/>", "", s)
    s = re.sub(r"<w:saveSubsetFonts\b[^>]*/>", "", s)
    s = re.sub(r"<w:embedSystemFonts\b[^>]*/>", "", s)
    # embedTrueTypeFonts belongs near the top of w:settings (after writeProtection/view/zoom/removePersonalInformation...).
    ins = '<w:embedTrueTypeFonts/>'
    prior = ("writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime",
             "doNotDisplayPageBoundaries", "displayBackgroundShape", "printPostScriptOverText",
             "printFractionalCharacterWidth", "printFormsData")
    ends = [m.end() for m in re.finditer(rf"<w:({'|'.join(prior)})\b[^>]*?(/>|>.*?</w:\1>)", s, re.S)]
    if ends:
        e = max(ends)
        s = s[:e] + ins + s[e:]
    else:
        s = re.sub(r"(<w:settings\b[^>]*>)", r"\1" + ins, s, 1)
    return s


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    zin = zipfile.ZipFile(src)
    zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
    typed = []
    for it in zin.infolist():
        data = zin.read(it.filename)
        n = it.filename
        if n.endswith(".xml"):
            x = data.decode("utf8")
            if n.startswith("word/theme/"):
                for slot in ("majorFont", "minorFont"):
                    x = re.sub(rf'(<a:{slot}>\s*<a:latin typeface=")[^"]*(")', rf"\g<1>{HOUSE}\g<2>", x)
            elif re.match(r"word/(document|styles|header\d*|footer\d*|footnotes|endnotes|numbering|comments)\.xml$", n):
                x = fix_rfonts(x)
                x = drop_italics(x)
                if n == "word/styles.xml":
                    x = ensure_doc_defaults(x)
                if re.match(r"word/footer\d*\.xml$", n):
                    has_field = re.search(r"(fldSimple[^>]*instr=\"\s*PAGE|<w:instrText[^>]*>\s*PAGE)", x)
                    if not has_field and re.search(r"<w:t[^>]*>\s*\d{1,3}\s*</w:t>", x):
                        typed.append(n)
            elif n == "word/settings.xml":
                x = fix_settings(x)
            elif n == "word/fontTable.xml":
                x = re.sub(r'w:name="(Inter( 18pt)*( (?:Thin|ExtraLight|Light|Medium|SemiBold|ExtraBold|Black))?)"',
                           lambda m: f'w:name="{house_name(m.group(1)) or m.group(1)}"', x)
            data = x.encode("utf8")
        zout.writestr(it, data)
    zout.close()
    print("finalized ->", dst)
    for n in typed:
        print(f"WARNING {n}: page number looks typed. Replace it with a PAGE field.")


if __name__ == "__main__":
    main()
