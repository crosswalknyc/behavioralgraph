#!/usr/bin/env python3
"""Decks never use the word doors (Jenna 2026-10-28).

  [1] the door to new viewers is flagged and rewritten
  [2] doors in a header fails the lint / readability check
  [3] a proper rewrite has no door token
  [4] Prometheus deck prompts and the skill text carry the ban

Run: python3 scripts/test_no_doors_in_decks.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WEBAPP = Path(os.environ.get("PM_TEST_WEBAPP") or (REPO / "bg-webapp"))
if not (WEBAPP / "app.py").is_file():
    for cand in (Path("/tmp/bgw-dtiq-nokind"), REPO / "bg-webapp"):
        if (cand / "app.py").is_file():
            WEBAPP = cand
            break

FAILS: list[str] = []


def check(cond, msg, detail=""):
    print(("  ok   " if cond else "  FAIL ") + msg + (
        f"  ({str(detail)[:220]})" if (detail and not cond) else ""))
    if not cond:
        FAILS.append(msg)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _find_helper() -> Path:
    cands = [
        REPO / ".cursor" / "skills" / "crosswalk-design" / "scripts" / "cw_door_copy.py",
        WEBAPP / "design" / "crosswalk-design" / "scripts" / "cw_door_copy.py",
        Path("/tmp/fc-dtiq-nokind/.cursor/skills/crosswalk-design/scripts/cw_door_copy.py"),
    ]
    for p in cands:
        if p.is_file():
            return p
    raise SystemExit("cw_door_copy.py not found")


HELPER = _find_helper()
door = _load(HELPER, "cw_door_copy")
print(f"[helper] {HELPER}")

print("[1] rewrite")
raw = "01 The door to new viewers."
out = door.scrub_door_metaphor(raw)
check("door" not in out.lower(), "the door to new viewers loses the token", out)
check("new viewers" in out.lower(), "the claim remains", out)
check(door.has_door_metaphor(raw), "the source is flagged")
check(not door.has_door_metaphor(out), "the rewrite is clean", out)

opens = door.scrub_door_metaphor("She opens the door to new viewers.")
check("door" not in opens.lower() and "brings" in opens.lower(),
      "opens the door becomes brings", opens)

a_door = door.scrub_door_metaphor("This is a door for growth.")
check("door" not in a_door.lower(), "a door for is dropped", a_door)

kept = door.scrub_door_metaphor("Meet at The Door NYC after the show.")
check("The Door NYC" in kept, "The Door NYC as a name is kept", kept)
check(not door.has_door_metaphor(kept), "the venue name is not flagged", kept)

dash = door.scrub_door_metaphor("They order DoorDash at the desk.")
check("DoorDash" in dash and not door.has_door_metaphor(dash),
      "DoorDash is not the metaphor", dash)

print("[2] readability and lint flag doors")
read_py = HELPER.with_name("readability_check.py")
rr = subprocess.run(
    [sys.executable, str(read_py), "--text", "the door to new viewers"],
    capture_output=True, text=True)
check(rr.returncode == 1 and "door metaphor" in rr.stdout,
      "readability flags a door header", rr.stdout[-240:])
clean = subprocess.run(
    [sys.executable, str(read_py), "--text", "She brought the new viewers."],
    capture_output=True, text=True)
check(clean.returncode == 0 or "door metaphor" not in clean.stdout,
      "a clean header does not flag door", clean.stdout[-200:])

lint_py = HELPER.with_name("cw_brand_lint.py")
if lint_py.is_file():
    try:
        lint = _load(lint_py, "cw_brand_lint")
    except Exception as e:
        print(f"  (lint module not loadable here: {e.__class__.__name__})")
        lint = None
    if lint is not None:
        rep = lint.Report("probe")
        lint.check_copy("Doors for new viewers.", "title", rep)
        check(any("door metaphor" in m for _, m in rep.fails),
              "lint FAILs on doors in a header", rep.fails)
        rep2 = lint.Report("clean")
        lint.check_copy("She brought the new viewers.", "title", rep2)
        check(not any("door" in m.lower() for _, m in rep2.fails),
              "lint is clean on a rewrite")
else:
    print("  (cw_brand_lint.py not beside the helper)")

print("[3] design_skill wrapper")
sys.path.insert(0, str(WEBAPP))
sys.path.insert(0, str(REPO))
try:
    from migration import design_skill as ds
    wrapped = ds.scrub_door_metaphor("the door to new viewers")
    check("door" not in wrapped.lower(), "design_skill.scrub_door_metaphor", wrapped)
except Exception as e:
    check(False, "design_skill import", e)

print("[4] prompts and skill text")
ban = "never use the word door"
hits = []
for p in (
    WEBAPP / "prometheus_analysis.py",
    WEBAPP / "migration" / "profile_iq_deck_builder.py",
    REPO / ".cursor" / "skills" / "crosswalk-design" / "SKILL.md",
    REPO / ".cursor" / "skills" / "crosswalk-plain-english" / "SKILL.md",
    REPO / ".cursor" / "rules" / "no-doors-in-decks.mdc",
    Path("/tmp/fc-dtiq-nokind/.cursor/rules/no-doors-in-decks.mdc"),
    Path("/tmp/fc-dtiq-nokind/.cursor/skills/crosswalk-design/SKILL.md"),
):
    if not p.is_file():
        continue
    src = p.read_text(encoding="utf-8", errors="replace").lower()
    if "never use the word door" in src or "never the word door" in src or "decks never use the word door" in src:
        hits.append(str(p))
        print(f"  ok   ban in {p.name}")
    else:
        check(False, f"ban missing in {p}")
check(len(hits) >= 2, "at least two surfaces carry the ban", hits)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for m in FAILS:
        print(" -", m)
    sys.exit(1)
print("ALL PASS")
