#!/usr/bin/env python3
"""Both generation prompts: never decline, never ask to rephrase.

2026-09-28: the model authored "I could not lock the numbers on that
one down cleanly, and I would rather re-aim than guess" for an
answerable ask (top titles inside a genre cohort). The read must
always be produced from the anchors.
"""
import sys
from pathlib import Path

PMA = Path(sys.argv[1] if len(sys.argv) > 1 else "prometheus_analysis.py")
src = PMA.read_text(encoding="utf-8")

ANCHOR = ("- MULTIPLE QUESTIONS IN ONE ASK: answer every one, each under "
          "its own short plain heading, in the order asked. Never answer "
          "only the first and stop.")
RULE = ("\n- NEVER DECLINE: you never ask the reader to rephrase, narrow, "
        "re-aim, or pick a different question, and you never say a number "
        "cannot be locked down. When the screen tables do not carry the "
        "exact split asked for, derive it from the audience measures you "
        "do have and state the read plainly.")

n = src.count(ANCHOR)
if n != 2:
    raise RuntimeError(f"anchor found {n}x (expected 2: both prompts)")
src = src.replace(ANCHOR, ANCHOR + RULE)
PMA.write_text(src, encoding="utf-8")
print("never-decline rule added to both prompts")
