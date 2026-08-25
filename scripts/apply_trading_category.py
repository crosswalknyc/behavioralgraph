#!/usr/bin/env python3
"""Add TRADING as a new canonical BRAND CATEGORY for Profile IQ.

User directive 2026-08-25 (Jenna):
> "add trading as a new behavioral category for profile iq and have it
>  appear in this dropdown"

Where TRADING lives:
  - Master bucket: BRAND (alongside BANKING, DIGITAL BANKING, CREDIT
    PROVIDER, INVESTMENTS, INSURANCE - the finance family).
  - Alphabetical placement in the BRAND list: between TOY and TRAVEL.
  - Emoji: 💹 (chart with yen, reads as active trading / market
    movement, distinct from INVESTMENTS's 📈 which reads as long-term
    investing).

Changes to templates/index.html (all byte-safe splices per
index-html-safety.mdc - never StrReplace on this file):

  Splice A: MASTER_CATEGORIES.BRAND gains 'TRADING' between 'TOY' and
            'TRAVEL' (single-line array on line ~136437)
  Splice B: tooltipCategories gains 'TRADING' description after
            'INVESTMENTS' entry (line ~121612)
  Splice C: behavioralCategoryIcons #1 gains 'TRADING': '💹' between
            'INVESTMENTS' and 'INSURANCE' (line ~121676)
  Splice D: icon map #2 (Finance & Banking block) gains 'TRADING': '💹'
            between 'INVESTMENTS' and 'INSURANCE' (line ~122293)

Every splice uses a unique anchor with a count-guard. If any anchor
lookup returns != 1 the script aborts BEFORE writing, so a partial
apply cannot corrupt the file.
"""

from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[1]  # bg-webapp/
INDEX = REPO / "templates" / "index.html"

STAMP = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
BACKUP = Path("/tmp") / f"index.pre_trading_category_{STAMP}.html"


def splice(src: str, old: str, new: str, desc: str) -> str:
    """Replace exactly one occurrence of `old` with `new`; abort on any other count."""
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND in {INDEX}")
    if count > 1:
        raise RuntimeError(
            f"[{desc}] anchor found {count}x, needs to be unique. "
            f"Widen the anchor with more surrounding context."
        )
    return src.replace(old, new)


# --- Splice A: MASTER_CATEGORIES.BRAND (line ~136437, single-line array) ---
# Add 'TRADING' alphabetically between 'TOY' and 'TRAVEL'.
A_OLD = "'TICKETING', 'TOY', 'TRAVEL', 'VENUE'"
A_NEW = "'TICKETING', 'TOY', 'TRADING', 'TRAVEL', 'VENUE'"


# --- Splice B: tooltipCategories (line ~121612) --------------------------
# Add TRADING description after INVESTMENTS. Both are finance so they
# sit next to each other; the map is alphabetical near INVESTMENTS.
B_OLD = (
    "                'INVESTMENTS': 'Investment companies panelists digitally engaged with.',\n"
    "                'MOVIE THEATER': 'Movie theaters panelists digitally engaged with.',"
)
B_NEW = (
    "                'INVESTMENTS': 'Investment companies panelists digitally engaged with.',\n"
    "                'TRADING': 'Trading platforms panelists digitally engaged with.',\n"
    "                'MOVIE THEATER': 'Movie theaters panelists digitally engaged with.',"
)


# --- Splice C: behavioralCategoryIcons #1 (line ~121676) -----------------
# The FIRST icon map. Anchor uses AUTOMOTIVE+INVESTMENTS+INSURANCE to
# disambiguate from the second icon map further down the file.
C_OLD = (
    "                'AUTOMOTIVE': '🚗',\n"
    "                'INVESTMENTS': '📈',\n"
    "                'INSURANCE': '🛡️',"
)
C_NEW = (
    "                'AUTOMOTIVE': '🚗',\n"
    "                'INVESTMENTS': '📈',\n"
    "                'TRADING': '💹',\n"
    "                'INSURANCE': '🛡️',"
)


# --- Splice D: icon map #2 Finance & Banking block (line ~122293) --------
# The SECOND icon map, sectioned by domain with comments. Anchor uses
# CREDIT PROVIDER+INVESTMENTS+INSURANCE to disambiguate.
D_OLD = (
    "                'CREDIT PROVIDER': '💳',\n"
    "                'INVESTMENTS': '📈',\n"
    "                'INSURANCE': '🛡️',"
)
D_NEW = (
    "                'CREDIT PROVIDER': '💳',\n"
    "                'INVESTMENTS': '📈',\n"
    "                'TRADING': '💹',\n"
    "                'INSURANCE': '🛡️',"
)


def main() -> int:
    if not INDEX.is_file():
        print(f"[trading] {INDEX} not found")
        return 2

    src = INDEX.read_text(encoding="utf-8")
    orig_bytes = len(src.encode("utf-8"))
    orig_trading_count = src.count("TRADING")
    print(f"[trading] {INDEX} ({orig_bytes:,} bytes, "
          f"{orig_trading_count} pre-existing TRADING refs)")

    # Backup BEFORE any change.
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[trading] backup written to {BACKUP}")

    # Apply all four splices. If any anchor lookup fails we abort
    # without writing, so the file stays intact.
    src = splice(src, A_OLD, A_NEW, "A: MASTER_CATEGORIES.BRAND")
    print("[trading]   splice A applied (MASTER_CATEGORIES.BRAND)")
    src = splice(src, B_OLD, B_NEW, "B: tooltipCategories")
    print("[trading]   splice B applied (tooltipCategories)")
    src = splice(src, C_OLD, C_NEW, "C: behavioralCategoryIcons #1")
    print("[trading]   splice C applied (behavioralCategoryIcons #1)")
    src = splice(src, D_OLD, D_NEW, "D: Finance & Banking icon block")
    print("[trading]   splice D applied (Finance & Banking icon block)")

    # Sanity: TRADING should now appear exactly 4 MORE times than before
    # (once per splice). Guard against a partial apply or an unintended
    # collision with an earlier occurrence.
    new_trading_count = src.count("TRADING")
    delta = new_trading_count - orig_trading_count
    if delta != 4:
        raise RuntimeError(
            f"expected 4 new TRADING occurrences, got {delta}. "
            f"Not writing (backup preserved at {BACKUP})."
        )

    INDEX.write_text(src, encoding="utf-8")
    new_bytes = len(src.encode("utf-8"))
    byte_delta = new_bytes - orig_bytes
    print(f"[trading] wrote {new_bytes:,} bytes ({byte_delta:+,} bytes vs original)")
    print("[trading] TRADING refs: "
          f"{orig_trading_count} -> {new_trading_count} (+{delta})")
    print("[trading] run: python3 scripts/validate_index_html.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
