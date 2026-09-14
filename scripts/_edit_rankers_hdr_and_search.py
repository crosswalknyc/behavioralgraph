#!/usr/bin/env python3
"""Rankers sheet cards:
1. Keep the card header (e.g. 'SPOTIFY DAILY TOP 200 (US)') and its
   Export CSV pill each on one line (nowrap + slightly smaller header
   font + non-shrinking export pill).
2. Light mode: card search fields become white with a black outline and
   black text (overriding the hard-coded dark inline styles).
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    n = src.count(old)
    assert n == 1, "anchor not unique (%d): %s" % (n, label)
    return src.replace(old, new)

# 1a. Header h4: nowrap + slightly smaller so the longest label fits one line.
src = sub_once(
    "            #trendsIQView .tiq-sheet-cardhead h4 { margin: 0; }",
    "            #trendsIQView .tiq-sheet-cardhead h4 { margin: 0; white-space: nowrap; font-size: 0.82rem; }",
    "cardhead h4 nowrap",
)
# 1b. Export wrapper doesn't shrink (so the pill never gets squeezed/wrapped).
src = sub_once(
    "            #trendsIQView .tiq-export { position: relative; }",
    "            #trendsIQView .tiq-export { position: relative; flex: 0 0 auto; }",
    "export flex noshrink",
)
# 1c. Export button text stays on one line.
src = sub_once(
    "border-radius: 6px; padding: 3px 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 0.3rem; }",
    "border-radius: 6px; padding: 3px 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 0.3rem; white-space: nowrap; }",
    "export-btn nowrap",
)

# 2. Light-mode card search fields: white bg, black outline, black text.
anchor = "            #trendsIQView .tiq-export-menu button:hover { background: rgba(148,163,184,0.16); }"
search_css = anchor + (
    "\n            /* Light mode: card search fields (inline-styled dark in JS)"
    " become white with a black outline + black text. (Jessie 2026-09-02) */"
    '\n            body[data-theme="light"] #trendsIQView .tiq-card-search {'
    " background: #ffffff !important; color: #000000 !important; border-color: #000000 !important; }"
    '\n            body[data-theme="light"] #trendsIQView .tiq-card-search::placeholder { color: #555555 !important; }'
)
src = sub_once(anchor, search_css, "light search css")

assert src.rstrip().endswith("</html>")
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
