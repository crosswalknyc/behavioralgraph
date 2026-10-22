#!/usr/bin/env python3
"""Render a .pptx or .docx to PDF and page images for visual QA.

Usage:
  python3 render_preview.py deck.pptx [--fonts DIR] [--out DIR] [--dpi 90]

Uses the real Crosswalk Inter files: the bundled assets/brand-files/fonts by default, or --fonts DIR.
(LibreOffice ignores fonts embedded inside a .pptx, so the preview always loads the font files from disk.
That makes this a picture of the layout, not a test of embedding: cw_embed_fonts.py checks embedding.)
If no font folder exists, it maps every
"Crosswalk Inter <Weight>" family to the matching weight of plain Inter (if Inter is installed),
which is metrically the same design, so wrapping and weight pairs preview correctly.
Writes <out>/<name>.pdf, <out>/page-NN.png and <out>/contact-sheet.png, and prints the paths.
Look at every page: the linter cannot see widows, crops, collisions or clipped labels.
"""
import argparse, glob, os, shutil, subprocess, sys, tempfile

WEIGHTS = {"Thin": "thin", "ExtraLight": "extralight", "Light": "light", "Medium": "medium",
           "SemiBold": "demibold", "ExtraBold": "extrabold", "Black": "black"}


def fonts_conf(tmp, font_dir):
    rules = []
    if font_dir:
        rules.append(f"<dir>{os.path.abspath(font_dir)}</dir>")
    else:
        for name, const in WEIGHTS.items():
            rules.append(
                f'<match target="pattern"><test name="family"><string>Crosswalk Inter {name}</string></test>'
                f'<edit name="family" mode="assign" binding="strong"><string>Inter</string></edit>'
                f'<edit name="weight" mode="assign" binding="strong"><const>{const}</const></edit></match>')
        rules.append('<match target="pattern"><test name="family"><string>Crosswalk Inter</string></test>'
                     '<edit name="family" mode="assign" binding="strong"><string>Inter</string></edit></match>')
    conf = os.path.join(tmp, "fonts.conf")
    with open(conf, "w") as f:
        f.write('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>' + "".join(rules) + "</fontconfig>")
    return conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--fonts")
    ap.add_argument("--out")
    ap.add_argument("--dpi", type=int, default=90)
    a = ap.parse_args()
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "brand-files", "fonts")
    if not a.fonts and os.path.exists(os.path.join(bundled, "CrosswalkInter-Regular.ttf")):
        a.fonts = bundled
    src = os.path.abspath(a.src)
    out = os.path.abspath(a.out or os.path.splitext(src)[0] + "_preview")
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, FONTCONFIG_FILE=fonts_conf(tmp, a.fonts), HOME=tmp)
    if not a.fonts and not subprocess.run(["fc-list", ":family=Inter"], capture_output=True, text=True).stdout.strip():
        print("warning: neither Crosswalk Inter (--fonts) nor Inter is available; the preview will use a substitute and wrap differently.")
    shutil.copy(src, os.path.join(tmp, os.path.basename(src)))
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, os.path.join(tmp, os.path.basename(src))],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
    pdf = os.path.join(tmp, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if not os.path.exists(pdf):
        sys.exit("render failed (is LibreOffice installed?)")
    final_pdf = os.path.join(out, os.path.basename(pdf))
    shutil.move(pdf, final_pdf)
    for old in glob.glob(os.path.join(out, "page-*.png")):
        os.remove(old)
    subprocess.run(["pdftoppm", "-png", "-r", str(a.dpi), final_pdf, os.path.join(out, "page")], check=True)
    raw = glob.glob(os.path.join(out, "page-*.png"))
    width = max(2, len(str(len(raw))))
    for f in raw:  # pdftoppm pads to the page count (page-1.png under 10 pages); always write page-01.png
        num = int(os.path.basename(f)[5:-4])
        os.rename(f, os.path.join(out, f"page-{num:0{width}d}.png"))
    pages = sorted(glob.glob(os.path.join(out, "page-*.png")))
    try:
        from PIL import Image
        ims = [Image.open(p) for p in pages]
        tw = 640
        th = int(tw * ims[0].height / ims[0].width)
        cols = 2 if len(ims) > 1 else 1
        rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw + (cols + 1) * 12, rows * th + (rows + 1) * 12), (128, 128, 128))
        for i, im in enumerate(ims):
            sheet.paste(im.convert("RGB").resize((tw, th)), (12 + (i % cols) * (tw + 12), 12 + (i // cols) * (th + 12)))
        sheet.save(os.path.join(out, "contact-sheet.png"))
    except Exception as e:  # contact sheet is a convenience
        print("contact sheet skipped:", e)
    print(final_pdf)
    for p in pages:
        print(p)
    if os.path.exists(os.path.join(out, "contact-sheet.png")):
        print(os.path.join(out, "contact-sheet.png"), "(all pages; still open each page to check detail)")


if __name__ == "__main__":
    main()
