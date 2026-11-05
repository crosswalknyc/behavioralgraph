#!/usr/bin/env python3
"""Regression: staff archive-cover picks survive every auto pass.

2026-11-04 defect: lock_cover_image(by="auto") kept a staff pick's name
but relabeled it "auto", so the NEXT auto pass no longer recognized it
as a staff pick and re-picked the top-scored candidate. The scorer
prefers tall text cards over photos, so hand-picked covers on the
public archive were silently replaced (Digital Twins showed the
playbook text card instead of the voters photo Jenna selected).

Checks:
1. An auto pass over a staff-locked cover keeps the name AND the label.
2. Ten consecutive auto passes still keep it.
3. A staff pick is not re-scored at render: _public_cover_image returns
   it even when the scorer would reject it.
4. A staff pick whose asset is gone falls back to auto picking.
5. An explicit new staff pick still wins, and clearing to auto re-picks.

Run: python3 scripts/test_newsletter_cover_lock.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fake_mod = types.ModuleType("s3_json_state")
_fake_mod.update_json = lambda *a, **k: None
sys.modules["s3_json_state"] = _fake_mod

import newsletter as nl  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}")


CID = "nl-covertest"
_state = {"campaigns": [{
    "id": CID, "name": "Cover Test", "status": "sent",
    "cover_image": "staff_photo.jpg", "cover_locked_by": "staff",
}]}
_assets = {nl.ASSET_KEY.format(cid=CID, name="staff_photo.jpg"): b"jpgbytes"}

nl.load_state_raw = lambda: _state
nl._get_bytes = lambda key: _assets.get(key)
nl._best_cover_name = lambda cid, camp=None: "autopick_textcard.png"


def _fake_cas(mutate):
    nxt = mutate(_state)
    return _state if nxt is not None else None


nl._cas_update_state = _fake_cas
camp = _state["campaigns"][0]

print("1. auto pass keeps a staff pick, name and label")
chosen = nl.lock_cover_image(CID)  # by="auto" default, the scheduler's call
check("name kept", chosen == "staff_photo.jpg" and camp["cover_image"] == "staff_photo.jpg")
check("label stays staff", camp["cover_locked_by"] == "staff")

print("2. ten auto passes change nothing")
for _ in range(10):
    nl.lock_cover_image(CID)
check("still the staff pick", camp["cover_image"] == "staff_photo.jpg")
check("still labeled staff", camp["cover_locked_by"] == "staff")

print("3. render never re-scores a staff pick")
nl._cover_src_ok = lambda *a, **k: False  # scorer would reject everything
url = nl._public_cover_image(CID, camp)
check("staff pick returned despite scorer", "staff_photo.jpg" in (url or ""))

print("4. a staff pick whose asset is gone falls back to auto")
_assets.clear()
chosen = nl.lock_cover_image(CID)
check("auto re-pick on missing asset", chosen == "autopick_textcard.png")
check("label flips to auto", camp["cover_locked_by"] == "auto")

print("5. explicit staff pick wins; clearing re-picks")
_assets[nl.ASSET_KEY.format(cid=CID, name="new_pick.jpg")] = b"jpg"
chosen = nl.lock_cover_image(CID, "new_pick.jpg", by="staff")
check("new staff pick lands", chosen == "new_pick.jpg" and camp["cover_locked_by"] == "staff")
camp["cover_image"] = ""
camp["cover_locked_by"] = ""
chosen = nl.lock_cover_image(CID, by="auto")
check("cleared cover auto-picks", chosen == "autopick_textcard.png")

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
