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
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# This suite ships in both repos with the same bytes. Run from the parent,
# REPO is the parent and the web app sits under it. Run from bg-webapp (a
# bare worktree, the deploy, the build server, or the parent's bg-webapp
# symlink, which resolve() follows), REPO is the web app itself.
if os.environ.get("PM_TEST_WEBAPP"):
    WEBAPP = Path(os.environ["PM_TEST_WEBAPP"])
elif (REPO / "app.py").is_file():
    WEBAPP = REPO
else:
    WEBAPP = REPO / "bg-webapp"

FAILS: list[str] = []


def check(cond, msg, detail=""):
    print(("  ok   " if cond else "  FAIL ") + msg + (
        f"  ({str(detail)[:220]})" if (detail and not cond) else ""))
    if not cond:
        FAILS.append(msg)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod             # a module with dataclasses looks itself up by name
    spec.loader.exec_module(mod)
    return mod


def _find_helper() -> Path:
    """The door helper, wherever this suite runs: the pack the builders
    themselves resolve (migration.design_skill: the full skill when the
    parent checkout is present, else the packaged copy the web app
    deploys with), then the two known homes by path."""
    cands = []
    for base in (WEBAPP, REPO):
        ds_py = base / "migration" / "design_skill.py"
        if not ds_py.is_file():
            continue
        try:
            sd = _load(ds_py, f"design_skill_probe_{len(cands)}").scripts_dir()
        except Exception:
            sd = None
        if sd is not None:
            cands.append(Path(sd) / "cw_door_copy.py")
    cands += [
        REPO / ".cursor" / "skills" / "crosswalk-design" / "scripts" / "cw_door_copy.py",
        WEBAPP.parent / ".cursor" / "skills" / "crosswalk-design" / "scripts" / "cw_door_copy.py",
        WEBAPP / "design" / "crosswalk-design" / "scripts" / "cw_door_copy.py",
    ]
    for p in cands:
        if p.is_file():
            return p
    raise SystemExit("cw_door_copy.py not found (looked in: " + ", ".join(str(c) for c in cands) + ")")


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

print("[1b] the literal senses are said plainly (Jenna 2026-11-11: a deck read 'the widest front store')")
# the word is banned in a deck whatever it means, so the rewrite has to be correct English for what it meant
LITERAL = [
    # the way in to a place
    ("The profile's widest front door is Walmart.", "The profile's widest entrance is Walmart."),
    ("Shoppers walk in the front door of the store and buy within the hour.",
     "Shoppers walk in the entrance of the store and buy within the hour."),
    ("Front-door placement drives trial.", "Entrance placement drives trial."),
    ("Back door traffic", "Back entrance traffic"),
    ("The line at the door was long.", "The line at the entrance was long."),
    ("Next door neighbors", "Nearby neighbors"),
    ("A door-to-door sales push", "A house-to-house sales push"),
    ("door-buster deals", "limited-time deals"),
    # the places a brand is sold
    ("Target leads on door count.", "Target leads on store count."),
    ("Door count rose to 1,912 doors across the Midwest.", "Store count rose to 1,912 stores across the Midwest."),
    ("DOOR COUNT", "STORE COUNT"),
    ("The brand is in 4,213 doors nationwide.", "The brand is in 4,213 stores nationwide."),
    ("Sold in 312 retail doors.", "Sold in 312 retail stores."),
    ("the brand added 37 doors in Q3", "the brand added 37 stores in Q3"),
    ("sold in more doors than any rival", "sold in more stores than any rival"),
    ("half of its doors are in Texas", "half of its stores are in Texas"),
    ("per-door sales rose 12.4%", "per-store sales rose 12.4%"),
    ("same-door sales", "same-store sales"),
    ("a three-door audience", "a three-store audience"),
    ("Retail Door Mix", "Retail Store Mix"),
    ("Doors: 4,213", "Stores: 4,213"),
    ("Doors", "Stores"),
    ("Number of doors", "Number of stores"),
    ("Doors grew 12.4% year over year.", "Stores grew 12.4% year over year."),
    # the figures of speech keep their tense and their meaning
    ("Her posts opened the door to new viewers.", "Her posts brought new viewers."),
    ("Her post opened doors for the brand.", "Her post made room for the brand."),
    ("It opens doors.", "It makes room."),
    ("It gives the brand a door into the category.", "It gives the brand a way into the category."),
    ("They get a foot in the door with Gen Z.", "They get a way in with Gen Z."),
    ("Deals are made behind closed doors.", "Deals are made in private."),
    ("It leaves the door open for a sequel.", "It leaves room for a sequel."),
    ("The network closed the door on a third season.", "The network ruled out a third season."),
]
for src, want in LITERAL:
    got = door.scrub_door_metaphor(src)
    check(got == want, f"{src!r} reads {want!r}", got)
    check(not door.has_door_metaphor(got), f"no door left in {want!r}", got)
# never the old leftovers: a word cut out of the middle of a phrase, or "store" for an entrance
for src in ("the widest front door", "Next door neighbors", "A door-to-door sales push", "Doors: 4,213", "Target leads on door count."):
    got = door.scrub_door_metaphor(src)
    check("front store" not in got and "-to-" not in got.replace("house-to-house", "") and not got.startswith(":")
          and "  " not in got and not re.search(r"\b(?:on|next) (?:count|neighbors)\b", got.lower().replace("store count", "")),
          f"{src!r} is whole English", got)
# a name is still held aside, byte for byte, beside a literal sense on the same line
for src, want in (("AD Open Door reaches 4,213 doors.", "AD Open Door reaches 4,213 stores."),
                  ("The Doors index high at the front door.", "The Doors index high at the entrance."),
                  ("Three Doors Down fans shop in more doors.", "Three Doors Down fans shop in more stores."),
                  ("Door County tourism", "Door County tourism"),
                  ("Meet at The Door NYC, by the front door.", "Meet at The Door NYC, by the entrance.")):
    got = door.scrub_door_metaphor(src)
    check(got == want, f"a name stays beside a literal sense: {want!r}", got)

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
seen: set[str] = set()
for p in (
    WEBAPP / "prometheus_analysis.py",
    WEBAPP / "migration" / "profile_iq_deck_builder.py",
    REPO / ".cursor" / "skills" / "crosswalk-design" / "SKILL.md",
    REPO / ".cursor" / "skills" / "crosswalk-plain-english" / "SKILL.md",
    REPO / ".cursor" / "rules" / "no-doors-in-decks.mdc",
    WEBAPP.parent / ".cursor" / "skills" / "crosswalk-design" / "SKILL.md",
    WEBAPP.parent / ".cursor" / "rules" / "no-doors-in-decks.mdc",
):
    if not p.is_file() or str(p.resolve()) in seen:
        continue
    seen.add(str(p.resolve()))
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
