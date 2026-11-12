#!/usr/bin/env python3
"""The spec normalizer sizes a one-service build by rule (2026-11-11).

Three builds of Georgie & Mandy's First Marriage shipped at the show's
whole audience when the request named one service (6,117,270 and
6,340,150 on Paramount+, 6,045,319 on HBO Max) next to 1,285,455 for
the settled Paramount+ profile, and the HBO Max brief filed the show
under STREAMING/PLATFORM. Every spec leaves the normalizer through one
call that files a title under its own kind, pins only the named
service, and sets the size from the title's whole audience and the
share of its viewing on the named service(s).

    python3 scripts/test_pm_one_service_build_size.py
"""
from __future__ import annotations

import copy
import os
import re
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["REGRESSION_TEST_MODE"] = "1"

from migration import service_scope_sizing as S  # noqa: E402

FAILS = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


TITLE = "Georgie & Mandy's First Marriage"
WIN = {"start": "2024-10-25", "end": "2026-11-10"}
FACTS = {"placed": {"paramount": 49, "hbomax": 44, "amazon": 51}, "research": ["Max", "Paramount+", "Hulu"]}
REC = S.merge_record({}, title=TITLE, whole={"people": 6_220_660, "window": WIN},
                     build={"services": ["Paramount+"], "people": 1_285_455, "sample": 38_965, "window": WIN}, facts=FACTS)


def spec(name, cat, tu, avid, anchor, share, window, named, rows):
    return {"name": name, "brand_category": cat, "subject_raw_tu": tu, "subject_raw_avid": avid,
            "universe_anchor": anchor, "engaged_share": share, "date_range": window,
            "content_seed_platforms_named": list(named), "content_show": TITLE, "seed_source": "content_map",
            "audience_type": "viewers", "subject_rows": [list(r) for r in rows]}


JESSIE = spec("Georgie & Mandy's First Marriage Viewers - Paramount+", "STREAMING/PLATFORM", 187167, 58038, 6_200_000, 1.0,
              "2024-10-25 TO 2026-11-10", ["Paramount+"],
              [["STREAMING/PLATFORM", "Paramount+"], ["BROADCAST/CABLE", "CBS"], ["STREAMING/PLATFORM", "Peacock"]])
ALEXIA = spec("GEORGIE & MANDY'S FIRST MARRIAGE Viewers", "SERIES", 190493, 58436, 8_200_000, 0.755,
              "2024-10-25 TO 2026-10-31", ["Paramount+"],
              [["SERIES", TITLE], ["STREAMING/PLATFORM", "Paramount+"], ["BROADCAST/CABLE", "CBS"]])
HBO = spec("Georgie & Mandy's First Marriage Viewers - HBO Max", "STREAMING/PLATFORM", 184642, 52339, 6_200_000, 1.0,
           "2026-08-07 TO 2026-11-10", ["HBO Max"],
           [["STREAMING/PLATFORM", "HBO Max"], ["BROADCAST/CABLE", "CBS"], ["STREAMING/PLATFORM", "Paramount+"],
            ["STREAMING/PLATFORM", "Peacock"]])

print("[1] the three real specs through the normalizer's call, the Paramount+ profile on record")
out = {}
with mock.patch.object(S, "load_record", lambda title, s3=None: copy.deepcopy(REC)):
    for key, sp in (("jessie", JESSIE), ("alexia", ALEXIA), ("hbo", HBO)):
        out[key] = S.normalize_spec(copy.deepcopy(sp), sp["name"])
        print(f"       {key}: {sp['subject_raw_tu']:,} -> {out[key]['subject_raw_tu']:,} "
              f"({round(out[key]['subject_raw_tu'] * S.PANEL_TO_US):,} people)")
people = {k: round(v["subject_raw_tu"] * S.PANEL_TO_US) for k, v in out.items()}
check(out["jessie"]["subject_raw_tu"] == 38_965, "Jessie's rerun is the settled Paramount+ profile, 1,285,455", str(people))
check((out["alexia"]["subject_raw_tu"], out["hbo"]["subject_raw_tu"]) == (38_677, 12_263),
      "Alexia's spec reads 1,275,954 and the HBO Max brief 404,556, the size its live file carries", str(people))
check(1_285_455 * 0.97 < people["alexia"] < 1_285_455, "Alexia's shorter window sits just under it", str(people))
check(people["hbo"] < 1_285_455 and people["hbo"] < out["hbo"]["universe_anchor"] * 0.88,
      "HBO Max, a slice on three months, sits under Paramount+ and well under the whole", str(people))
for key in out:
    check(out[key]["subject_raw_tu"] % 10 != 0, f"{key}: the sample is messy")
    check(0 < out[key]["subject_raw_avid"] < out[key]["subject_raw_tu"], f"{key}: the avid cohort stays a part of it")
    check(not any(v in ("CBS", "Peacock") for _c, v in out[key]["subject_rows"]), f"{key}: only the named service pins")
check(out["hbo"]["brand_category"] == "SERIES" and ["SERIES", TITLE] in out["hbo"]["subject_rows"],
      "the HBO Max brief is filed under SERIES with the title anchored there")
check(out["jessie"]["brand_category"] == "SERIES", "and so is the Paramount+ rerun's")

print("[2] nothing on record: still under the whole the draft stated")
for key, sp, whole in (("jessie", JESSIE, 6_200_000), ("alexia", ALEXIA, 8_200_000), ("hbo", HBO, 6_200_000)):
    got = S.normalize_spec(copy.deepcopy(sp), sp["name"])
    check(got["subject_raw_tu"] * S.PANEL_TO_US < whole * 0.5, f"{key}: under half the whole with nothing established",
          str(got["subject_raw_tu"]))

print("[3] a spec that is not a one-service title build is handed back as it came")
for sp in ({"name": "Nike", "brand_category": "APPAREL", "subject_raw_tu": 90211, "subject_rows": [["APPAREL", "Nike"]]},
           {"name": "Netflix", "brand_category": "STREAMING/PLATFORM", "subject_raw_tu": 90211,
            "subject_rows": [["STREAMING/PLATFORM", "Netflix"]]},
           dict(copy.deepcopy(ALEXIA), content_seed_platforms_named=[])):
    check(S.normalize_spec(copy.deepcopy(sp), sp["name"]) == sp, f"{sp['name']!r} untouched")
check(S.normalize_spec(None) is None and S.normalize_spec({}) == {}, "bad input never raises")

print("[4] the normalizer")
chat = open(os.path.join(ROOT, "prometheus", "legacy", "chat.py"), encoding="utf-8").read()
body = chat[chat.index("def _spec_from_draft(draft):"):]
body = body[:body.index("\ndef ", 10)]
rets = re.findall(r"^    return (.+)$", body, re.M)
check(len(rets) == 1 and "fromlist=['normalize_spec']).normalize_spec(spec, subject)" in rets[0],
      "every spec leaves _spec_from_draft through the one call", str(rets)[:200])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
print("all checks passed")
