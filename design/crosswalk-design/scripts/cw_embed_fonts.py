#!/usr/bin/env python3
"""Embed Crosswalk Inter in a finished file, then verify it. Run this on EVERY file before delivery.

Usage:
  python3 cw_embed_fonts.py FILE [OUT] [--fonts DIR] [--render-check] [--full]
  python3 cw_embed_fonts.py --verify FILE

FILE types:
  .pptx  theme fonts set to Crosswalk Inter, every run named explicitly, old "Inter 18pt" names renamed,
         every Crosswalk Inter face the deck uses embedded in full (all characters), then the font-safety
         check. --render-check adds the slower renders with the font missing (cw_pptx_check.py).
  .docx  theme, defaults and styles set to Crosswalk Inter, italics removed, every face in use embedded in
         full as Word embedded fonts, embedding switched on with all characters.
  email  The exception. An email (The Read's email version, sent work) or the email signature page is
         never given embedded fonts: Gmail clips messages over about 102 KB and email clients ignore
         inlined @font-face. Such files are recognized (or pass --email) and handed to cw_email_check.py.
  .html  @font-face rules with the Crosswalk Inter weights the page uses, inlined as base64 woff2, so the
         page renders in the house face on any machine and inside an artifact. Each face carries the Latin
         set plus every character found on the page; --full keeps every glyph (larger file). Stock "Inter"
         faces a page already carries do not count (Crosswalk Inter is the narrower 18pt cut); stacks that
         start with Inter get 'Crosswalk Inter' put first.
  .pdf   verify only: every font must be embedded.

OUT defaults to FILE (updated in place). --fonts defaults to ../assets/brand-files/fonts.
Exit status 1 if verification fails. Never deliver a file that fails.

Why: on a machine without Crosswalk Inter, PowerPoint and Word substitute another font and recompute every
line break, so lines rewrap and a word can be drawn twice. With the full font inside the file there is
nothing to substitute, and the file looks right outside Crosswalk.
"""
import base64, io, os, re, shutil, subprocess, sys, tempfile, uuid, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOUSE = "Crosswalk Inter"
WEIGHT_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: None, 500: "Medium", 600: "SemiBold",
                700: "Bold", 800: "ExtraBold", 900: "Black"}


def font_index(fdir):
    """(family, slot) -> path, and weight -> path, from the .ttf files in fdir."""
    from fontTools.ttLib import TTFont
    faces, by_weight = {}, {}
    for root, _, files in os.walk(fdir):
        if "__MACOSX" in root:
            continue
        for f in files:
            if not f.lower().endswith(".ttf") or f.startswith("._"):
                continue
            p = os.path.join(root, f)
            t = TTFont(p, lazy=True)
            n = t["name"]
            fam, sub = n.getDebugName(1), n.getDebugName(2)
            slot = {"Regular": "regular", "Bold": "bold", "Italic": "italic", "Bold Italic": "boldItalic"}.get(sub)
            if fam and slot:
                faces[(fam, slot)] = p
            if slot in ("regular", "bold"):
                # CSS weight from the family name: the Thin and ExtraLight files both report usWeightClass
                # 250 (set for Windows), so the OS/2 value cannot tell them apart.
                word = (fam or "").split()[-1].lower()
                named = {"thin": 100, "extralight": 200, "light": 300, "medium": 500, "semibold": 600,
                         "extrabold": 800, "black": 900}.get(word)
                by_weight[named or (700 if slot == "bold" else 400)] = p
    return faces, by_weight


# ------------------------------------------------------------------ pptx
def do_pptx(src, dst, fdir, render_check):
    tmp = tempfile.mkdtemp()
    a, b = os.path.join(tmp, "a.pptx"), os.path.join(tmp, "b.pptx")
    run([sys.executable, os.path.join(HERE, "cw_pptx_finalize.py"), src, a])
    run([sys.executable, os.path.join(HERE, "cw_pptx_embed.py"), a, b, fdir], show=True)
    shutil.move(b, dst)
    args = [sys.executable, os.path.join(HERE, "cw_pptx_check.py"), dst, "--fonts", fdir]
    if not render_check:
        args.append("--no-render")
    r = subprocess.run(args, capture_output=True, text=True)
    print(r.stdout.strip())
    return r.returncode == 0


