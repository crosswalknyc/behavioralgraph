#!/usr/bin/env python3
"""Three defects that turned the focus group suites red or flaky (2026-11-11).

  [1] Report copy opens on a capital because the report sets it. Until
      2026-11-11 the capital was a side effect of the door scrub, which
      raised the first letter of every string it was handed (and turned
      '25 to 34' into '25 To 34' on decks). The scrub was fixed to return
      a string with no door in it byte for byte, and two suites went red:
      a table cell read 'feature screening norm 30%' and a question typed
      in lower case kept its lower-case opening. `digital_twin_report.
      _clean` now raises the opening letter itself, and only that: a
      figure, an address, a handle and a name spelled with a lower-case
      first letter are left as written, and nothing is ever lowered.

  [2] A room of twelve never seats 'Nora A.'. A seat whose drawn name
      was taken walked the name bank in strides of seven; seven divides
      the 28 names of a bank, so the walk reached four names and then
      fell to a last initial with twenty names unused. Which rooms did
      depended on the session id, so a check on a seat's name passed or
      failed from run to run. The whole bank is now walked before an
      initial is used.

  [3] A room job never reads done before its session has landed. The
      job document was written first and the session second; the page
      polls the job and reads the session on 'done', so a read between
      the two writes took the finished record off the job document
      (which carries no trail) and saved it over the room's own landing.
      In the API suite that was a missing cost on the trail about one
      run in six under load. The session is now written first, and the
      route that asked no longer saves the twin a second time once the
      room has started (that second save could land a copy taken while
      the room was still working).

Hermetic: REGRESSION_TEST_MODE=1, no model, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_casing_and_seat_names.py
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ.setdefault("CORPUS_CATALOG", "0")

from migration import design_skill as DS  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    from test_digital_twin_pilot import _beals  # noqa: E402

FAIL = 0


def check(label, cond, detail=""):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}" + (f"  ({str(detail)[:300]})" if detail else ""))


def test_report_copy_opens_on_a_capital():
    print("[1] report copy opens on a capital, set by the report itself")
    for raw, want in (
        ("feature screening norm 30%", "Feature screening norm 30%"),
        ("did you want more of Mae or Ray after the diner fight?", "Did you want more of Mae or Ray after the diner fight?"),
        ("norm 51%", "Norm 51%"),
        ("\"quoted line\" from a seat", "\"Quoted line\" from a seat"),
    ):
        check(f"raised: {raw[:34]!r}", R._clean(raw) == want, R._clean(raw))
    for raw in (
        "Feature screening norm 30%",                 # already capitalized
        "12 of 14 seats stayed",                      # opens on a figure
        "25 to 34",                                   # a range
        "gap.com/browse/women drew the most visits",  # an address
        "@wearejyt posted the clip",                  # a handle
        "iPhone owners lean in",                      # a name spelled with a lower-case first letter
        "eBay shoppers stayed",
        "4:10 on screen, liked",
        "NCIS: New York",
        "",
    ):
        check(f"left as written: {raw[:34]!r}", R._clean(raw) == raw, R._clean(raw))
    samples = ["Jennifer Beals leads the cut.", "The Feature Screening Norm is 50%.", "CBS ordered it in May.",
               "Mae and Ray after the Diner Fight."]
    check("nothing is ever lowered: a name or a proper label keeps every capital it had",
          all(sum(c.isupper() for c in R._clean(s)) >= sum(c.isupper() for c in s) for s in samples))
    # the scrub itself changes nothing in a string with no door in it
    for raw in ("25 to 34", "17 and under", "51 days", "google.com/search", "feature screening norm 30%"):
        check(f"the door scrub returns {raw!r} byte for byte", DS.scrub_door_metaphor(raw) == raw, DS.scrub_door_metaphor(raw))
    check("and still takes the door out of a line that has one",
          "door" not in DS.scrub_door_metaphor("01 The door to new viewers").lower())


def _stride_only(base, bank, seen):
    """The walk as it was before 2026-11-11."""
    name, k = base, 0
    while name in seen:
        k += 1
        if k < len(bank):
            name = bank[(bank.index(base) + k * 7) % len(bank)]
        else:
            name = f"{base} {chr(ord('A') + (k - len(bank)) % 26)}."
    return name


def test_seat_names():
    print("[2] a seat takes a free name from the bank before it takes a last initial")
    bank = P._NAMES_F
    check("the female and male banks hold 28 names each, which seven divides", len(P._NAMES_F) == 28 and len(P._NAMES_M) == 28)
    # five seats draw 'Nora': the stride reaches four names, then the bank is walked
    seen, got = set(), []
    for _ in range(5):
        name = "Nora" if "Nora" not in seen else P._free_name("Nora", bank, seen)
        seen.add(name)
        got.append(name)
    check("five seats drawing one name get five bank names, no initial",
          len(set(got)) == 5 and all(n in bank for n in got), got)
    old_seen, old = set(), []
    for _ in range(5):
        name = _stride_only("Nora", bank, old_seen)
        old_seen.add(name)
        old.append(name)
    check("the walk as it was gave the fifth seat 'Nora A.' (the defect)", old[-1] == "Nora A.", old)
    check("the first names the walk lands on are the ones rooms were always given", got[:4] == old[:4], (got, old))
    # the bank spent: initials, each one distinct
    seen = set(bank)
    extra = []
    for _ in range(30):
        n = P._free_name("Nora", bank, seen)
        seen.add(n)
        extra.append(n)
    check("with every name seated, a last initial keeps each seat distinct",
          len(set(extra)) == 30 and extra[0] == "Nora A." and all(re.fullmatch(r"Nora [A-Z]\d*\.", n) for n in extra), extra[:4])
    # rooms of twelve across many session ids: every seat a plain first name
    twin = _beals()
    bad, would_have = [], 0
    for k in range(400):
        seed = f"panel|sess{k:04d}|jb|v"
        with contextlib.redirect_stdout(io.StringIO()):
            roster = P.build_group([twin], in_room=12, seed=seed)["roster"]
        names = [r["name"] for r in roster]
        if len(set(names)) != len(names) or not all(re.fullmatch(r"[A-Za-z]+", n) for n in names):
            bad.append((seed, names))
    check("400 rooms of twelve: every seat has its own plain first name", not bad, bad[:1])
    # the defect was real at this size: count draws that the stride alone could not seat
    import itertools
    for k in range(4000):
        seen = set()
        for j in range(12):
            base = bank[int(P.unit("name", f"s{k}", "seg", j) * len(bank))]
            name = _stride_only(base, bank, seen)
            seen.add(name)
            if "." in name:
                would_have += 1
                break
    check("the stride alone left a room of twelve with an initial on some draws, which is why the check flickered",
          would_have > 0, would_have)
    del itertools


# ------------------------------------------------------------------ [3]
def test_room_job_lands_the_session_first():
    print("[3] a room job reads done only after its session has landed")
    from migration import digital_twin_room as R
    order = []
    keep = (R._put_json, R._update_payload)
    R._put_json = lambda s3, key, doc: order.append(("job", doc.get("status"), doc.get("stage")))
    R._update_payload = lambda s3, twin_key, sid, rec, roster=None, carry=None: order.append(
        ("session", rec.get("stage"), "cost_usd" in (rec.get("trail") or {})))
    try:
        def ctx():
            return {"rec": {"id": "q1", "trail": {"cost_usd": 0.127}, "answers": []}, "t0": 0.0, "s3": object(),
                    "job_id": "j1", "twin_key": "digital-twins/t/x.json.gz", "session": {"id": "s1", "group": {"roster": []}},
                    "roster": [], "asked_by": "maya"}
        R._publish(ctx(), "done", status="done", extra={"success": True}, payload_too=True)
        check("the session is written before the job document says done",
              [o[0] for o in order] == ["session", "job"], order)
        check("the session written carries the finished stage and the trail's cost",
              order and order[0] == ("session", "done", True), order[:1])
        check("the job document that follows reads done", order[-1] == ("job", "done", "done"), order[-1:])
        del order[:]
        R._publish(ctx(), "the room is thinking", payload_too=True)
        check("a stage on the way keeps the same order", [o[0] for o in order] == ["session", "job"], order)
        del order[:]
        R._publish(ctx(), "researching")
        check("a stage with no session write only moves the job document", [o[0] for o in order] == ["job"], order)
    finally:
        R._put_json, R._update_payload = keep
    # the route that asked saves the twin only when the room did not already
    R._SAVED_AT_START.clear()
    check("a job the room never saved for is the route's to save", R.saved_at_start("j-none") is False and R.saved_at_start(None) is False)
    R._SAVED_AT_START.add("j-early")
    check("a job the room saved at the start is not saved again by the route", R.saved_at_start("j-early") is True)
    check("the id is forgotten once asked for", R.saved_at_start("j-early") is False and not R._SAVED_AT_START)
    api = open(os.path.join(ROOT, "digital_twins_api.py"), encoding="utf-8").read()
    ask = api[api.index("def dt_panel_ask("):api.index("def dt_panel_upgrade(") if "def dt_panel_upgrade(" in api else len(api)]
    tail = ask[ask.index("rec = P.ask("):]
    check("the ask route asks the room before it saves, and saves only when the room did not",
          "_R.saved_at_start(rec.get('job_id'))" in tail and "if not _landed:\n        DT.save_twin(_s3(), key, payload)" in tail
          and tail.count("DT.save_twin(") == 1, tail[:300])


if __name__ == "__main__":
    test_report_copy_opens_on_a_capital()
    test_seat_names()
    test_room_job_lands_the_session_first()
    print(f"\n{'PASS' if not FAIL else f'{FAIL} FAIL'}: focus group copy casing, seat names and the room job's landing")
    sys.exit(1 if FAIL else 0)
