#!/usr/bin/env python3
"""Round Marketing tab audience-age display to 2 decimal places.

User directive 2026-08-25 (Jenna, screenshot of TikTok Distribution
card showing "age 22.661391460137352-40.66139146013735"):
> "round the decimals only two places"

Bug: two Marketing-tab renderers build the audience age line as
    `age ${Math.max(18, median-8)}-${median+10}`
where `median` is a float from `_mkMedianAge()`. JS float subtraction
prints the raw 15-digit value into the DOM. Fix: wrap each end of the
range in `.toFixed(2)` so the string reads e.g. `age 22.66-40.66`.

Same pattern at exactly two spots in templates/index.html:
  - line ~108070 (_mkRenderPlatformGrid: TikTok / Meta / etc. cards)
  - line ~108427 (creator-overlap card that shares the same audience
                  line format)

Both spots are byte-identical so a single str.replace with a
count-guard of exactly 2 covers both. If a future edit produces a
third occurrence, the guard fires and this script aborts before
writing so nothing gets clobbered.

Byte-safe: uses str.replace with a hard count check per
index-html-safety.mdc; never StrReplace / Write on the whole file.
"""

from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[1]  # bg-webapp/
INDEX = REPO / "templates" / "index.html"

STAMP = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
BACKUP = Path("/tmp") / f"index.pre_age_round_{STAMP}.html"

OLD = "if (median) audienceBits.push(`age ${Math.max(18, median-8)}-${median+10}`);"
NEW = "if (median) audienceBits.push(`age ${Math.max(18, median-8).toFixed(2)}-${(median+10).toFixed(2)}`);"

# Guard: this exact line should exist EXACTLY 2 times today. If a
# sibling agent added a third instance we abort so the operator can
# review; if it dropped to 1 or 0 we abort so we don't silently
# rewrite the wrong pattern.
EXPECTED_COUNT = 2


def main() -> int:
    if not INDEX.is_file():
        print(f"[age-round] {INDEX} not found")
        return 2

    src = INDEX.read_text(encoding="utf-8")
    orig_bytes = len(src.encode("utf-8"))
    n = src.count(OLD)
    print(f"[age-round] {INDEX} ({orig_bytes:,} bytes, "
          f"{n} occurrence(s) of the un-rounded pattern)")

    if n != EXPECTED_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_COUNT} occurrences, found {n}. "
            f"A sibling change likely modified the pattern. Not writing."
        )

    BACKUP.write_text(src, encoding="utf-8")
    print(f"[age-round] backup written to {BACKUP}")

    src = src.replace(OLD, NEW)

    # Sanity: OLD should be gone, NEW should be present exactly
    # EXPECTED_COUNT times.
    if src.count(OLD) != 0:
        raise RuntimeError("un-rounded pattern still present after replace")
    if src.count(NEW) != EXPECTED_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_COUNT} rounded occurrences, "
            f"got {src.count(NEW)}"
        )

    INDEX.write_text(src, encoding="utf-8")
    new_bytes = len(src.encode("utf-8"))
    delta = new_bytes - orig_bytes
    print(f"[age-round] wrote {new_bytes:,} bytes "
          f"({delta:+,} bytes vs original)")
    print("[age-round] run: python3 scripts/validate_index_html.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