# ------------------------------------------------------------------ docx
FONT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def obfuscate(data, guid):
    key = bytes.fromhex(guid.strip("{}").replace("-", ""))[::-1]
    head = bytes(data[i] ^ key[i % 16] for i in range(32))
    return head + data[32:]


def docx_faces_used(z):
    used = set()
    for n in z.namelist():
        if not re.match(r"word/(document|styles|header\d*|footer\d*|footnotes|endnotes|numbering)\.xml$", n):
            continue
        x = z.read(n).decode("utf8", "ignore")
        for rpr in re.findall(r"<w:rPr>.*?</w:rPr>|<w:rPrDefault>.*?</w:rPrDefault>", x, re.S):
            fm = re.search(r'<w:rFonts\b[^>]*w:ascii="([^"]+)"', rpr)
            fam = fm.group(1) if fm else None
            if fam and fam.startswith(HOUSE):
                bold = re.search(r"<w:b(\s+w:val=\"(1|true|on)\")?\s*/>", rpr) is not None
                used.add((fam, "bold" if bold and fam == HOUSE else "regular"))
    used.add((HOUSE, "regular"))  # defaults and anything typed later
    return used


def do_docx(src, dst, fdir):
    tmp = tempfile.mkdtemp()
    fin = os.path.join(tmp, "fin.docx")
    run([sys.executable, os.path.join(HERE, "cw_docx_finalize.py"), src, fin])
    faces, _ = font_index(fdir)
    zin = zipfile.ZipFile(fin)
    used = docx_faces_used(zin)
    missing = sorted(u for u in used if u not in faces)
    want = sorted(u for u in used if u in faces)
    ft = zin.read("word/fontTable.xml").decode("utf8")
    rels_name = "word/_rels/fontTable.xml.rels"
    rels = zin.read(rels_name).decode("utf8") if rels_name in zin.namelist() else \
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
    rels = re.sub(r'<Relationship[^>]*relationships/font"[^>]*/>', "", rels)
    if 'xmlns:r=' not in ft[:2000]:
        ft = re.sub(r"(<w:fonts\b)", rf'\1 xmlns:r="{R_NS}"', ft, 1)
    ft = re.sub(r"<w:embed(Regular|Bold|Italic|BoldItalic)\b[^>]*/>", "", ft)
    parts, n = [], 0
    tag = {"regular": "embedRegular", "bold": "embedBold"}
    for fam in sorted({f for f, _ in want}):
        el = ""
        for slot in ("regular", "bold"):
            if (fam, slot) not in want:
                continue
            n += 1
            guid = "{" + str(uuid.uuid4()).upper() + "}"
            rid = f"rIdCwFont{n}"
            data = open(faces[(fam, slot)], "rb").read()
            parts.append((f"word/fonts/cwfont{n}.odttf", obfuscate(data, guid)))
            rels = rels.replace("</Relationships>", f'<Relationship Id="{rid}" Type="{FONT_REL}" Target="fonts/cwfont{n}.odttf"/></Relationships>')
            el += f'<w:{tag[slot]} r:id="{rid}" w:fontKey="{guid}"/>'
        block = re.search(rf'<w:font w:name="{re.escape(fam)}"\s*>.*?</w:font>|<w:font w:name="{re.escape(fam)}"\s*/>', ft, re.S)
        if block:
            b = block.group(0)
            nb = b[:-2] + ">" + el + "</w:font>" if b.endswith("/>") else b.replace("</w:font>", el + "</w:font>")
            ft = ft.replace(b, nb)
        else:
            ft = ft.replace("</w:fonts>", f'<w:font w:name="{fam}"><w:charset w:val="00"/><w:family w:val="swiss"/>'
                                          f'<w:pitch w:val="variable"/>{el}</w:font></w:fonts>')
    zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
    for it in zin.infolist():
        nme = it.filename
        if nme.startswith("word/fonts/cwfont") or nme == rels_name:
            continue
        d = zin.read(nme)
        if nme == "word/fontTable.xml":
            d = ft.encode("utf8")
        elif nme == "[Content_Types].xml":
            c = d.decode("utf8")
            if 'Extension="odttf"' not in c:
                c = c.replace("<Default ", '<Default Extension="odttf" ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont"/><Default ', 1)
            d = c.encode("utf8")
        zout.writestr(it, d)
    zout.writestr(rels_name, rels)
    for p, d in parts:
        zout.writestr(p, d)
    zout.close()
    print(f"embedded {len(parts)} face(s): " + ", ".join(f"{f} {s}" for f, s in want))
    if missing:
        print("NOT FOUND in font folder:", missing)
    return verify_docx(dst) and not missing


