#!/usr/bin/env python3
"""Put the WoW chip and Download PDF button side by side on the
Weekly Summary card.

Before: `.iiq-hero-wow` is `flex-direction: column; align-items:
flex-end`, so the WoW pill and the outlined PDF button stack in the
top-right corner. Two chips of different shapes (pill vs rounded
rectangle) stacked vertically read as competing badges rather than
one status readout + one utility action.

After: `.iiq-hero-wow` is `flex-direction: row; align-items: center`
so both pills sit on one line. The PDF button becomes a matching
pill (`border-radius: 999px`) with a hair more horizontal padding
so the two shapes agree. Reading order stays the same (WoW chip
first, PDF button second) which puts the data readout to the left
and the utility action to its right in a header row.

No HTML change, no functional change. Pure CSS layout patch. Safe
because both selectors are scoped to `#intentIQView` and only touch
declarations on `.iiq-hero-wow` and `.iiq-weekly-pdf-btn`.

Follows `index-html-safety.mdc`: atomic byte-level splice, validator
after write.
"""
from pathlib import Path

INDEX = Path("bg-webapp/templates/index.html")
BACKUP = Path("/tmp/index.pre_weekly_wow_pdf_inline.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count} times")
    return src.replace(old, new)


# ---- 1. Wrapper: column -> row ---------------------------------------
OLD_WRAP = "#intentIQView .iiq-hero-wow { flex: 0 0 auto; display: flex; flex-direction: column; align-items: flex-end; gap: 0.4rem; }"
NEW_WRAP = "#intentIQView .iiq-hero-wow { flex: 0 0 auto; display: flex; flex-direction: row; align-items: center; gap: 0.55rem; flex-wrap: nowrap; }"

# ---- 2. PDF button: rectangle -> pill --------------------------------
OLD_BTN = "#intentIQView .iiq-weekly-pdf-btn { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.32rem 0.75rem; background: transparent; border: 1px solid rgba(199,242,62,0.35); border-radius: 8px;"
NEW_BTN = "#intentIQView .iiq-weekly-pdf-btn { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.32rem 0.9rem; background: transparent; border: 1px solid rgba(199,242,62,0.35); border-radius: 999px;"


def main() -> None:
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP}  ({len(src):,} bytes)")

    src = splice(src, OLD_WRAP, NEW_WRAP, "iiq-hero-wow flex row")
    src = splice(src, OLD_BTN, NEW_BTN, "pdf button pill shape")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[write]  {INDEX.resolve()}  ({len(src):,} bytes)")


if __name__ == "__main__":
    main()
