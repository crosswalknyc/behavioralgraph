#!/usr/bin/env python3
"""Copy the parts of the crosswalk-design skill that code needs into
bg-webapp/design/crosswalk-design (the packaged copy Render runs on).

The full skill lives in the parent repo (.cursor/skills/crosswalk-design).
Render deploys bg-webapp alone, so the fonts, the logo, the tokens, the
font metrics, the deck / SOW / POC templates and the finishing scripts
ride inside bg-webapp too. This script copies them; test_design_skill.py
holds the two byte-identical so they never drift.

Usage:
    python3 scripts/sync_design_pack.py            # copy from the skill
    python3 scripts/sync_design_pack.py --check    # exit 1 when anything differs
    python3 scripts/sync_design_pack.py --skill DIR
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parent
sys.path.insert(0, str(WEBAPP))
from migration import design_skill as ds  # noqa: E402

COPY_DIRS = (
    ("assets/brand-files/fonts", None),
    ("assets/brand-files/logo", None),
    ("assets/brand-files/shapes", None),
)
COPY_FILES = tuple(f"assets/{f}" for f in ds.PACKAGED_ASSET_FILES) + \
    tuple(f"assets/templates/{t}" for t in ds.PACKAGED_TEMPLATES) + \
    tuple(f"scripts/{s}" for s in ds.PACKAGED_SCRIPTS) + ("assets/brand-files/README.md",)


def pairs(skill: Path, dst: Path):
    for rel, _ in COPY_DIRS:
        src_d = skill / rel
        if not src_d.is_dir():
            continue
        for p in sorted(src_d.iterdir()):
            if p.is_file():
                yield p, dst / rel / p.name
    for rel in COPY_FILES:
        p = skill / rel
        if p.is_file():
            yield p, dst / rel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", help="the full skill directory (default: the repo's)")
    ap.add_argument("--check", action="store_true", help="report differences, copy nothing")
    a = ap.parse_args()
    skill = Path(a.skill).expanduser() if a.skill else ds.full_skill_root()
    if skill is None or not (skill / "SKILL.md").is_file():
        print("full crosswalk-design skill not found; pass --skill DIR")
        return 2
    dst = ds.PACKAGED
    differing, copied = [], 0
    for src, out in pairs(skill, dst):
        same = out.is_file() and filecmp.cmp(src, out, shallow=False)
        if same:
            continue
        differing.append(str(out.relative_to(dst)))
        if not a.check:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            copied += 1
    # the packaged copy carries a one-line pointer instead of SKILL.md
    note = dst / "README.md"
    text = ("Packaged copy of the crosswalk-design skill for the deployed app (fonts, logo, tokens, metrics, "
            "the deck / SOW / POC templates and the finishing scripts). The full skill, with SKILL.md and every "
            "reference and template, is .cursor/skills/crosswalk-design in the parent repo. Do not edit here: "
            "run scripts/sync_design_pack.py after the skill changes.\n")
    if not note.is_file() or note.read_text(encoding="utf-8") != text:
        differing.append("README.md")
        if not a.check:
            dst.mkdir(parents=True, exist_ok=True)
            note.write_text(text, encoding="utf-8")
    if a.check:
        if differing:
            print("packaged design copy differs from the skill:\n  " + "\n  ".join(differing))
            return 1
        print("packaged design copy matches the skill")
        return 0
    print(f"synced {copied} file(s) into {dst}" + (f": {', '.join(differing[:8])}{' ...' if len(differing) > 8 else ''}" if differing else " (already current)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
