#!/usr/bin/env python3
"""Revert the sidebar sideways-scroll for long names and WRAP them instead so
they fit the menu width (utility choice: Cousins Liz & Jenna preferred
wrapping over horizontal scroll). Covers Profile IQ (.profile-select-item)
and Brand Partnership (.bpiq-name-scroll span). Removes the now-unused slim
scrollbar rules.
"""
import io

PATH = "templates/index.html"

# 1) Profile IQ item: horizontal scroll -> wrap.
OLD_ITEM = """            border-radius: 4px;
            white-space: nowrap;
            /* Long entity names no longer truncate with an ellipsis; the row
               scrolls horizontally so the full title can be read.
               (Cousin Liz request 2026-09-08) */
            overflow-x: auto;
            overflow-y: hidden;
            transition: all 0.15s;
            min-width: 0;
            max-width: 100%;
"""
NEW_ITEM = """            border-radius: 4px;
            /* Long entity names WRAP to fit the menu width. Utility choice
               (Cousins Liz & Jenna): wrapping beats sideways scroll for
               readability. (2026-09-08) */
            white-space: normal;
            overflow-wrap: break-word;
            word-break: break-word;
            line-height: 1.3;
            transition: all 0.15s;
            min-width: 0;
            max-width: 100%;
"""

# 2) Remove both slim-scrollbar blocks (no longer needed once wrapping).
OLD_SB = """        /* Slim horizontal scrollbar for profile names that overflow the
           sidebar width (see overflow-x: auto above). */
        .profile-select-item::-webkit-scrollbar { height: 5px; }
        .profile-select-item::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.25);
            border-radius: 3px;
        }
        .profile-select-item::-webkit-scrollbar-track { background: transparent; }

        /* Brand Partnership sidebar: long partnership names scroll
           sideways (same affordance as Profile IQ).
           (Cousin Liz request 2026-09-08) */
        .bpiq-name-scroll::-webkit-scrollbar { height: 5px; }
        .bpiq-name-scroll::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.25);
            border-radius: 3px;
        }
        .bpiq-name-scroll::-webkit-scrollbar-track { background: transparent; }

        .profile-select-item:not(.locked) {
"""
NEW_SB = """        .profile-select-item:not(.locked) {
"""

# 3) Brand Partnership name span: horizontal scroll -> wrap.
OLD_BP = (
    "                                + '<span class=\"bpiq-name-scroll\" style=\"flex:1;"
    "min-width:0;overflow-x:auto;overflow-y:hidden;white-space:nowrap;\">' + esc(f.name || f.key) + '</span>'\n"
)
NEW_BP = (
    "                                + '<span style=\"flex:1;min-width:0;white-space:normal;"
    "overflow-wrap:break-word;word-break:break-word;line-height:1.3;\">' + esc(f.name || f.key) + '</span>'\n"
)


def main():
    with io.open(PATH, "r", encoding="utf-8") as fh:
        txt = fh.read()
    before = txt.count("\n")

    for label, old, new in (("item", OLD_ITEM, NEW_ITEM),
                            ("scrollbars", OLD_SB, NEW_SB),
                            ("bp-span", OLD_BP, NEW_BP)):
        n = txt.count(old)
        assert n == 1, "anchor not unique (%d) for %s" % (n, label)
        txt = txt.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8") as fh:
        fh.write(txt)

    after = txt.count("\n")
    assert txt.rstrip().endswith("</html>"), "missing trailing </html>"
    assert "bpiq-name-scroll" not in txt, "leftover bpiq-name-scroll reference"
    assert "profile-select-item::-webkit-scrollbar" not in txt, "leftover scrollbar rule"
    print("OK: lines %d -> %d (%+d); trailing </html> present; scroll rules removed"
          % (before, after, after - before))


if __name__ == "__main__":
    main()
