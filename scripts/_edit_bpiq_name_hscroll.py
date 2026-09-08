#!/usr/bin/env python3
"""Brand Partnership IQ sidebar: let long partnership names scroll sideways
instead of truncating with an ellipsis, matching the Profile IQ behavior
(Cousin Liz request 2026-09-08).

The BP name is an inline-styled <span> inside a flex .profile-tree-item row.
Swap its ellipsis for horizontal overflow scrolling (+ flex:1;min-width:0 so
it stays constrained to the row) and add a slim scrollbar.
"""
import io

PATH = "templates/index.html"

# 1) BP name span: ellipsis -> horizontal scroll.
OLD_SPAN = (
    "                                + '<span style=\"overflow:hidden;"
    "text-overflow:ellipsis;white-space:nowrap;\">' + esc(f.name || f.key) + '</span>'\n"
)
NEW_SPAN = (
    "                                + '<span class=\"bpiq-name-scroll\" style=\"flex:1;"
    "min-width:0;overflow-x:auto;overflow-y:hidden;white-space:nowrap;\">' + esc(f.name || f.key) + '</span>'\n"
)

# 2) Slim scrollbar for the BP name span (mirrors .profile-select-item).
CSS_ANCHOR = "        .profile-select-item::-webkit-scrollbar-track { background: transparent; }\n"
CSS_NEW = CSS_ANCHOR + (
    "\n"
    "        /* Brand Partnership sidebar: long partnership names scroll\n"
    "           sideways (same affordance as Profile IQ).\n"
    "           (Cousin Liz request 2026-09-08) */\n"
    "        .bpiq-name-scroll::-webkit-scrollbar { height: 5px; }\n"
    "        .bpiq-name-scroll::-webkit-scrollbar-thumb {\n"
    "            background: rgba(255, 255, 255, 0.25);\n"
    "            border-radius: 3px;\n"
    "        }\n"
    "        .bpiq-name-scroll::-webkit-scrollbar-track { background: transparent; }\n"
)


def main():
    with io.open(PATH, "r", encoding="utf-8") as fh:
        txt = fh.read()
    before = txt.count("\n")

    assert txt.count(OLD_SPAN) == 1, "span anchor not unique: %d" % txt.count(OLD_SPAN)
    txt = txt.replace(OLD_SPAN, NEW_SPAN, 1)

    assert txt.count(CSS_ANCHOR) == 1, "css anchor not unique: %d" % txt.count(CSS_ANCHOR)
    txt = txt.replace(CSS_ANCHOR, CSS_NEW, 1)

    with io.open(PATH, "w", encoding="utf-8") as fh:
        fh.write(txt)

    after = txt.count("\n")
    assert txt.rstrip().endswith("</html>"), "missing trailing </html>"
    print("OK: lines %d -> %d (+%d); trailing </html> present" % (before, after, after - before))


if __name__ == "__main__":
    main()
