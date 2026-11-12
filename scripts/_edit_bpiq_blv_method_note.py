#!/usr/bin/env python3
"""Brand Partnership page: the Brand Lift "How this is calculated" note
names the matched group only when one is part of the valuation.

2026-11-11. The note was static text and described the lift as the
exposed group's gain minus a matched group's gain on every valuation,
including one bought without a comparison group. The note now loads with
the before and after wording, and the render sets the matched-group
wording when control_group.enabled.

One-shot byte splice of templates/index.html (never StrReplace or Write
on that file: it is too large for either). Each anchor must appear
exactly once. Run from the bg-webapp root, then:

    python3 scripts/validate_index_html.py
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"

PLAIN = ("Values only the <strong>new consumers</strong> at a higher rate than BEV: the people engaging with the "
         "brand in the campaign window beyond those who did in the days before it. A before and after read of "
         "the same audience.")
CONTROL = ("Values only the <strong>incremental consumers</strong> at a higher rate than BEV: the exposed group's "
           "gain in brand engagers minus the gain of a matched group of the same size with the same brand "
           "engagement before the partnership. These are the brand engagers who would not have engaged without "
           "the campaign.")

OLD_HTML = ('                                    <div class="bpiq-method-body">\n'
            '                                        ' + CONTROL + '\n'
            '                                    </div>\n')
NEW_HTML = ('                                    <!-- The matched group is named only when one is part of the valuation\n'
            '                                         (2026-11-11). The note loads with the before and after wording; the\n'
            '                                         render sets the matched-group wording when control_group.enabled. -->\n'
            '                                    <div class="bpiq-method-body" id="bpiqBlvMethodBody">\n'
            '                                        ' + PLAIN + '\n'
            '                                    </div>\n')

OLD_JS = ("            var gpRow = document.getElementById('bpiqGenPopKpiRow');\n"
          "            if (gpRow) gpRow.style.display = cg.enabled ? 'block' : 'none';\n")
NEW_JS = (OLD_JS
          + "            // Brand Lift \"How this is calculated\" (2026-11-11): the note names the\n"
          + "            // matched group only when one is part of the valuation. A valuation\n"
          + "            // bought without a comparison group reads as a before and after.\n"
          + "            var blvMethod = document.getElementById('bpiqBlvMethodBody');\n"
          + "            if (blvMethod) {\n"
          + "                blvMethod.innerHTML = cg.enabled\n"
          + "                    ? '" + CONTROL.replace("'", "\\'") + "'\n"
          + "                    : '" + PLAIN.replace("'", "\\'") + "';\n"
          + "            }\n")


def splice(src: str, old: str, new: str, desc: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


def main() -> None:
    src = INDEX.read_text(encoding="utf-8")
    if 'id="bpiqBlvMethodBody"' in src:
        print("already applied")
        return
    before = len(src)
    src = splice(src, OLD_HTML, NEW_HTML, "the Brand Lift note")
    src = splice(src, OLD_JS, NEW_JS, "the render")
    INDEX.write_text(src, encoding="utf-8")
    print(f"spliced: {before:,} -> {len(src):,} characters")


if __name__ == "__main__":
    main()
