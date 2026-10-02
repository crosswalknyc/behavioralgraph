#!/usr/bin/env python3
"""Wrong-answer complaint intercept + replay-once guard + refusal guard.

Hermetic: extracts the helper block from app.py source and exercises it
with a stubbed prometheus_memory. No Flask, no network.
"""
import re
import sys
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
# Legacy-move shim (2026-10-01): the Prometheus code lives in
# bg-webapp/prometheus/legacy/chat.py; read app.py + legacy as one source.
import os as _pm_os, sys as _pm_sys
_pm_r = _pm_os.path.dirname(_pm_os.path.abspath(__file__))
while not _pm_os.path.exists(_pm_os.path.join(_pm_r, 'bg-webapp', 'app.py')):
    _pm_r = _pm_os.path.dirname(_pm_r)
_pm_sys.path.insert(0, _pm_os.path.join(_pm_r, 'scripts'))
from _pm_test_source import app_path as _pm_app_path, host_for as _pm_host_for  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
_APP = _pm_app_path()
if not _APP.exists():
    _APP = _pm_app_path()
SRC = _APP.read_text(encoding="utf-8")

start = SRC.index("_PM_WRONG_ANSWER_RES = (")
end = SRC.index("_PM_FEEDBACK_RES = (")
block = SRC[start:end]

fake_mem = types.ModuleType("prometheus_memory")
fake_mem._RECS = []
fake_mem.recall = lambda user, k=12: list(fake_mem._RECS)
sys.modules["prometheus_memory"] = fake_mem

ns = {"re": re, "datetime": datetime, "timezone": timezone}
_pm_host_for(ns)
exec(block, ns)

WRONG = ns["_PM_WRONG_ANSWER_RES"]
prev_q = ns["_pm_prev_user_question"]
repeat_block = ns["_pm_replay_repeat_block"]
refusal = ns["_pm_reads_as_refusal"]

fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def hits(t):
    return any(rx.search(t) for rx in WRONG)


# --- complaint patterns ---
check("casey exact phrasing", hits("thats not what i asked for"))
check("apostrophe variant", hits("that's not what I asked"))
check("wrong answer", hits("wrong answer"))
check("this is not right", hits("this is not right"))
check("didnt answer my question", hits("you didnt answer my question"))
check("try again", hits("try again"))
check("still wrong", hits("still wrong"))
check("status ask not a complaint", not hits("is eastside golf running?"))
check("real ask not a complaint", not hits(
    "Give me the top 5 most common activities happening on a second "
    "screen while a person is watching Paramount+"))
check("window rerun not a complaint", not hits("run it again for 2024"))
check("wrong turn not a complaint", not hits(
    "what share made a wrong turn in the journey"))

# --- previous-question recovery (Casey's real history shape) ---
HIST = [
    {"role": "user", "text": "Pull the top titles inside the Kids & Family genre cohort"},
    {"role": "agent", "text": "Kids & Family is the largest genre cohort..."},
    {"role": "user", "text": "Give me the top 5 most common activities happening on a second screen while a person is watching Paramount+, segmented by if they are watching sports/Live content vs. VOD"},
    {"role": "agent", "text": "Kids & Family is the largest genre cohort..."},
    {"role": "user", "text": "thats not what i asked for"},
]
got = prev_q(HIST, "thats not what i asked for")
check("prev question recovered", got.startswith("Give me the top 5"))
check("prev question skips complaint turn", "not what i asked" not in got)
check("no history -> empty", prev_q([], "thats not what i asked for") == "")

# --- replay-once guard ---
NOW = datetime.now(timezone.utc)
Q = ("Give me the top 5 most common activities happening on a second "
     "screen while a person is watching Paramount+, segmented by if "
     "they are watching sports/Live content vs. VOD")
fake_mem._RECS = [{
    "route": "replay", "question": Q,
    "ts": (NOW - timedelta(seconds=40)).isoformat(),
}]
check("identical re-ask 40s later blocked", repeat_block("cpearson", Q))
check("different question not blocked",
      not repeat_block("cpearson", "what is the genre mix"))
fake_mem._RECS = [{
    "route": "replay", "question": Q,
    "ts": (NOW - timedelta(hours=2)).isoformat(),
}]
check("old replay not blocked", not repeat_block("cpearson", Q))
fake_mem._RECS = [{
    "route": "generated", "question": Q,
    "ts": (NOW - timedelta(seconds=40)).isoformat(),
}]
check("non-replay route not blocked", not repeat_block("cpearson", Q))
check("empty user not blocked", not repeat_block("", Q))

# --- refusal detector ---
CASEY_REFUSAL = ("I could not lock the numbers on that one down cleanly, "
                 "and I would rather re-aim than guess. Tell me the "
                 "specific read you want on Paramount+ - name the cohort, "
                 "the category, or the two things to compare - and I will "
                 "run it clean.")
check("casey refusal detected", refusal(CASEY_REFUSAL))
check("rephrase ask detected", refusal(
    "Could you rephrase your question so I can pin it down?"))
GOOD = ("Second screens run hotter during live sports: 81.3% of sports "
        "and live sessions carry concurrent activity, against 62.7% of "
        "VOD sessions, on 61,419,362 of 83,791,763 projected viewers.")
check("real read passes", not refusal(GOOD))
check("short numberless reply flagged", refusal("Happy to take a look."))
check("mode chip skips digit test", not refusal("Happy to take a look.", "personas"))
check("long qualitative passes", not refusal("word " * 250))

# --- wiring assertions on the route source ---
check("replay honors skip flag",
      "and not _pm_skip_replay" in SRC and
      "_pm_replay_repeat_block(_pm_user, text)" in SRC)
check("complaint runs before artwork feedback",
      SRC.index("_PM_WRONG_ANSWER_RES):") <
      SRC.index("for rx in _PM_FEEDBACK_RES):"))
check("refusal retry wired",
      "refusal_retry" in SRC and
      "Your previous draft declined to answer" in SRC)

PMA = (Path(__file__).resolve().parents[1] / "prometheus_analysis.py").read_text(encoding="utf-8")
check("never-decline in both prompts",
      PMA.count("NEVER DECLINE") == 2)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("ALL PASS")
