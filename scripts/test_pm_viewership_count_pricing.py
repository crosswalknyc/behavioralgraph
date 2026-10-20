#!/usr/bin/env python3
"""A viewership count on a named title is a priced viewership read,
never a sample check and never the $100 breakdown price.

Marie Flores at WBD, 2026-10-20 21:12 UTC: "Among U.S. panelists who
watched Everybody Loves Raymond on Amazon Prime Video in September
2026, how many people watched the series and what other streaming
platforms did those viewers also watch ... Please show the results by
platform as a table" drew the sample-check card (route sample_check,
empty answer, "No written answer on this step" in the question copy)
because the interpret surface's incidence pre-check matched "how many
... people". Had it reached the pricing lane, the breakdown price
($100, a split of a figure already delivered) would have overridden
the viewership price ($500 per year of window) on a question that had
no figure to break down.

Hermetic: reads prometheus/legacy/chat.py and prometheus/legacy/watch.py
as source and executes only the pure detectors.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
CHAT = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text()
WATCH = (WEB / 'prometheus' / 'legacy' / 'watch.py').read_text()

MARIE = ("Among U.S. panelists who watched Everybody Loves Raymond on Amazon Prime Video in September 2026, "
         "how many people watched the series and what other streaming platforms did those viewers also watch "
         "Everybody Loves Raymond on during September 2026? Please show the results by platform as a table")

FAIL = 0


def check(name, ok):
    global FAIL
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAIL += 1


def _grab(src, name):
    i = src.index(f"def {name}(")
    j = src.index("\ndef ", i + 1)
    return src[i:j]


# --- the incidence pre-check (interpret surface) --------------------------
ns = {'re': re}
exec(_grab(CHAT, '_synth_chat_is_incidence_request'), ns)
is_incidence = ns['_synth_chat_is_incidence_request']

check('Marie\'s viewership count is not a sample check', is_incidence(MARIE) is False)
check('"how many people read the Walsh family book series last year" is not a sample check',
      is_incidence('how many people read the walsh family book series in the us last year?') is False)
check('a sample question still is one',
      is_incidence('how many panelists would we have for FanDuel customers over the last year?') is True)
check('incidence wording still is one',
      is_incidence("what's the incidence of Kalshi customers?") is True)
check('a count that names the sample still is one',
      is_incidence('how many people watched Landman are in the sample?') is True)
check('a sample-size instruction is still not one',
      is_incidence('use a sample size of 50,000 for the Landman build') is False)

# --- the viewership price (watch.py) -------------------------------------
wns = {'re': re, 'datetime': datetime, 'timezone': timezone}
c0 = WATCH.index('_PM_VIEWERSHIP_METRIC_RX = re.compile(')
c1 = WATCH.index('def _pm_is_viewership_series_ask(')
exec(WATCH[c0:c1], wns)
for fn in ('_pm_is_consumption_count_ask', '_pm_window_years'):
    exec(_grab(WATCH, fn), wns)
check('Marie\'s ask is a consumption count (a viewership read)', wns['_pm_is_consumption_count_ask'](MARIE) is True)
check('one month of window prices as one year', wns['_pm_window_years'](MARIE) == 1)

# --- pricing precedence in the custom-read lane ---------------------------
lane = CHAT[CHAT.index('_vw = _pm_viewership_read_price(text, _pm_user)'):]
lane = lane[:lane.index('if isinstance(panel_confirm, dict):')]
check('the breakdown price needs a delivered figure (bound_series or a derived confirm)',
      "(metric_request or {}).get('bound_series')" in lane and "== 'derived'" in lane)
check('the viewership price stands when the ask asks for the count itself',
      re.search(r"if _is_derived and \(_has_delivered or not _vw\):", lane) is not None)
check('the old unconditional override is gone',
      re.search(r"^\s*if _is_derived:\s*#", lane, re.M) is None)

print('\n' + ('OK' if not FAIL else f'{FAIL} FAILED'))
sys.exit(1 if FAIL else 0)
