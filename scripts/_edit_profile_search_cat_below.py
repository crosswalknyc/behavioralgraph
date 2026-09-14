#!/usr/bin/env python3
"""Profile selector search results: the category label sat to the RIGHT of the
name in a flex row, squeezing long names into a narrow column that then broke
character-by-character ("AGGREG ATE UNLIKELY..."). Move the category BENEATH
the name so the name gets full width and wraps at word boundaries. Also relax
the over-aggressive word-break on .profile-select-item (and the BP name span)
so normal words stop mid-word breaking.
"""
import io

PATH = "templates/index.html"

# 1) Relax the aggressive word-break in the .profile-select-item wrap rule.
OLD_CSS = """            white-space: normal;
            overflow-wrap: break-word;
            word-break: break-word;
            line-height: 1.3;
"""
NEW_CSS = """            white-space: normal;
            overflow-wrap: break-word;
            line-height: 1.3;
"""

# 2) BP name span: same relax (drop word-break, keep overflow-wrap).
OLD_BP = ("overflow-wrap:break-word;word-break:break-word;line-height:1.3;\">' "
          "+ esc(f.name || f.key) + '</span>'")
NEW_BP = ("overflow-wrap:break-word;line-height:1.3;\">' "
          "+ esc(f.name || f.key) + '</span>'")

# 3) Search-result item templates: stack category beneath the name.
def _old_label(name_expr):
    return ("`<div style=\"display: flex; align-items: center; gap: 0.5rem;\">"
            "<span style=\"font-size: 0.9rem; opacity: 0.7;\">${isLocked ? '\U0001f512' : icon}</span>"
            "<span style=\"flex: 1;\">${escapeAttr(" + name_expr + ")}</span>"
            "<span style=\"font-size: 0.75rem; color: var(--text-secondary); opacity: 0.6;\">"
            "${getCategoryDisplayLabel(cat)}</span></div>`")

def _new_label(name_expr):
    return ("`<div style=\"display: flex; align-items: flex-start; gap: 0.5rem;\">"
            "<span style=\"font-size: 0.9rem; opacity: 0.7; flex-shrink: 0;\">${isLocked ? '\U0001f512' : icon}</span>"
            "<span style=\"flex: 1; min-width: 0;\">"
            "<span style=\"display: block;\">${escapeAttr(" + name_expr + ")}</span>"
            "<span style=\"display: block; font-size: 0.7rem; color: var(--text-secondary); opacity: 0.6; margin-top: 2px;\">"
            "${getCategoryDisplayLabel(cat)}</span></span></div>`")


def main():
    with io.open(PATH, "r", encoding="utf-8") as fh:
        txt = fh.read()
    before = txt.count("\n")

    edits = [
        ("css word-break", OLD_CSS, NEW_CSS),
        ("bp span word-break", OLD_BP, NEW_BP),
        ("search label (single)", _old_label("fullName"), _new_label("fullName")),
        ("search label (grouped)", _old_label("subjectDisplayName"), _new_label("subjectDisplayName")),
    ]
    for label, old, new in edits:
        n = txt.count(old)
        assert n == 1, "anchor not unique (%d) for %s" % (n, label)
        txt = txt.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8") as fh:
        fh.write(txt)

    after = txt.count("\n")
    assert txt.rstrip().endswith("</html>"), "missing trailing </html>"
    assert "word-break: break-word" not in txt, "leftover CSS word-break"
    assert "word-break:break-word" not in txt, "leftover inline word-break"
    print("OK: lines %d -> %d (%+d); trailing </html> present" % (before, after, after - before))


if __name__ == "__main__":
    main()
