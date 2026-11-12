#!/usr/bin/env python3
"""The Brand Partnership valuation deck leaves the builder clean (2026-11-11).

A valuation deck is a paid delivery. On 2026-11-11 the deck the product
built for one had to be repaired by hand before it could go out, and
Jenna's instruction was: "these errors cannot happen. fix and ensure
itll be correct going forward". These checks hold every defect that was
found, on a valuation with no comparison group and on one with:

  [1] no comparison group bought: no page speaks of one
  [2] neither deck carries a test statistic, an outside name behind a
      rate, a word for how a figure was made, or a dashboard note
  [3] every page total is the sum of what that page shows: the lens
      tiles add to the total, the earned-media table adds to its tile
      and carries every channel that was priced (LinkedIn included),
      each calculator multiplies out, and conversion value marked as a
      thin signal still has its tile when it is in the total
  [4] valuation dollars print exact with cents; none is abbreviated
  [5] the short version is the second page, three to five lines
  [6] the cover is the pairing in the product's order with a plain claim
      line; DRAFT prints only when the valuation says it is a draft
  [7] the builder's own traits pass the brand lint: a page-number field
      on every page, rounded corners, no type under 9pt and no sentence
      at 9pt in the content area, headlines set as a bold claim with a
      light close, one sentence of twelve words or fewer, ending in a
      period
  [8] a page the campaign created has no visits before it existed, from
      the engine and on a valuation saved before the engine was fixed; a
      page that was live inside the before window keeps its count
  [9] labels come out as written: no '51 Days', no '17 And under'
  [10] READY is the whole truth: a lint FAIL or a plain-English flag is
      never READY; both decks finish READY with nothing for the finisher
      to repair; a headline in two weights is not bound or resized by
      the finisher's widow net
  [11] the dashboard's Brand Lift "How this is calculated" note names a
      matched group only when one is part of the valuation
  [12] when the valuation carries the talent's own audience beside the
      campaign's reach, the deck names it once and no count moves

Hermetic: no S3, no network, no model, no app import. Run:
    python3 scripts/test_bpiq_valuation_deck_clean.py
"""
from __future__ import annotations

import contextlib
import copy
import io
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CORPUS_CATALOG", "0")
os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ.setdefault("PM_MAIL_OFF", "1")

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / "scripts"))
sys.modules.setdefault("app", None)          # the dashboard app never boots in a test

from pptx import Presentation  # noqa: E402
from pptx.util import Emu  # noqa: E402

from migration import bpiq_campaign_assets as CA  # noqa: E402
from migration import bpiq_deck_builder as B  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    import test_bpiq_campaign_reach as R  # noqa: E402  (the footprint fixture)
    import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim / campaign fixtures)

FAIL = 0


def check(label, cond, detail=""):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}" + (f"  ({str(detail)[:300]})" if detail else ""))


# --------------------------------------------------------------- fixtures
BRAND, TALENT, CAMPAIGN = "Fernhill", "Mara Vale", "Fernhill x Mara Vale"
OWN_PAGE = "fernhill.com/browse/fernhill-x-mara-vale"
TOUR_PAGE = "Mara Vale mall tour landing page"
RELEASE = "fernhillinc.com newsroom partnership release"
PROPERTIES = [
    {"common_name": "fernhill.com homepage", "pre_hits": 2417, "post_hits": 6983},
    {"common_name": OWN_PAGE, "pre_hits": 67, "post_hits": 5831},          # did not exist before the campaign
    {"common_name": "Fernhill on Instagram (@fernhill)", "pre_hits": 1947, "post_hits": 5486},
    {"common_name": "fernhill.com/browse/women", "pre_hits": 1864, "post_hits": 3217},
    {"common_name": RELEASE, "pre_hits": 142, "post_hits": 1673},          # published on the first day
    {"common_name": TOUR_PAGE, "pre_hits": 39, "post_hits": 1284},         # launched with the campaign
]


def engine_inputs(control: bool, start="2026-09-22", end="2026-11-11"):
    return T.inputs(brand=BRAND, campaign=CAMPAIGN, start=start, end=end, post_end=end, control=control,
                    talent=TALENT)


def engine_campaign(inp, drop_dates=("2026-09-22",)):
    c = dict(T.campaign(sold_out=False), campaign_name=CAMPAIGN, brand=BRAND, talent=TALENT,
             drop_dates=list(drop_dates), footprint=R.footprint())
    return S.normalize_campaign(c, inp)


def valuation(control: bool, drop_dates=("2026-09-22",)) -> dict:
    """A valuation as the engine builds it: every lens, share and line
    derived, so the document is coherent by construction."""
    inp = engine_inputs(control)
    prim = dict(T.prim(), top_brand_properties=copy.deepcopy(PROPERTIES))
    with contextlib.redirect_stdout(io.StringIO()):
        p = S.build_payload(inp, prim, campaign=engine_campaign(inp, drop_dates))
    p["conversions"]["low_signal"] = True          # a thin signal: the dashboard hides the card
    return p


