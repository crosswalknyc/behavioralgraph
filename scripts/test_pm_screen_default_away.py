#!/usr/bin/env python3
"""The open profile is not the default subject (Jenna 2026-09-29).

Hermetic checks on _pm_screen_bind_verdict plus source wiring for the
bind/away routing, the audience line, and the one-tap switch chip.
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
# Extract the verdict function with light stubs.
# ------------------------------------------------------------------
m = re.search(r"(_PM_SCREEN_DEIXIS_RE = re\.compile.*?)"
              r"\n\ndef _pm_open_screen_confirm", SRC, re.S)
check("verdict block present", m is not None)


def _norm(s):
    return re.sub(r"\s+", " ",
                  re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())).strip()


ns = {"re": re, "_normalize_for_match": _norm,
      "_PM_CLARIFY_STOP_TOKENS": {"the", "a", "an", "of", "on", "for",
                                  "fans", "viewers", "audience"}}
_pm_host_for(ns)
exec(m.group(1), ns)
verdict = ns["_pm_screen_bind_verdict"]

PAGE = "Trinity Tatum"
PKEY = "Trinity_Tatum_09_01_2026_10_00.csv"
PBASE = {"subject": PAGE, "s3_key": PKEY, "source": "page"}

# Points at the screen -> page, silently.
check("pronoun ask binds the page",
      verdict("how do they index on QSR", PAGE, PBASE, PKEY) == "page")
check("deixis binds the page",
      verdict("what does this audience buy", PAGE, PBASE, PKEY)
      == "page")
check("elliptical demo ask binds the page",
      verdict("age breakdown?", PAGE, PBASE, PKEY) == "page")
check("income skew binds the page",
      verdict("whats the income skew", PAGE, PBASE, PKEY) == "page")
check("page named outright binds the page",
      verdict("how do trinity tatum fans shop", PAGE,
              {"subject": PAGE, "s3_key": PKEY, "source": "catalog"},
              PKEY) == "page")

# General asks default away from the screen.
check("market question answers away",
      verdict("what is the most watched streaming platform", PAGE,
              PBASE, PKEY) == "away")
check("market-scoped demo ask answers away",
      verdict("average age of americans", PAGE, PBASE, PKEY) == "away")
check("us adults scope answers away",
      verdict("how many us adults use smart tvs", PAGE, PBASE, PKEY)
      == "away")
check("unrelated general ask answers away",
      verdict("which qsr chain is growing fastest", PAGE, PBASE, PKEY)
      == "away")

# Torn cases still confirm.
CUT = "The Spiderwick Chronicles - Under-18 Viewers"
CUTKEY = "The_Spiderwick_Chronicles_-_Under-18_Viewers.csv"
TUBASE = {"subject": "The Spiderwick Chronicles",
          "s3_key": "The_Spiderwick_Chronicles.csv",
          "source": "catalog"}
check("cut-vs-parent confirms",
      verdict("how did spiderwick grow on streaming", CUT, TUBASE,
              CUTKEY) == "confirm")
check("definite ref with a cut open confirms",
      verdict("how did the show grow its audience", CUT,
              {"subject": CUT, "s3_key": CUTKEY, "source": "page"},
              CUTKEY) == "confirm")
check("definite ref with the TU open binds the page",
      verdict("how did the show grow its audience", "Landman",
              {"subject": "Landman", "s3_key": "L.csv",
               "source": "page"}, "L.csv") == "page")

# ------------------------------------------------------------------
# Source wiring.
# ------------------------------------------------------------------
check("named subject returns a bind route",
      "return {'route': 'bind', 'subject': named}" in SRC)
check("general asks return an away route",
      "return {'route': 'away'}" in SRC)
check("callsite consumes routing dicts",
      "if isinstance(_osc, dict):" in SRC)
check("bind path never falls back to the page",
      "prefer_catalog=True,\n                        "
      "bind_subject=_osc.get('subject')," in SRC)
check("switch_page kwarg on the generate pass",
      "switch_page=None):" in SRC)
check("answer opens with the audience line",
      'reply = f"On {_aud}:\\n\\n{reply}"' in SRC)
check("switch chip rides memory_confirm",
      "_sw_chip = f'On {_sw} instead'" in SRC)
check("torn confirm carries both options",
      "_opts.append({'label': f'On {_alt}', 'subject': _alt})" in SRC)
check("old always-ask confirm retired",
      "'reply': (f'Did you want this on {page} (open on '" not in SRC)
check("2026-09-28 always-confirm superseded",
      "supersedes the 2026-09-28 always-confirm" in SRC)

print()
if FAIL:
    print(f"{FAIL} FAILED")
    sys.exit(1)
print("all screen-default-away checks passed")