def verify_docx(path):
    z = zipfile.ZipFile(path)
    s = z.read("word/settings.xml").decode("utf8", "ignore")
    ft = z.read("word/fontTable.xml").decode("utf8", "ignore")
    ok = True
    if "<w:embedTrueTypeFonts" not in s:
        print("FAIL embedding is not switched on (w:embedTrueTypeFonts)"); ok = False
    if re.search(r"<w:saveSubsetFonts(?! w:val=\"(0|false)\")", s):
        print("FAIL fonts are set to subset; embed all characters"); ok = False
    used = docx_faces_used(z)
    for fam, slot in sorted(used):
        tagname = {"regular": "embedRegular", "bold": "embedBold"}[slot]
        blk = re.search(rf'<w:font w:name="{re.escape(fam)}"\s*>(.*?)</w:font>', ft, re.S)
        if not blk or f"<w:{tagname} " not in blk.group(1):
            print(f"FAIL face used but not embedded: {fam} {slot}"); ok = False
    print("docx fonts: " + ("all Crosswalk Inter faces in use are embedded" if ok else "NOT ready to deliver"))
    return ok


# ------------------------------------------------------------------ html
LATIN = ("U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0300-0301,U+0303-0304,U+0308-0309,"
         "U+0323,U+0329,U+2000-206F,U+2074,U+20AC,U+2122,U+2190-2199,U+2212,U+2215,U+FEFF,U+FFFD,U+0100-017F")


def email_kind(path, forced=False):
    """'email' for an email HTML, 'signature' for the email signature page, else None."""
    html = open(path, encoding="utf8", errors="ignore").read()
    if "Copy signature" in html and re.search(r"signature", html[:4000], re.I):
        return "signature"
    marker = "x-apple-disable-message-reformatting" in html or "%%unsubscribe%%" in html
    if forced or (marker and not re.search(r"<script\b", html, re.I)):
        return "email"
    return None


FACE_RE = re.compile(r"@font-face\s*\{[^}]*\}", re.I)


def face_family(rule):
    m = re.search(r"font-family\s*:\s*([^;}]+)", rule, re.I)
    return m.group(1).strip() if m else ""


def drop_invalid_faces(html):
    """An @font-face font-family takes ONE name. 'Crosswalk Inter','Inter' makes the whole rule invalid, and
    browsers silently skip it. Remove such rules for Inter or Crosswalk Inter so valid faces replace them."""
    n = 0
    def fix(m):
        nonlocal n
        fam = face_family(m.group(0))
        if "," in fam and re.search(r"inter", fam, re.I):
            n += 1
            return ""
        return m.group(0)
    return FACE_RE.sub(fix, html), n


def lead_with_house(html):
    """Font stacks (and CSS custom properties holding one) that start with Inter or Inter 18pt get
    'Crosswalk Inter' put in front, so the inlined house face is the one used."""
    n = 0
    def fix(m):
        nonlocal n
        n += 1
        return m.group(1) + "'Crosswalk Inter'," + m.group(2)
    html = re.sub(r"((?:font-family|--[\w-]*(?:sans|font)[\w-]*)\s*:\s*)((?:['\"])?Inter(?: 18pt)?(?:['\"])?\s*[,;}\"])", fix, html)
    return html, n


def css_weights(html):
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)) + " " + " ".join(re.findall(r'style="([^"]*)"', html))
    ws = {400, 700}
    for v in re.findall(r"font-weight\s*:\s*([^;}\"']+)", css, re.I):
        v = v.strip().lower()
        if v.isdigit():
            ws.add(int(v))
        elif v in ("bold", "bolder"):
            ws.add(700)
        elif v == "lighter":
            ws.add(300)
    if re.search(r"<(b|strong|h[1-6]|th)\b", html, re.I):
        ws.add(700)
    for v in re.findall(r"\bvar\(--cw-weight-(\w+)\)", css):
        ws.add({"light": 300, "regular": 400, "semibold": 600, "bold": 700}.get(v, 400))
    return sorted(w for w in ws if w in WEIGHT_NAMES)


