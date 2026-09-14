#!/usr/bin/env python3
"""Profile IQ sidebar: let long entity names scroll sideways instead of being
truncated with an ellipsis (Cousin Liz request 2026-09-08).

Applies to the child entity rows (.profile-select-item). Category headers
already wrap, and Subscriber IQ's .subscriber-show-item wrapping override
(overflow: visible) still wins by specificity, so those are unaffected.
"""
import io

PATH = "templates/index.html"

OLD = """            border-radius: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            transition: all 0.15s;
            min-width: 0;
            max-width: 100%;
"""
NEW = """            border-radius: 4px;
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

# Slim horizontal scrollbar so the sideways-scroll affordance is visible.
SB_ANCHOR = """            text-transform: uppercase;
        }

        .profile-select-item:not(.locked) {
"""
SB_NEW = """            text-transform: uppercase;
        }

        /* Slim horizontal scrollbar for profile names that overflow the
           sidebar width (see overflow-x: auto above). */
        .profile-select-item::-webkit-scrollbar { height: 5px; }
        .profile-select-item::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.25);
            border-radius: 3px;
        }
        .profile-select-item::-webkit-scrollbar-track { background: transparent; }

        .profile-select-item:not(.locked) {
"""


def main():
    with io.open(PATH, "r", encoding="utf-8") as fh:
        txt = fh.read()
    before_lines = txt.count("\n")

    assert txt.count(OLD) == 1, "OLD anchor not unique: %d" % txt.count(OLD)
    txt = txt.replace(OLD, NEW, 1)

    assert txt.count(SB_ANCHOR) == 1, "SB anchor not unique: %d" % txt.count(SB_ANCHOR)
    txt = txt.replace(SB_ANCHOR, SB_NEW, 1)

    with io.open(PATH, "w", encoding="utf-8") as fh:
        fh.write(txt)

    after_lines = txt.count("\n")
    assert txt.rstrip().endswith("</html>"), "missing trailing </html>"
    print("OK: lines %d -> %d (+%d); trailing </html> present"
          % (before_lines, after_lines, after_lines - before_lines))


if __name__ == "__main__":
    main()
