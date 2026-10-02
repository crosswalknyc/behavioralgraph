#!/usr/bin/env python3
"""Casey Pearson 2026-09-29 regression: a which-audience clarify
answer merges back into the question that triggered it, and an ask
that requests a file gets its CSV attached inline.

Hermetic: extracts the helpers from app.py source and exercises them
directly, plus source-wiring asserts on the callsite and the
generated-read tail.
"""
import re
import sys
from pathlib import Path
# Legacy-move shim (2026-10-01): the Prometheus code lives in
# bg-webapp/prometheus/legacy/chat.py; read app.py + legacy as one source.
import os as _pm_os, sys as _pm_sys
_pm_r = _pm_os.path.dirname(_pm_os.path.abspath(__file__))
while not _pm_os.path.exists(_pm_os.path.join(_pm_r, 'bg-webapp', 'app.py')):
    _pm_r = _pm_os.path.dirname(_pm_r)
_pm_sys.path.insert(0, _pm_os.path.join(_pm_r, 'scripts'))
from _pm_test_source import app_path as _pm_app_path, host_for as _pm_host_for  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
_APP = _pm_app_path()
if not _APP.exists():
    _APP = _pm_app_path()
SRC = _APP.read_text()

FAIL = 0


def check(name, ok):
    global FAIL
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        FAIL += 1


# ------------------------------------------------------------------
# Extract the helper block and exec it.
# ------------------------------------------------------------------
m = re.search(
    r"(_PM_FILE_ASK_RE = re\.compile.*?)\ndef _pm_open_screen_confirm",
    SRC, re.S)
check("helper block present", m is not None)
ns = {"re": re}
_pm_host_for(ns)
exec(m.group(1), ns)
merge = ns["_pm_clarify_answer_merge"]
file_rx = ns["_PM_FILE_ASK_RE"]

CASEY_ASK = (
    "Provide total hours spent on streaming services in the past 6 "
    "months, broken out by content genre and the platform the "
    "subscriber used. Provide two cuts of this data, the total "
    "universe and a view filtered to just Paramount+ subscribers. "
    "Provide output as a csv")
CASEY_ANSWER = (
    "I want for two audiences: Total universe of subscribers active "
    "on streaming platforms and Paramount+ subscribers only")

# Casey's exact sequence: ask -> confirm -> Something else ->
# which-audience -> her typed answer.
hist = [
    {"role": "user", "text": CASEY_ASK},
    {"role": "agent",
     "text": "Do you want this on Paramount+ (open on your screen)?"},
    {"role": "user", "text": "Something else"},
    {"role": "agent",
     "text": ("No problem. Which audience should I use? Name it "
              "here, or open a profile from Select Profile and ask "
              "again.")},
]
merged = merge(hist, CASEY_ANSWER)
check("casey sequence merges", bool(merged))
check("merge keeps the original ask", CASEY_ASK in merged)
check("merge carries the answer",
      merged.endswith(f"Audience: {CASEY_ANSWER}"))
check("chip echo skipped as original",
      "Something else" not in merged.split("\n\nAudience:")[0][-40:])

# Direct confirm (no Something else hop) also merges.
hist2 = [
    {"role": "user", "text": CASEY_ASK},
    {"role": "agent",
     "text": "Do you want this on Paramount+ (open on your screen)?"},
]
check("direct confirm answer merges",
      CASEY_ASK in (merge(hist2, "Disney+ subscribers only") or ""))

# Guards: not after a normal answer, not for a fresh question, not
# for long text, not with empty history.
hist3 = [
    {"role": "user", "text": CASEY_ASK},
    {"role": "agent", "text": "Here is the read. 41.9% of hours."},
]
check("no merge after a normal reply", merge(hist3, CASEY_ANSWER) == "")
check("question mark means a new ask",
      merge(hist, "Actually what is churn for Netflix?") == "")
check("long text means a new ask", merge(hist, "x" * 260) == "")
check("empty history is safe", merge([], CASEY_ANSWER) == "")
check("assistant role also detected",
      CASEY_ASK in (merge(
          [{"role": "user", "text": CASEY_ASK},
           {"role": "assistant",
            "text": "Which audience should I use?"}],
          CASEY_ANSWER) or ""))

# ------------------------------------------------------------------
# File-ask regex.
# ------------------------------------------------------------------
check("casey file ask matches", bool(file_rx.search(CASEY_ASK)))
for t in ("Provide output as a csv", "send this as a spreadsheet",
          "export to excel please", "can you output a csv file",
          "give me the data in a csv", "download as xlsx"):
    check(f"file ask: {t[:28]}", bool(file_rx.search(t)))
for t in ("what does csv stand for",
          "how many hours did subscribers stream",
          "who watches Paramount+"):
    check(f"not a file ask: {t[:28]}", not file_rx.search(t))

# ------------------------------------------------------------------
# Source wiring.
# ------------------------------------------------------------------
check("callsite consumes the merge",
      "_ca_merged = _pm_clarify_answer_merge(history, text)" in SRC)
check("merge skips the confirm",
      "if _ca_merged:" in SRC and "text = _ca_merged" in SRC)
check("ask hint on merge",
      "_pm_ask_hint(route='clarify_answer_merge')" in SRC)
check("generated read checks file ask",
      "_PM_FILE_ASK_RE.search(str(text or ''))" in SRC)
check("file built from the shipped numbers",
      "pma.build_generated_csv(_fe)" in SRC)
check("file is stamped", "_fcsv = _stamp_csv_text(_fcsv, _frng)" in SRC)
check("return carries the download", "**_file_payload}" in SRC)
check("reply says the file is saving",
      "is saving to your browser" in SRC)

print()
if FAIL:
    print(f"{FAIL} FAILED")
    sys.exit(1)
print("all clarify-answer merge checks passed")