def do_html(src, dst, fdir, full=False):
    from fontTools import subset
    _, by_weight = font_index(fdir)
    html = open(src, encoding="utf8").read()
    try:
        import brotli  # noqa: F401
        flavor, fmt = "woff2", "woff2"
    except ImportError:
        flavor, fmt = "woff", "woff"
    rules = []
    html = re.sub(r'<style id="cw-fonts">.*?</style>', "", html, flags=re.S)
    html, dropped = drop_invalid_faces(html)
    if dropped:
        print(f"removed {dropped} @font-face rule(s) that named several families (browsers ignore those rules, "
              f"so the page was falling back to system fonts)")
    html, led = lead_with_house(html)
    if led:
        print(f"put 'Crosswalk Inter' first in {led} font stack(s) that started with Inter")
    already = inlined_weights(html)
    for w in css_weights(html):
        p = by_weight.get(w)
        if not p or w in already:
            continue
        opts = subset.Options()
        opts.flavor = flavor
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        f = subset.load_font(p, opts)
        s = subset.Subsetter(opts)
        if full:
            s.populate(unicodes=f.getBestCmap().keys())
        else:
            s.populate(unicodes=set(subset.parse_unicodes(LATIN)) | {ord(ch) for ch in html if ord(ch) > 127})
        s.subset(f)
        buf = io.BytesIO()
        subset.save_font(f, buf, opts)
        b64 = base64.b64encode(buf.getvalue()).decode()
        rules.append(f'@font-face{{font-family:"Crosswalk Inter";font-style:normal;font-weight:{w};font-display:swap;'
                     f'src:url(data:font/{fmt};base64,{b64}) format("{fmt}");}}')
    block = '<style id="cw-fonts">' + "".join(rules) + "</style>"
    # Just before </head>, so <meta charset> stays in the first 1024 bytes; without a head, before
    # </body> or at the end. Never before the doctype (that forces quirks mode).
    m = re.search(r"</head\s*>", html, re.I) or re.search(r"</body\s*>", html, re.I)
    if m:
        html = html[:m.start()] + block + html[m.start():]
    else:
        html = html + block
    open(dst, "w", encoding="utf8").write(html)
    print(f"inlined Crosswalk Inter weights {sorted(w for w in css_weights(html) if w not in already)} as {fmt}"
          + (f" (weights {sorted(already)} were already inside)" if already else ""))
    return verify_html(dst)


def inlined_weights(html):
    """Weights inlined as data: URLs for Crosswalk Inter. Stock "Inter" does not count: Crosswalk Inter is the
    18pt optical cut, a little narrower than the Inter text cut, so a page carrying only stock Inter sets
    differently from every other Crosswalk file."""
    have = set()
    for rule in re.findall(r"@font-face\s*\{([^}]*)\}", html, re.I):
        fam = face_family(rule).strip("'\" ").lower()
        wt = re.search(r"font-weight\s*:\s*(\d+)(?:\s+(\d+))?", rule, re.I)
        if fam == "crosswalk inter" and "data:" in rule and wt:
            lo = int(wt.group(1)); hi = int(wt.group(2) or lo)
            have.update(w for w in WEIGHT_NAMES if lo <= w <= hi)
    return have


def verify_html(path):
    html = open(path, encoding="utf8").read()
    have = inlined_weights(html)
    bad = [face_family(r) for r in FACE_RE.findall(html) if "," in face_family(r)]
    if bad:
        print(f"FAIL {len(bad)} @font-face rule(s) name several families ({bad[0][:40]}): browsers ignore them. "
              f"Run cw_embed_fonts.py on the file to replace them.")
        have = set() if all(re.search("inter", b, re.I) for b in bad) else have
    need = set(css_weights(html))
    ok = True
    if not have:
        print("FAIL no inlined Crosswalk Inter @font-face"); ok = False
    for w in sorted(need - have):
        print(f"FAIL weight {w} is used but not inlined"); ok = False
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I))
    for m in re.finditer(r"font-family\s*:\s*([^;}]+)", css):
        first = m.group(1).split(",")[0].strip().strip("'\"").lower()
        if first not in ("crosswalk inter", "inter", "geist mono", "inherit") and not first.startswith("var("):
            print(f"WARN a font stack starts with '{first}'; lead with \"Crosswalk Inter\"")
    print("html fonts: " + ("Crosswalk Inter is inside the page" if ok else "NOT ready to deliver"))
    return ok


