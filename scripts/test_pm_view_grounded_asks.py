#!/usr/bin/env python3
"""View-grounded asks stay on the view; campaign asks never offer
profile names (Jenna 2026-09-30, Attribution view chip defect)."""
import re
import sys
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
# Hermetic: extract the helpers and exercise the verdicts.
# ------------------------------------------------------------------
m = re.search(r"(_PM_VIEW_DEIXIS_RE = re\.compile.*?)\n\n\ndef "
              r"_pm_open_screen_confirm", SRC, re.S)
check("helpers present", m is not None)
ns = {"re": re}
exec(m.group(1), ns)
owns = ns["_pm_view_owns_ask"]

VC = {"view_context": {"view_id": "intentIQ",
                       "view_title": "Attribution IQ", "data": {}}}
SUB = {"view_context": {"view_id": "subscriberIQ",
                        "view_title": "Subscriber IQ", "data": {}}}

check("the defect ask grounds on the Attribution view",
      owns("What drove conversion in this campaign window?", VC))
check("campaign vocab alone owns intentIQ asks",
      owns("which creative drove the most conversions?", VC))
check("screen deixis owns any data view",
      owns("What drove signups this window?", SUB))
check("analyze-this-screen owns the view",
      owns("Analyze the data on this screen", SUB))
check("no view context never owns",
      not owns("What drove conversion in this campaign window?", {}))
check("no view context via primary-only ctx never owns",
      not owns("what is on this screen?",
               {"primary": {"name": "Paw Patrol Series Viewers"}}))
check("audience deixis stays with the profile verdict",
      not owns("what is the age split for this audience?", SUB))
check("named-subject data ask stays off the view",
      not owns("How do Yellowstone viewers skew by age?", SUB))
check("conversion vocab does not own non-attribution views",
      not owns("what drove conversions for the brand?", SUB))

camp = ns["_PM_CAMPAIGN_ASK_RE"]
check("campaign regex hits campaign/attribution/roas",
      all(camp.search(s) for s in
          ["the campaign", "attribution read", "what was the roas",
           "ad spend impact"]))
check("campaign regex skips travel flights and creative angles",
      not camp.search("how many booked flights to Vegas")
      and not camp.search("give me a creative angle for this "
                          "audience"))

# ------------------------------------------------------------------
# Source wiring.
# ------------------------------------------------------------------
check("route override computes view ownership",
      "_vc_owns = _pm_view_owns_ask(text, ctx)" in SRC)
check("view-owned asks skip generate and memory_confirm",
      "if _route in ('generate', 'memory_confirm'):\n"
      "            _route = ''" in SRC)
check("open-profile verdict gated off for view-owned asks",
      "if isinstance(ctx, dict) and not _vc_owns" in SRC)
check("view grounding leaves a trail",
      "route='view_grounded'" in SRC)
check("campaign clarify present",
      "Which campaign is this about?" in SRC)
check("campaign clarify runs before memory referents",
      SRC.index("Which campaign is this about?")
      < SRC.index("_refs = pmm.recent_referents"))
check("campaign clarify never offers profile chips",
      "'followups': [], 'offer_deck': False,\n"
      "                'deck_angle': None})" in SRC.split(
          "Which campaign is this about?", 1)[1][:600])

print()
if FAIL:
    print(f"{FAIL} FAILED")
    sys.exit(1)
print("all view-grounded ask checks passed")
