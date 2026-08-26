#!/usr/bin/env python3
"""Force every dashboard value to render in UPPERCASE.

User directive 2026-08-26 (Jenna):
> "on the dashboard ouput make sure all values are alwasy in capital
>  letters. no mixed cases"

Context: profile CSVs carry hostmap-canonical casing (Coca-Cola,
TikTok, Apple TV+, New York Ny, Reba McEntire, ...). The dashboard
was rendering those values as-is, so cards showed a mix of "TikTok"
+ "New York Ny" + "TOTAL UNIVERSE" side by side. Jenna wants
everything consistently uppercase on the visual output.

Solution: a single CSS rule on `body` that inherits down to every
descendant, with a small opt-out list for controls where mixed case
must be preserved (form inputs the user types into, raw code /
clickstream slugs, and an escape-hatch class `.no-uppercase`).

Why CSS (not JS mutation, not CSV rewrite):
  - Non-destructive: underlying DOM textContent stays canonical, so
    copy-paste, CSV exports, search matching, JS logic, and API
    responses all continue to see the original values.
  - Zero drift: any new content added later (new cards, new profile
    types, new tabs) automatically inherits without code changes.
  - Emojis / numbers / punctuation are untouched by text-transform.

Placement: inserted at the END of the first big `<style>` block
(line ~17971, right before `</style>`) so it sits with the base
dashboard styles. `!important` is used because there are ~833
pre-existing text-transform declarations in this file - some set
uppercase, some set lowercase, capitalize, or none. Without
`!important` my body rule would lose to any class-specific selector.

The opt-outs also use `!important` so the base body rule can't
override them via inheritance.

Byte-safe per `index-html-safety.mdc`: uses str.count guards
(exactly 1 anchor match required) and a pre-write backup. Never
uses StrReplace / Write on the file.
"""

from pathlib import Path
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "templates" / "index.html"

STAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
BACKUP = Path("/tmp") / f"index.pre_uppercase_dashboard_{STAMP}.html"


# Anchor: the final CSS block inside the first big <style> block, plus
# the closing </style>. Verified unique (1 occurrence) as of the file
# state this script was authored against.
OLD = (
    "        .jiq-path-ribbon .arrow {\n"
    "            font-size: 11px;\n"
    "            color: var(--text-secondary);\n"
    "            opacity: 0.55;\n"
    "            line-height: 1;\n"
    "        }\n"
    "    </style>\n"
)

NEW = (
    "        .jiq-path-ribbon .arrow {\n"
    "            font-size: 11px;\n"
    "            color: var(--text-secondary);\n"
    "            opacity: 0.55;\n"
    "            line-height: 1;\n"
    "        }\n"
    "\n"
    "        /* ---- All dashboard values UPPERCASE (Jenna 2026-08-26) ------------\n"
    "           Values in profile CSVs carry hostmap-canonical casing (TikTok,\n"
    "           New York Ny, Coca-Cola, Reba McEntire, ...). The dashboard now\n"
    "           renders every string in uppercase so cards read consistently\n"
    "           against the always-uppercase category headers.\n"
    "\n"
    "           Display-only: the underlying DOM textContent stays canonical\n"
    "           so copy-paste, CSV exports, search matching, and JS logic all\n"
    "           still see the original values.\n"
    "\n"
    "           Opt-outs (case-preserving):\n"
    "             - Form controls: user-typed text renders as typed.\n"
    "             - code / pre: raw slugs preserve original typography.\n"
    "             - .no-uppercase: escape hatch for the rare element that\n"
    "               genuinely needs mixed case (add sparingly, with reason).\n"
    "           `!important` beats the ~800 pre-existing text-transform\n"
    "           declarations in this file. */\n"
    "        body {\n"
    "            text-transform: uppercase !important;\n"
    "        }\n"
    "        input, textarea, select, option,\n"
    "        input::placeholder, textarea::placeholder,\n"
    "        code, pre, kbd, samp,\n"
    "        .no-uppercase, .no-uppercase * {\n"
    "            text-transform: none !important;\n"
    "        }\n"
    "    </style>\n"
)


def main() -> int:
    if not INDEX.is_file():
        print(f"[uppercase] {INDEX} not found")
        return 2

    src = INDEX.read_text(encoding="utf-8")
    orig_bytes = len(src.encode("utf-8"))
    print(f"[uppercase] {INDEX} ({orig_bytes:,} bytes)")

    n = src.count(OLD)
    if n == 0:
        raise RuntimeError(
            "anchor NOT FOUND. A sibling agent likely modified the final "
            "block of the first <style> section. Widen the anchor and retry."
        )
    if n > 1:
        raise RuntimeError(
            f"anchor matched {n} times; needs to be unique. Widen the anchor."
        )

    BACKUP.write_text(src, encoding="utf-8")
    print(f"[uppercase] backup written to {BACKUP}")

    new_src = src.replace(OLD, NEW)

    # Sanity: OLD gone, NEW present exactly once.
    if new_src.count(OLD) != 0:
        raise RuntimeError("old anchor still present after replace")
    if new_src.count(NEW) != 1:
        raise RuntimeError("new block not inserted exactly once")

    INDEX.write_text(new_src, encoding="utf-8")
    new_bytes = len(new_src.encode("utf-8"))
    delta = new_bytes - orig_bytes
    print(f"[uppercase] wrote {new_bytes:,} bytes ({delta:+,} bytes vs original)")
    print("[uppercase] run: python3 scripts/validate_index_html.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