# ------------------------------------------------------------------ pdf
def verify_pdf(path):
    r = subprocess.run(["pdffonts", path], capture_output=True, text=True)
    ok, rows = True, r.stdout.splitlines()[2:]
    for line in rows:
        cols = line.split()
        name = cols[0] if cols else ""
        emb = re.search(r"\s(yes|no)\s+(yes|no)\s+(yes|no)\s", line)
        if emb and emb.group(1) == "no":
            print(f"FAIL font not embedded in PDF: {name}"); ok = False
        if name and "Crosswalk" not in name and "Geist" not in name and "Inter" not in name:
            print(f"WARN non-house font in PDF: {name}")
    print("pdf fonts: " + ("all embedded" if ok else "NOT ready to deliver"))
    return ok


def run(cmd, show=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"{os.path.basename(cmd[1])} failed:\n{r.stdout}\n{r.stderr}")
    if show and r.stdout.strip():
        print(r.stdout.strip())


def main():
    argv = sys.argv[1:]
    fdir = os.path.join(HERE, "..", "assets", "brand-files", "fonts")
    if "--fonts" in argv:
        i = argv.index("--fonts"); fdir = argv[i + 1]; del argv[i:i + 2]
    render_check = "--render-check" in argv
    full = "--full" in argv
    verify = "--verify" in argv
    argv = [a for a in argv if not a.startswith("--")]
    if not argv:
        sys.exit(__doc__)
    src = argv[0]
    dst = argv[1] if len(argv) > 1 else src
    ext = os.path.splitext(src)[1].lower()
    if not verify and ext != ".pdf" and not os.path.exists(os.path.join(fdir, "CrosswalkInter-Regular.ttf")):
        sys.exit(f"Crosswalk Inter not found in {fdir}. Pass --fonts DIR or ask for Crosswalk-Inter-v2.zip. Do not substitute another font.")
    kind = email_kind(src, "--email" in sys.argv) if ext in (".html", ".htm") else None
    if kind == "signature":
        print("email signature page: signatures carry no embedded fonts (email clients ignore them). "
              "They use the stack 'Inter 18pt','Inter',Arial,Helvetica,sans-serif and a hosted logo. Nothing to embed.")
        print("READY: signature page (no fonts to embed)")
        sys.exit(0)
    if kind == "email":
        print("email HTML: fonts are never embedded in email (Gmail clips over ~102 KB). Running the email check instead.")
        r = subprocess.run([sys.executable, os.path.join(HERE, "cw_email_check.py"), src], capture_output=True, text=True)
        print(r.stdout.strip())
        sys.exit(r.returncode)
    if verify:
        ok = {".docx": verify_docx, ".html": verify_html, ".htm": verify_html, ".pdf": verify_pdf}.get(ext)
        if ext == ".pptx":
            r = subprocess.run([sys.executable, os.path.join(HERE, "cw_pptx_check.py"), src, "--no-render"], capture_output=True, text=True)
            print(r.stdout.strip()); ok = r.returncode == 0
        elif ok:
            ok = ok(src)
        else:
            sys.exit(f"unsupported type {ext}")
    elif ext == ".pptx":
        ok = do_pptx(src, dst, fdir, render_check)
    elif ext == ".docx":
        tmp = dst + ".tmp.docx"
        ok = do_docx(src, tmp, fdir); shutil.move(tmp, dst)
    elif ext in (".html", ".htm"):
        ok = do_html(src, dst, fdir, full)
    elif ext == ".pdf":
        ok = verify_pdf(src)
    else:
        sys.exit(f"unsupported type {ext}")
    print("READY: fonts embedded" if ok else "NOT READY: fix the failures above before delivering")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