def legacy(doc: dict) -> dict:
    """The same valuation as it was saved before 2026-11-11: the campaign's
    own pages carry a before count and no marker."""
    d = copy.deepcopy(doc)
    bogus = {OWN_PAGE: 2211, TOUR_PAGE: 1287, RELEASE: 4684}
    for row in d["top_brand_properties"] + d["top_brand_properties_pre"]:
        row.pop("new_in_window", None)
    for row in d["top_brand_properties_pre"]:
        if row["common_name"] in bogus:
            row["hits_projected"] = bogus[row["common_name"]]
            row["hits"] = max(1, bogus[row["common_name"]] // 33)
    return d


def pages(prs) -> list:
    """Per page, the text of every shape in drawing order."""
    out = []
    for s in prs.slides:
        out.append([" ".join(sh.text_frame.text.split()) for sh in s.shapes
                    if sh.has_text_frame and sh.text_frame.text.strip()])
    return out


def page_named(pg: list, eyebrow: str) -> list:
    for texts in pg:
        if texts and re.fullmatch(r"\d\d\s+" + re.escape(eyebrow.upper()), texts[0]):
            return texts
    return []


def money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def after(texts: list, label: str) -> str:
    """The text drawn right after a label."""
    i = texts.index(label)
    return texts[i + 1]


MONEY = re.compile(r"^\$\d[\d,]*\.\d\d$")
COUNT = re.compile(r"^\d[\d,]*$")

with contextlib.redirect_stdout(io.StringIO()):
    DOC_PLAIN = valuation(False)
    DOC_CONTROL = valuation(True)
    PRS_PLAIN = B.build_presentation(DOC_PLAIN, category="FASHION")
    PRS_CONTROL = B.build_presentation(DOC_CONTROL, category="FASHION")
PG_PLAIN, PG_CONTROL = pages(PRS_PLAIN), pages(PRS_CONTROL)
BLOB_PLAIN = "\n".join(t for pg in PG_PLAIN for t in pg)
BLOB_CONTROL = "\n".join(t for pg in PG_CONTROL for t in pg)
DECKS = (("no comparison group", DOC_PLAIN, PRS_PLAIN, PG_PLAIN, BLOB_PLAIN),
         ("with a comparison group", DOC_CONTROL, PRS_CONTROL, PG_CONTROL, BLOB_CONTROL))


# ------------------------------------------------------------------ [1]
def test_no_control_language():
    print("[1] a valuation bought without a comparison group never mentions one")
    check("the fixture has no comparison group", not DOC_PLAIN["control_group"].get("enabled")
          and DOC_CONTROL["control_group"].get("enabled"))
    hits = sorted(set(m.group(0).lower() for m in re.finditer(
        r"matched|control|comparison group|exposed|adjusted|incremental|holdout|held baseline|\+0\.00", BLOB_PLAIN, re.I)))
    check("no matched group, control, exposed group, adjusted or incremental lift on any page", not hits, hits)
    check("the brand lift page reads as a before and after count",
          any("had not in the 51 days before" in t for t in page_named(PG_PLAIN, "Brand lift")),
          page_named(PG_PLAIN, "Brand lift"))
    check("with one bought, the deck names the matched group and states the lift flat",
          "matched group" in BLOB_CONTROL and "LIFT OVER THE MATCHED GROUP" in BLOB_CONTROL
          and re.search(r"[+-]\d+\.\d points", BLOB_CONTROL) is not None)
    check("the matched group's account carries projected counts, never the observed base",
          f"Both groups hold {DOC_CONTROL['projected_audience_size']:,} people" in BLOB_CONTROL
          and f"{DOC_CONTROL['audience_size']:,} people" not in BLOB_CONTROL)
    _cg = DOC_CONTROL["control_group"]
    _m = _cg.get("match") or {}
    _apart = abs(float(_m.get("exposed_pre_rate_pct", 0)) - float(_m.get("control_pre_rate_pct", 0))) >= 0.05
    check("two groups that start apart are never said to start the same",
          not _apart or not re.search(r"same starting rate|had the same \w+ engagement", BLOB_CONTROL),
          str(_m))
    check("the two starting rates print at the one decimal the rest of the deck uses",
          not re.search(r"\d\.\d\d% (?:engaged|before)", BLOB_CONTROL))


# ------------------------------------------------------------------ [2]
STATS = re.compile(r"z\s*=|\bz[- ]score|p\s*[<=]\s*0|p[- ]value|95%|\bCI\b|confidence|significan|detection floor|"
                   r"margin of error|Δ|statistic", re.I)
OUTSIDE = re.compile(r"hootsuite|sprout|emarketer|\bIAB\b|\bSMI\b|nielsen|comscore|statista|\bpew\b|sensor tower|"
                     r"marketing solutions|reddit for business|amazon ads|benchmark|ratecard|rate card|rate guide|"
                     r"rates and sources|all platform rates|according to", re.I)
METHOD = re.compile(r"\bpanel|panelist|modeled|modelled|estimated|synthetic|\bsynth\b|calibrated|anchored|"
                    r"editable|dashboard|incidence|universe|cohort|behaviorgraph|pipeline|hostmap|claude", re.I)


def test_no_statistics_sources_or_method_words():
    print("[2] no test statistic, no outside name, no method word, no dashboard note")
    for label, _doc, _prs, pg, blob in DECKS:
        for name, rx in (("test statistic", STATS), ("outside name", OUTSIDE), ("method word", METHOD)):
            hits = sorted(set(m.group(0) for m in rx.finditer(blob)))
            check(f"{label}: no {name}", not hits, hits)
        check(f"{label}: the builder's own guard agrees", not B.reader_blocked(blob), B.reader_blocked(blob)[:5])
        check(f"{label}: no em dash, en dash or door", "\u2014" not in blob and "\u2013" not in blob
              and not re.search(r"\bdoors?\b", blob, re.I))
        check(f"{label}: no significance row on the method page",
              "SIGNIFICANCE" not in blob and any(t == "THE DOLLAR VALUES" for t in pg[-1]), pg[-1][:6])
    # a line the valuation supplies is cleaned before it prints
    doc = copy.deepcopy(DOC_PLAIN)
    doc["conversions"]["note"] = ("The collection sold through in a week. Per Hootsuite Social Media Benchmark Report "
                                  "2026 the rate is modeled from panel data.")
    doc["valuation"]["rate_sources"] = {"bev_per_user": "Sprout Social 2026 Q1 Benchmarks. Editable in the live dashboard.",
                                       "emv_per_user": {"Instagram": "Hootsuite Social Media Benchmark Report 2026"}}
    doc["diagnostics"]["significance"] = {"primary_test_z": 4.2, "delta_ci_95_pp": [1.0, 2.0], "significant": True,
                                          "primary_test_p_value": 0.0001, "detection_floor_pp": 0.04}
    with contextlib.redirect_stdout(io.StringIO()):
        blob = "\n".join(t for pg in pages(B.build_presentation(doc, category="FASHION")) for t in pg)
    check("a valuation that carries outside rate names and a significance block prints neither",
          not STATS.search(blob) and not OUTSIDE.search(blob) and not METHOD.search(blob),
          [m.group(0) for rx in (STATS, OUTSIDE, METHOD) for m in rx.finditer(blob)][:6])
    check("the clean sentence of a supplied line still prints", "The collection sold through in a week." in blob)


# ------------------------------------------------------------------ [3]
def test_pages_add_up():
    print("[3] every page total is the sum of what the page shows")
    for label, doc, _prs, pg, blob in DECKS:
        v = doc["valuation"]
        hero = page_named(pg, "Total value")
        check(f"{label}: the total value page is there", bool(hero), [p[0] for p in pg])
        tiles = {}
        for lens in ("EARNED MEDIA", "BRAND ENGAGEMENT", "BRAND LIFT", "CONVERSION"):
            if lens in hero:
                tiles[lens] = money(after(hero, lens))
        total = money(next(t for t in hero if MONEY.match(t)))
        check(f"{label}: conversion is in the total, so its tile shows (thin signal or not)",
              doc["conversions"]["low_signal"] and v["conversion_value"] > 0 and "CONVERSION" in tiles, tiles)
        check(f"{label}: the lens tiles add to the total on the page",
              len(tiles) == 4 and abs(sum(tiles.values()) - total) < 0.005, (tiles, total))
        check(f"{label}: the total is the valuation's total", abs(total - v["total_brand_value"]) < 0.005,
              (total, v["total_brand_value"]))
        check(f"{label}: the cover and the short version carry the same total",
              B.fmt_money(total) in pg[0] and any(B.fmt_money(total) in t for t in pg[1]))
        # earned media: every priced channel is a row and the rows sum to the tile
        emv = page_named(pg, "Earned media")
        i0, i1 = emv.index("EARNED MEDIA VALUE"), emv.index("Total earned media")
        cells = emv[i0 + 1:i1]
        rows = [cells[k:k + 4] for k in range(0, len(cells), 4)]
        check(f"{label}: the table is name, people, rate, value on every row",
              all(len(r) == 4 and COUNT.match(r[1]) and MONEY.match(r[2]) and MONEY.match(r[3]) for r in rows), rows[:2])
        priced = [r for r in v["emv_breakdown"] if r["emv_value"] > 0]
        check(f"{label}: every channel that was priced is in the table ({len(priced)} rows, LinkedIn among them)",
              len(rows) == len(priced) and any(r[0] == "LinkedIn" for r in rows), [r[0] for r in rows])
        table_sum = round(sum(money(r[3]) for r in rows), 2)
        printed = money(after(emv, "Total earned media"))
        check(f"{label}: the table sums to its total row, and that is the earned media tile",
              abs(table_sum - printed) < 0.005 and abs(printed - tiles["EARNED MEDIA"]) < 0.005,
              (table_sum, printed, tiles.get("EARNED MEDIA")))
        check(f"{label}: each row is people times our rate",
              all(abs(int(r[1].replace(",", "")) * money(r[2]) - money(r[3])) < 0.005 for r in rows))
        # the calculators
        bev = page_named(pg, "Brand engagement")
        people = int(after(bev, f"{BRAND} engagers after launch").replace(",", ""))
        rate = money(after(bev, "x our 2026 value per engaged person"))
        check(f"{label}: brand engagement is people times rate, and is its tile",
              abs(people * rate - money(after(bev, "BRAND ENGAGEMENT VALUE"))) < 0.005
              and abs(people * rate - tiles["BRAND ENGAGEMENT"]) < 0.005, (people, rate))
        blv = page_named(pg, "Brand lift")
        rate = money(after(blv, "x our 2026 value per new engaged person"))
        if doc["control_group"].get("enabled"):
            gain = int(after(blv, f"More {BRAND} engagers among those reached").replace(",", ""))
            matched = int(after(blv, "Less the same gain in the matched group").replace(",", ""))
            new = int(after(blv, "New people beyond the matched group").replace(",", ""))
            check(f"{label}: the gain less the matched group's gain is the new people", gain - matched == new,
                  (gain, matched, new))
        else:
            new = int(after(blv, f"New {BRAND} engagers since the campaign began").replace(",", ""))
        check(f"{label}: brand lift is new people times rate, and is its tile",
              new == v["incremental_users"] and abs(new * rate - money(after(blv, "BRAND LIFT VALUE"))) < 0.005
              and abs(new * rate - tiles["BRAND LIFT"]) < 0.005, (new, rate))
        cv = page_named(pg, "Sales")
        check(f"{label}: the conversion page is there (it is in the total)", bool(cv))
        orders = int(after(cv, "Orders in the window").replace(",", ""))
        aov = money(after(cv, "x average order value"))
        check(f"{label}: conversion is orders times the average order value, and is its tile",
              abs(orders * aov - money(after(cv, "CONVERSION VALUE"))) < 0.005
              and abs(orders * aov - tiles["CONVERSION"]) < 0.005, (orders, aov))
    # automotive: purchases are not visible, so the lens and its dollars both stay out
    with contextlib.redirect_stdout(io.StringIO()):
        pg = pages(B.build_presentation(DOC_PLAIN, category="automotive"))
    hero = page_named(pg, "Total value")
    tiles = [money(after(hero, lens)) for lens in ("EARNED MEDIA", "BRAND ENGAGEMENT", "BRAND LIFT")]
    total = money(next(t for t in hero if MONEY.match(t)))
    check("automotive: no conversion tile, no conversion page, and the total is the three lenses shown",
          "CONVERSION" not in hero and not page_named(pg, "Sales") and abs(sum(tiles) - total) < 0.005
          and abs(total - (DOC_PLAIN["valuation"]["total_brand_value"] - DOC_PLAIN["valuation"]["conversion_value"])) < 0.005,
          (tiles, total))


# ------------------------------------------------------------------ [4]
def test_dollars_exact():
    print("[4] valuation dollars print exact, with cents")
    check("the formatter always prints cents", B.fmt_money(12030) == "$12,030.00" and B.fmt_money(604875.0) == "$604,875.00"
          and B.fmt_money(11335.5) == "$11,335.50" and B.fmt_money("n/a") == "$0.00")
    for label, _doc, _prs, pg, blob in DECKS:
        short = [m.group(0) for m in re.finditer(r"\$\d[\d,]*(?:\.\d+)?\s?[KMB]\b|\$\d[\d,.]*\s?(?:million|thousand|billion)", blob)]
        check(f"{label}: no abbreviated dollar figure ($12K, $1.2M)", not short, short)
        bare = []
        for texts in pg:
            if texts and re.fullmatch(r"\d\d\s+WHO ENGAGED.*", texts[0]):
                continue                                   # income bands are labels, not valuation dollars
            for t in texts:
                for m in re.finditer(r"\$(\d[\d,]*)(\.\d\d)?", t):
                    if not m.group(2) and int(m.group(1).replace(",", "")) >= 1000:
                        bare.append(m.group(0))
        check(f"{label}: every dollar figure of $1,000 or more carries its cents", not bare, bare[:6])
        check(f"{label}: no rounded count (19K people, 2.4M)", not re.search(r"\b\d+(?:\.\d+)?[KM]\b(?!\+)", blob.replace("30M+", "")),
              re.findall(r"\b\d+(?:\.\d+)?[KM]\b", blob.replace("30M+", ""))[:4])


# ------------------------------------------------------------------ [5]
def test_short_version():
    print("[5] the short version is the second page")
    for label, doc, _prs, pg, _blob in DECKS:
        tl = pg[1]
        check(f"{label}: page two is the short version", tl[0] == "02 THE SHORT VERSION", tl[:2])
        labels = [t for t in tl if t in ("WHAT WE FOUND", "WHERE THE VALUE SITS", "WHAT IS NEW", "WHAT SOLD", "WHAT TO DO")]
        check(f"{label}: three to five lines: what we found, why it matters, what to do",
              3 <= len(labels) <= 5 and labels[0] == "WHAT WE FOUND" and labels[-1] == "WHAT TO DO", labels)
        check(f"{label}: its headline states the total",
              any(B.fmt_money(doc["valuation"]["total_brand_value"]) in t and ("delivered" in t or "so far" in t) for t in tl),
              tl[:6])
    # the opening line follows the window (2026-11-11): the value so far while the campaign runs or on
    # the day the window ends; "delivered" for a window that closed before the valuation was made
    import copy as _copy
    doc = DECKS[0][1]
    money = B.fmt_money(doc["valuation"]["total_brand_value"])
    end = str((doc.get("post_period") or {}).get("end") or doc.get("end_date"))[:10]
    for name, change, want in (
            ("a closed past window", {"created_at": "2099-01-01T00:00:00Z", "windows": {}}, "delivered"),
            ("a window that ends the day it was valued", {"created_at": end + "T18:00:00Z", "windows": {}}, "so far"),
            ("a campaign the order said was still running", {"created_at": "2099-01-01T00:00:00Z", "windows": {"open": True}},
             "so far")):
        d = _copy.deepcopy(doc)
        d.setdefault("diagnostics", {}).pop("post_window_clipped_to_today", None)
        d.update(change)
        with contextlib.redirect_stdout(io.StringIO()):
            head = pages(B.build_presentation(d, category="FASHION"))[1]
        line = next((t for t in head if money in t), "")
        check(f"{name}: the opening line says '{want}'", want in line and len(line.split()) <= 12
              and ("delivered" in line) != ("so far" in line), line)


# ------------------------------------------------------------------ [6]
def test_cover():
    print("[6] the cover: the pairing, a claim, DRAFT only on a draft")
    for label, doc, prs, pg, _blob in DECKS:
        cover = pg[0]
        check(f"{label}: the title is the pairing in the product's own order",
              doc["project_name"] == f"{TALENT} x {BRAND}" and doc["project_name"] in cover, cover[:3])
        check(f"{label}: a plain claim line under it",
              f"A look at the value {TALENT} brought to {BRAND} in 51 days." in cover, cover)
        check(f"{label}: no DRAFT stamp on a valuation the product saved", not any("DRAFT" in t for t in cover), cover)
        pics = [sh for sh in prs.slides[0].shapes if sh.shape_type == 13]
        check(f"{label}: the real logo, at its own proportions",
              len(pics) == 1 and abs((pics[0].width / pics[0].height) - (4736 / 600)) < 0.05,
              [(p.width, p.height) for p in pics])
        bg = prs.slides[0]._element.xpath(".//p:bg//a:blip")
        check(f"{label}: the cover ground is the pack's gradient, set as the background (no type plate)",
              len(bg) == 1 and not any(Emu(sh.width).inches > 6 and sh.shape_type == 13 for sh in prs.slides[0].shapes))
    check("what decides DRAFT: the valuation says so", not B.is_draft(DOC_PLAIN) and B.is_draft({"draft": True})
          and B.is_draft({"status": "Draft"}) and not B.is_draft({"created_by": "canary:x", "created_at": "2026-11-11"}))
    with contextlib.redirect_stdout(io.StringIO()):
        cover = pages(B.build_presentation(dict(DOC_PLAIN, draft=True), category="FASHION"))[0]
    check("a valuation flagged as a draft says DRAFT on its cover", any("DRAFT" in t for t in cover), cover)


# ------------------------------------------------------------------ [7]
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def test_builder_traits():
    print("[7] the builder's own traits: fields, corners, type sizes, headlines")
    for label, _doc, prs, pg, _blob in DECKS:
        no_field, square, tiny, small_sentence, flat_titles, long_titles = [], [], [], [], [], []
        for i, s in enumerate(prs.slides, start=1):
            xml = s._element.xml
            if 'type="slidenum"' not in xml:
                no_field.append(i)
            for sh in s.shapes:
                if sh.shape_type == 1 and not (sh.has_text_frame and sh.text_frame.text.strip()):
                    geom = sh._element.spPr.find(f"{A_NS}prstGeom")
                    filled = sh._element.spPr.find(f"{A_NS}solidFill") is not None
                    if (geom is not None and geom.get("prst") == "rect" and filled
                            and Emu(sh.width).inches > 0.3 and Emu(sh.height).inches > 0.3):
                        square.append((i, round(Emu(sh.left).inches, 2), round(Emu(sh.top).inches, 2)))
                    if sh._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style") is not None:
                        square.append((i, "theme style left on a drawn shape"))
                if not sh.has_text_frame:
                    continue
                top = Emu(sh.top).inches
                for p in sh.text_frame.paragraphs:
                    runs = [r for r in p.runs if r.text.strip()]
                    for r in runs:
                        size = r.font.size.pt if r.font.size else 18
                        if size < 9:
                            tiny.append((i, r.text[:30], size))
                        if size < 10 and top < 6.45 and len(r.text.split()) >= 12 and r.text != r.text.upper():
                            small_sentence.append((i, r.text[:40], size))
                        if r.font.italic:
                            tiny.append((i, "italic", r.text[:20]))
                    if runs and (runs[0].font.size.pt if runs[0].font.size else 0) >= 28 and 0.8 < top < 1.6:
                        text = "".join(r.text for r in p.runs)
                        faces = {(r.font.name, bool(r.font.bold)) for r in runs}
                        heavy = any(b for _f, b in faces)
                        light = any("Light" in (f or "") for f, _b in faces)
                        if not (heavy and light):
                            flat_titles.append((i, text))
                        if len(text.split()) > 12 or not text.endswith(".") or len(re.findall(r"[.!?](?:\s|$)", re.sub(r"\d\.\d", "0", text))) != 1 \
                                or ":" in text:
                            long_titles.append((i, text))
        check(f"{label}: a page-number field on every page", not no_field, no_field)
        check(f"{label}: every filled shape is rounded and carries no theme style", not square, square[:4])
        check(f"{label}: no type under 9pt, no italics", not tiny, tiny[:4])
        check(f"{label}: no sentence at 9pt in the content area", not small_sentence, small_sentence[:4])
        check(f"{label}: every headline is a bold claim with a light close", not flat_titles, flat_titles[:3])
        check(f"{label}: every headline is one sentence of twelve words or fewer, ending in a period",
              not long_titles, long_titles[:3])
        check(f"{label}: the footer lockup is the house line",
              all("CROSSWALK / BEHAVIORAL INTELLIGENCE ENGINE" in texts for texts in pg[1:]))
        issues = B.check_layout(prs)
        check(f"{label}: the layout self-check finds nothing", not issues, issues[:3])
    faces = {r.font.name for s in PRS_PLAIN.slides for sh in s.shapes if sh.has_text_frame
             for p in sh.text_frame.paragraphs for r in p.runs}
    check("every run is set in the house family", all(str(f).startswith("Crosswalk Inter") for f in faces), faces)


# ------------------------------------------------------------------ [8]
def test_campaign_pages_have_no_history():
    print("[8] a page the campaign created has no visits before it existed")
    kw = dict(talent=[TALENT], campaign=[CAMPAIGN], pre_end="2026-08-01", drop_dates=["2026-09-22"],
              event_start="2026-09-22")
    check("the rule: a brand page that names the talent, the campaign page and the announcement are the campaign's own",
          all(CA.is_campaign_asset(n, talent=[TALENT], campaign=[CAMPAIGN]) for n in (OWN_PAGE, TOUR_PAGE, RELEASE))
          and not any(CA.is_campaign_asset(n, talent=[TALENT], campaign=[CAMPAIGN])
                      for n in ("fernhill.com homepage", "Fernhill on Instagram (@fernhill)", "fernhill.com/browse/women")))
    check("the rule: no history when the before window ends ahead of the campaign's first public day",
          CA.no_history_before(OWN_PAGE, **kw) and not CA.no_history_before("fernhill.com homepage", **kw))
    check("the rule: a page live inside the before window keeps its count",
          not CA.no_history_before(OWN_PAGE, **dict(kw, pre_end="2026-09-21", drop_dates=["2026-09-15"])))
    check("the rule: the research can mark a page new; a missing date never zeroes a count",
          CA.no_history_before("fernhill.com/lookbook", flag=True, **kw)
          and not CA.no_history_before(OWN_PAGE, talent=[TALENT], campaign=[CAMPAIGN], pre_end=None))
    # the engine
    pre = {r["common_name"]: r for r in DOC_PLAIN["top_brand_properties_pre"]}
    post = {r["common_name"]: r for r in DOC_PLAIN["top_brand_properties"]}
    check("engine: the three campaign pages carry no before count, and are marked",
          all(pre[n]["hits"] == 0 and pre[n]["hits_projected"] == 0 and post[n].get("new_in_window")
              for n in (OWN_PAGE, TOUR_PAGE, RELEASE)), [(n, pre[n]) for n in (OWN_PAGE, TOUR_PAGE, RELEASE)])
    check("engine: the brand's standing pages keep theirs",
          pre["fernhill.com homepage"]["hits_projected"] > 0 and not post["fernhill.com homepage"].get("new_in_window"))
    early = valuation(False, drop_dates=("2026-08-20", "2026-09-22"))
    pre_e = {r["common_name"]: r for r in early["top_brand_properties_pre"]}
    check("engine: a collection that dropped inside the before window keeps its before count",
          pre_e[OWN_PAGE]["hits_projected"] > 0)
    check("engine: the research prompt asks for it", "Never give a page a history" in S.research_prompt())
    # the deck, on the engine's valuation and on one saved before the engine was fixed
    for label, doc in (("engine valuation", DOC_PLAIN), ("valuation saved before the fix", legacy(DOC_PLAIN))):
        with contextlib.redirect_stdout(io.StringIO()):
            tp = page_named(pages(B.build_presentation(doc, category="FASHION")), f"Where people met {BRAND}")
        i = tp.index(OWN_PAGE)
        check(f"deck, {label}: the campaign's page shows Not live before and New, never a before count or a 0",
              tp[i + 1] == "Not live" and tp[i + 3] == "New" and "2,211" not in " ".join(tp) and "0" not in tp, tp[i:i + 4])
        check(f"deck, {label}: the page says what Not live means",
              any("Not live marks a page or account that did not exist before the launch." in t for t in tp))


# ------------------------------------------------------------------ [9]
def test_labels_as_written():
    print("[9] labels come out as written")
    for label, _doc, _prs, pg, blob in DECKS:
        check(f"{label}: sentence-case buckets and day counts",
              "17 and under" not in blob.lower() or "17 and under" in blob, "")
        # (the earned-media channel is the brand's site by the name a reader knows, 'Fernhill.com and app',
        # 2026-11-11; a page the valuation lists still prints as the valuation names it)
        channel = f"{BRAND}.com and app"
        bad = re.findall(r"\b\d+ Days\b|\b\d+ And\b|\bOr Older\b|\bOr More\b|\bNot To Say\b|\b[A-Z][a-z]+\.com\b",
                         blob.replace(channel, ""))
        check(f"{label}: nothing re-capitalized ('51 Days', '17 And under', 'Gap.com')", not bad, bad[:5])
        check(f"{label}: the brand's own site is a channel by the name a reader knows", channel in blob
              and f"{BRAND} site and app" not in blob and "Direct (" not in blob and "Twitter" not in blob)
        check(f"{label}: the reach page says what engaging means, once", blob.count(B.ENGAGING_MEANS) == 1)
        check(f"{label}: a touchpoint prints as the valuation names it", OWN_PAGE in blob and "fernhill.com homepage" in blob)
    check("bucket labels read in sentence case, names keep their capitals",
          B._bucket_label("17 and Under") == "17 and under" and B._bucket_label("65 or Older") == "65 or older"
          and B._bucket_label("Prefer Not to Say") == "Prefer not to say"
          and B._bucket_label("$25,000 - $49,999") == "$25,000 to $49,999"
          and B._bucket_label("$250,000 or More") == "$250,000 or more"
          and B._bucket_label("Hispanic or Latino") == "Hispanic or Latino"
          and B._bucket_label("Black or African American") == "Black or African American"
          and B._bucket_label("Another Race/Ethnicity") == "Another race or ethnicity")


# ------------------------------------------------------------------ [10]
def _texts_of(path):
    return [" ".join(sh.text_frame.text.split()) for s in Presentation(path).slides for sh in s.shapes
            if sh.has_text_frame and sh.text_frame.text.strip()]


def test_ready_is_the_truth():
    print("[10] READY is the whole truth, and both decks reach it with nothing to repair")
    try:
        from migration import design_skill as ds
    except Exception as e:  # noqa: BLE001
        check("design_skill imports", False, repr(e))
        return
    # the verdict, with each check stubbed: any FAIL is NOT READY
    real = {n: getattr(ds, n) for n in ("scrub_doors_in_file", "preship_fix", "embed_fonts", "brand_lint", "readability")}

    def stub(name, ok):
        def fn(path, report=None, **_kw):
            report.add(name, ok, "")
            return report
        return fn

    tmp = tempfile.mkdtemp(prefix="bpiq_ready_")
    probe = os.path.join(tmp, "probe.pptx")
    PRS_PLAIN.save(probe)
    try:
        for label, embed, lint, read, want in (("all clean", True, True, True, True),
                                               ("a lint FAIL", True, False, True, False),
                                               ("a plain-English flag", True, True, False, False),
                                               ("fonts not embedded", False, True, True, False)):
            ds.scrub_doors_in_file = stub("door_scrub", True)
            ds.preship_fix = stub("preship_fix", True)
            ds.embed_fonts = stub("embed", embed)
            ds.brand_lint = stub("brand_lint", lint)
            ds.readability = stub("readability", read)
            with contextlib.redirect_stdout(io.StringIO()):
                rep = ds.finish(probe)
            check(f"verdict with {label}: {'READY' if want else 'NOT READY'}",
                  rep.ready is want and ("NOT READY" in rep.summary) == (not want), rep.summary)
    finally:
        for n, fn in real.items():
            setattr(ds, n, fn)
    if not ds.available():
        print("  skip  the crosswalk-design pack is not on this machine; the real finish is not run")
        return
    try:
        import fontTools  # noqa: F401
    except Exception:
        print("  skip  fonttools is not installed here; the real finish is not run")
        return
    # a deck with a headline the plain-English check flags is never READY
    bad = Presentation()
    bad.slide_width, bad.slide_height = B.SLIDE_W, B.SLIDE_H
    for k in range(2):
        s = B._blank(bad)
        B._ground(s, B.S_DARK)
        B._head(s, B.S_DARK, k + 1, "A page", ("One claim. Two claims.", " And a third one that runs well past twelve words in all."),
                None, None)
    bad_path = os.path.join(tmp, "bad.pptx")
    bad.save(bad_path)
    with contextlib.redirect_stdout(io.StringIO()):
        rep = ds.finish(bad_path)
    check("a real deck with a flagged headline: NOT READY, and it says why",
          not rep.ready and not rep.copy_ok and "readability: FAIL" in rep.summary, rep.summary)
    # the two valuation decks, through the delivery path
    for label, doc, _prs, _pg, _blob in DECKS:
        with contextlib.redirect_stdout(io.StringIO()):
            data, rep = B.build_deck_report(doc, category="FASHION")
        check(f"{label}: a deck comes back with its report", data[:2] == b"PK" and rep is not None)
        if rep is None:
            continue
        steps = {n: ok for n, ok, _o in rep.steps}
        check(f"{label}: READY (Crosswalk Inter embedded, lint clean, plain-English clean)",
              rep.ready and steps.get("embed") and steps.get("brand_lint") and rep.copy_ok, rep.summary)
        check(f"{label}: brand lint: 0 FAIL, 0 WARN", rep.counts.get("lint_fail") == 0 and rep.counts.get("lint_warn") == 0,
              [o for n, _ok, o in rep.steps if n == "brand_lint"][-1][-600:])
        check(f"{label}: plain-English check: 0 flags", rep.counts.get("readability_flags") == 0, rep.counts)
        check(f"{label}: nothing for the finisher to repair", not rep.fixes, rep.fixes)
        out = os.path.join(tmp, f"{'control' if doc['control_group'].get('enabled') else 'plain'}.pptx")
        Path(out).write_bytes(data)
        texts = _texts_of(out)
        blob = "\n".join(texts)
        check(f"{label}: the finished file reads as the built one (no label re-cased, nothing bound)",
              "\u00a0" not in blob and not re.findall(r"\b\d+ Days\b|\b\d+ And\b", blob)
              and "51 days" in blob and "after launch" in blob.lower())
        check(f"{label}: the finished file still passes the reader guard", not B.reader_blocked(blob), B.reader_blocked(blob)[:4])
    # the finisher's widow net reads a two-weight headline run by run
    prs = Presentation()
    prs.slide_width, prs.slide_height = B.SLIDE_W, B.SLIDE_H
    s = B._blank(prs)
    B._ground(s, B.S_LIGHT)
    B._head(s, B.S_LIGHT, 1, "Brand lift", ("The lift over the matched group", " is worth $421,610.00."), None, None)
    s2 = B._blank(prs)
    B._ground(s2, B.S_DARK)
    B._head(s2, B.S_DARK, 2, "Brand lift", ("New engagers since launch", " are worth $434,805.00."), None, None)
    head_path = os.path.join(tmp, "head.pptx")
    prs.save(head_path)
    before = _texts_of(head_path)
    got = ds.fix_widows_in_pptx(head_path)
    check("a headline set as a bold claim and a light close is left as the builder set it",
          _texts_of(head_path) == before and not any(got.values()), got)


# ------------------------------------------------------------------ [12]
def test_talent_audience_named_once():
    print("[12] the talent's own audience is named once beside the reach, and no count moves")
    from migration import bpiq_anchors as BA
    doc = copy.deepcopy(DOC_PLAIN)
    reach = int(doc["projected_audience_size"])
    held = reach * 7 + 3
    BA.label_people(doc, {"talent_audience": held}, {"qualifier": doc.get("talent") or "Mara Vale"})
    check("the valuation carries the talent's audience beside the reach", (doc.get("campaign") or {}).get("talent_audience_held") == held)
    with contextlib.redirect_stdout(io.StringIO()):
        prs = B.build_presentation(doc, category="FASHION")
    pg = pages(prs)
    blob = "\n".join(t for texts in pg for t in texts)
    check("the deck names it once, on the who and when page", blob.count(f"{held:,}") == 1
          and any(f"projected US audience is {held:,}" in t for t in page_named(pg, "Who and when")), blob.count(f"{held:,}"))
    check("the reach on every page is still the campaign's", f"{reach:,} people" in blob)
    check("the page still fits and nothing is blocked", not B.check_layout(prs) and not B.reader_blocked(blob),
          str(B.check_layout(prs))[:200])
    check("a valuation with no such figure says nothing about it", "projected US audience is" not in BLOB_PLAIN)


# ------------------------------------------------------------------ [11]
def test_dashboard_note():
    print("[11] the dashboard's Brand Lift note names a matched group only when one was bought")
    html = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
    i = html.find('id="bpiqBlvMethodBody"')
    check("the note has an id the render can set", i > 0 and html.count('id="bpiqBlvMethodBody"') == 1)
    static = html[i:html.find("</div>", i)]
    check("the note loads with the before and after wording (no matched group in the page as served)",
          "before and after read of the same audience" in static and "matched" not in static and "exposed" not in static,
          static[:200])
    j = html.find("var blvMethod = document.getElementById('bpiqBlvMethodBody');")
    block = html[j:j + 1200]
    check("the render sets the matched-group wording only when the valuation has a control group",
          j > 0 and "blvMethod.innerHTML = cg.enabled" in block and block.index("matched group") < block.index("before and after read"),
          block[:160])
    check("no other static text on the page's cards names a matched group",
          html.count("gain of a matched group of the same size") == 1)


if __name__ == "__main__":
    test_dashboard_note()
    test_talent_audience_named_once()
    test_no_control_language()
    test_no_statistics_sources_or_method_words()
    test_pages_add_up()
    test_dollars_exact()
    test_short_version()
    test_cover()
    test_builder_traits()
    test_campaign_pages_have_no_history()
    test_labels_as_written()
    test_ready_is_the_truth()
    print(f"\n{'PASS' if not FAIL else f'{FAIL} FAIL'}: Brand Partnership valuation deck, clean out of the builder")
    sys.exit(1 if FAIL else 0)
