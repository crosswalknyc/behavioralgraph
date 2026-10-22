#!/usr/bin/env python3
"""Check an email HTML (The Read, sent work) before it goes to the newsletter hub.

Usage:
  python3 cw_email_check.py email.html [--package DIR]

Email is the one Crosswalk format that does NOT carry embedded fonts: Gmail clips any message over
about 102 KB, and email clients ignore inlined @font-face. The email links Inter from Google Fonts
(Apple Mail and iPhone load it) and falls back to Helvetica or Arial everywhere else.

Checks (FAIL blocks sending, WARN means look at it):
  - size under Gmail's clipping limit (FAIL at 102 KB, WARN from 90 KB)
  - no <script>, no data: URIs (embedded images and fonts), no inlined @font-face
  - every <img> is an absolute https URL, has alt text and a width
  - the hidden preheader is present
  - a 600 px wrapper table
  - a "view the animated version" / web-version link when a web version exists
  - unsubscribe and preferences tags (%%unsubscribe%%, %%preferences%%) in the footer
  - a font stack that starts with Crosswalk Inter and ends in Helvetica/Arial/sans-serif
  - with --package DIR: every image the email points at exists in the package's images folder,
    and every GIF opens on a finished frame (first frame not blank)
Run cw_brand_lint.py on the same file for copy and color rules.
"""
import os, re, sys

FAIL, WARN = [], []


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    path = a[0]
    pkg = a[a.index("--package") + 1] if "--package" in a else None
    raw = open(path, "rb").read()
    html = raw.decode("utf8", "ignore")
    kb = len(raw) / 1024
    if kb > 102:
        FAIL.append(f"{kb:.0f} KB: Gmail clips messages over about 102 KB. Move images to hosted files and trim markup.")
    elif kb > 90:
        WARN.append(f"{kb:.0f} KB: close to Gmail's 102 KB clipping limit.")
    if re.search(r"<script\b", html, re.I):
        FAIL.append("<script> found: email clients strip it. Animation in email is GIF only; the motion lives in the web version.")
    if "data:" in html and re.search(r"(src|href|url)\s*[=(]\s*['\"]?data:", html, re.I):
        FAIL.append("data: URI found: embedded images or fonts are stripped or clipped in email. Host the file and link it.")
    if re.search(r"@font-face", html, re.I):
        FAIL.append("@font-face found: do not inline fonts in email. Link Inter from Google Fonts and keep the fallback stack.")
    imgs = re.findall(r"<img\b[^>]*>", html, re.I)
    for tag in imgs:
        src = re.search(r'src="([^"]*)"', tag)
        s = src.group(1) if src else ""
        if not s.startswith("https://"):
            FAIL.append(f"image not on an absolute https URL: {s[:80]}")
        if not re.search(r'alt="[^"]+"', tag):
            WARN.append(f"image without alt text: {s[-60:]}")
        if not re.search(r'width="\d+"', tag):
            WARN.append(f"image without a width attribute (Outlook needs it): {s[-60:]}")
    if not re.search(r'display:\s*none[^"]*max-height:\s*0', html, re.I):
        WARN.append("no hidden preheader: add one sentence that carries the finding (it shows next to the subject line).")
    if not re.search(r'width="600"', html):
        WARN.append("no 600 px wrapper table found.")
    if not re.search(r"(animated version|view in browser|web version)", html, re.I):
        WARN.append("no link to the web version: add \"View the animated version\" at the top when there is one.")
    for tagname in ("%%unsubscribe%%", "%%preferences%%"):
        if tagname not in html:
            WARN.append(f"{tagname} not found: the footer needs the hub's unsubscribe and preferences tags.")
    stacks = set(re.findall(r"font-family:\s*([^;\"]+)", html))
    for st in stacks:
        fams = [f.strip().strip("'\"").lower() for f in st.split(",")]
        if fams[0] not in ("crosswalk inter", "inter 18pt", "inter", "geist mono"):
            WARN.append(f"font stack starts with {fams[0]!r}: lead with 'Crosswalk Inter','Inter 18pt','Inter'.")
        if not any(f in ("helvetica", "arial", "sans-serif", "monospace") for f in fams):
            WARN.append(f"font stack without a system fallback: {st[:70]}")
    if "fonts.googleapis.com" not in html:
        WARN.append("no Google Fonts link for Inter: Apple Mail and iPhone then fall back to Helvetica.")
    if pkg:
        imgdir = None
        for root, dirs, files in os.walk(pkg):
            if os.path.basename(root) == "images":
                imgdir = root
                break
        if not imgdir:
            FAIL.append(f"no images folder inside {pkg}")
        else:
            have = set(os.listdir(imgdir))
            for tag in imgs:
                s = re.search(r'src="([^"]*)"', tag)
                name = os.path.basename(s.group(1).split("?")[0]) if s else ""
                if name and name not in have:
                    FAIL.append(f"{name} is linked in the email but missing from {imgdir}")
            try:
                from PIL import Image
                for f in sorted(have):
                    if f.lower().endswith(".gif"):
                        im = Image.open(os.path.join(imgdir, f)).convert("L")
                        lo, hi = im.getextrema()
                        if hi - lo < 12:
                            WARN.append(f"{f}: the first frame looks empty. Outlook on Windows shows only frame one, so start each GIF on the finished chart.")
            except ImportError:
                pass
    for w in WARN:
        print("WARN ", w)
    for f in FAIL:
        print("FAIL ", f)
    print(f"{kb:.0f} KB, {len(imgs)} images: " + ("READY to hand to the hub" if not FAIL else "NOT ready"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
