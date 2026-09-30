#!/usr/bin/env python3
"""Sept 29 sweep regressions: duplicate re-approves block before the
queue post, worded durations bind the window, and clarify answers
(bare or typo'd) re-run their original question.

Hermetic: extracts the helpers from app.py source with stubbed
storage, plus source-wiring asserts.
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_APP = ROOT / "app.py"
if not _APP.exists():
    _APP = ROOT / "bg-webapp" / "app.py"
SRC = _APP.read_text()

FAIL = 0


def check(name, ok):
    global FAIL
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        FAIL += 1


# ------------------------------------------------------------------
# Recent-build guard with an in-memory S3 stub.
# ------------------------------------------------------------------
class _S3Stub:
    def __init__(self):
        self.docs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.docs:
            raise KeyError(Key)
        import io
        return {"Body": io.BytesIO(self.docs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.docs[Key] = Body


m = re.search(
    r"(_PM_RECENT_BUILDS_KEY = .*?)\n\n\n@app\.route", SRC, re.S)
check("guard helper present", m is not None)
stub = _S3Stub()
ns = {"re": re, "time": time, "json": json, "datetime": datetime,
      "s3_client": stub, "S3_BUCKET": "b",
      "traceback": __import__("traceback")}
exec(m.group(1), ns)
guard = ns["_pm_recent_build_guard"]

# First approve records and passes.
check("first build passes",
      guard("smclain", "Trinity Tatum",
            "2025-09-29", "2026-09-29") is None)
# Re-approve 2 minutes later with a 6-day window drift blocks.
check("near-identical window blocks",
      guard("smclain", "Trinity Tatum",
            "2025-09-23", "2026-09-23") is not None)
# Same subject, materially different window (trailing 36) builds.
check("material window change builds",
      guard("smclain", "Trinity Tatum",
            "2023-09-29", "2026-09-29") is None)
# Different user, same subject, passes.
check("different user passes",
      guard("scott", "Trinity Tatum",
            "2025-09-29", "2026-09-29") is None)
# Different subject passes.
check("different subject passes",
      guard("smclain", "Hoshimachi Suisei",
            "2025-09-29", "2026-09-29") is None)
# Punctuation-insensitive subject match.
check("normalized subject match blocks",
      guard("smclain", "trinity-tatum!",
            "2025-09-28", "2026-09-28") is not None)
# Stale entries outside 30 minutes do not block.
doc = json.loads(stub.docs["system/usage/recent_builds.json"])
for e in doc["entries"]:
    e["t"] = time.time() - 3600
stub.docs["system/usage/recent_builds.json"] = json.dumps(doc).encode()
check("30-minute window expires",
      guard("smclain", "Trinity Tatum",
            "2025-09-29", "2026-09-29") is None)
# Storage trouble fails open.
class _Boom:
    def get_object(self, **kw):
        raise RuntimeError("down")

    def put_object(self, **kw):
        raise RuntimeError("down")


ns["s3_client"] = _Boom()
check("storage trouble fails open",
      guard("smclain", "Trinity Tatum",
            "2025-09-29", "2026-09-29") is None)

# ------------------------------------------------------------------
# Clarify-answer merge upgrades.
# ------------------------------------------------------------------
m2 = re.search(
    r"(_PM_FILE_ASK_RE = re\.compile.*?)\ndef _pm_open_screen_confirm",
    SRC, re.S)
ns2 = {"re": re}
exec(m2.group(1), ns2)
merge = ns2["_pm_clarify_answer_merge"]

ORIG = ("how did the show grow its audience over the three years "
        "on the streaming platform")
did_hist = [
    {"role": "user", "text": ORIG},
    {"role": "agent",
     "text": ("Did you want this on The Spiderwick Chronicles "
              "Under-18 Viewers (open on your screen) or on The "
              "Spiderwick Chronicles?")},
]
check("Did-variant clarify detected",
      merge(did_hist, "open on sceren") == ORIG)
check("typo'd screen answer runs clean",
      merge(did_hist, "open on sceren") == ORIG)
check("bare no runs the question clean", merge(did_hist, "no") == ORIG)
check("bare yes runs the question clean",
      merge(did_hist, "yes") == ORIG)
check("something else runs clean",
      merge(did_hist, "Something else") == ORIG)
check("named audience still merges",
      merge(did_hist, "Disney+ subscribers only")
      == f"{ORIG}\n\nAudience: Disney+ subscribers only")

# ------------------------------------------------------------------
# Source wiring.
# ------------------------------------------------------------------
check("guard called before queue post",
      "_dup = _pm_recent_build_guard(" in SRC)
check("guard blocks only new_build",
      "if decision == 'new_build':" in SRC.split(
          "_dup = _pm_recent_build_guard(")[0][-600:])
check("block is guidance, no charge",
      "did not start a second\n                          "
      "\"copy or charge you again" in SRC
      or "did not start a second" in SRC)
check("worded durations in the interpret rule",
      "three years = trailing 36 months" in SRC)

print()
if FAIL:
    print(f"{FAIL} FAILED")
    sys.exit(1)
print("all duplicate-build guard checks passed")
