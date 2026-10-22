#!/usr/bin/env python3
"""A subscribe overlap of a held audience is a Custom Ask, $500.

Bria Dotson, 2026-10-21 19:39 UTC, open profile Law & Order: SVU on
Peacock: "Of those viewers who watch Law & Order: SVU on Peacock, how
many subscribe to Netflix?" shipped a compiled overlap (29.6M viewers,
10.7M Netflix subscribers, usage vs payment, ad-free vs ad-supported)
at the metered chat rate because the title's profile was open.

Opening a file does not make a new cross-service count chat. Size
is its own $100 read (2026-10-21). A Tania-style 'of those, how
many are on X' with no subscribe wording stays the free cross.
Insights and 'what does this mean' stay metered.

Hermetic: imports overlap_read and reads chat.py / sizing_lane.py as
source. No network.
"""
from __future__ import annotations

import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus.overlap_read import is_overlap_read  # noqa: E402

BRIA = ("Of those viewers who watch Law & Order: SVU on Peacock, "
        "how many subscribe to Netflix?")

FAIL = 0


def check(name, ok):
    global FAIL
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAIL += 1


# --- detector --------------------------------------------------------
check("Bria's SVU x Netflix subscribe ask is an overlap read",
      is_overlap_read(BRIA) is True)
check("how many of those viewers subscribe to Hulu is an overlap read",
      is_overlap_read("how many of those viewers subscribe to Hulu?") is True)
check("what share of this audience holds a Netflix subscription is one",
      is_overlap_read("what share of this audience holds a Netflix subscription?") is True)
check("incrementality of Lore Olympus to Prime Video is one",
      is_overlap_read("how incremental is Lore Olympus to Prime Video?") is True)
check("what they watched on Prime Video in the 30 days after is one",
      is_overlap_read("what did BookTok-exposed Love Hypothesis viewers watch "
                      "on Prime Video in the 30 days after?") is True)

check("how big is the FanDuel audience is not one",
      is_overlap_read("how big is the FanDuel audience") is False)
check("sample size of Chipotle buyers is not one",
      is_overlap_read("sample size of Chipotle buyers") is False)
check("what does this 36.2% mean is not one",
      is_overlap_read("what does this 36.2% mean") is False)
check("craziest insights from the Brock profile is not one",
      is_overlap_read("craziest insights from the Brock profile") is False)
check("what does Netflix read on this file is not one",
      is_overlap_read("what does Netflix read on this file") is False)
check("Tania's of those how many are on Paramount+ is not one",
      is_overlap_read("how many UFC fans are there out there, of those, "
                      "how many are on Paramount+, on HBO Max and how many "
                      "have neither service") is False)
check("empty is not one", is_overlap_read("") is False)

# --- with-base gate in chat.py --------------------------------------
chat = (WEB / "prometheus" / "legacy" / "chat.py").read_text()
gate_at = chat.find("NEW-NUMBERS ON AN OPEN FILE (2026-10-21)")
no_base_at = chat.find("if not base:\n        # A QUESTION about a never-pulled subject")
digest_at = chat.find("Base rows ride the prompt (2026-08-27, toy-categories routing)")
check("the with-base overlap gate is in generate_metrics", gate_at > 0)
check("the overlap gate sits after the no-base offer block",
      gate_at > no_base_at > 0)
check("the overlap gate sits before the digest load",
      0 < gate_at < digest_at)
check("confirm still charges via consume_credit",
      "consume=_H.consume_credit" in chat[gate_at:digest_at]
      and "offer_or_charge(" in chat[gate_at:digest_at])   # the one priced gate charges through consume_credit (2026-10-21)
check("unconfirmed overlap returns panel_offer, not a generated reply",
      "panel_offer" in chat[gate_at:digest_at])
check("overlap offer uses the custom-read kind, not viewership copy",
      "kind=_off_kind" in chat[gate_at:digest_at] or "kind=_pr_kind" in chat[gate_at:digest_at])
check("confirmed overlap keeps the catalog base (does not blank s3_key)",
      "s3_key': ''" not in chat[gate_at:digest_at])

# --- sizing lane steps aside ----------------------------------------
sizing = (WEB / "prometheus" / "sizing_lane.py").read_text()
check("sizing_lane returns None on an overlap read",
      "overlap_read" in sizing and "is_overlap_read(text)" in sizing)
sz_ans = sizing[sizing.index("def answer("):]
check("the overlap bail runs before the free cross answer",
      sz_ans.index("overlap_read") < sz_ans.index("cross_sizing"))

print("\n" + ("OK" if not FAIL else f"{FAIL} FAILED"))
sys.exit(1 if FAIL else 0)
