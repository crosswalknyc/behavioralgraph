#!/usr/bin/env python3
"""A deck leaves the builder clean, with no hand repair (2026-11-11).

Two decks built on 2026-11-11 came out of the builder marked READY while
the plain-English check failed, and nine defects were fixed by hand in the
finished files. Each one is held here against the builder and the finisher:

  [1] readability: a deck that fails the check on entry leaves
      design_skill.finish with zero flags. The flagged text is repaired
      in place (a rewriter pass over only the flagged units, then the
      fixed repairs); a rewrite that changes a figure is refused; a flag
      that cannot be repaired is reported as NOT READY, never as READY,
      and the file is still returned.
  [2] casing: a range reads "25 to 34" and a label "Arts and Culture";
      the door scrub no longer raises the first letter of a line that
      opens on a number or of an address.
  [3] band labels and thresholds print exactly ("$100,000 or more",
      "$75,000 to $99,999"); counts are still made messy.
  [4] subheads: "Size." and "Paths." are replaced with the slide's own
      content or dropped; no one-word subhead reaches a page.
  [5] table cells: every cell ships whole, or shortened at a clause or a
      word, never inside a word, never on a joining word or loose comma.
  [6] cover photos: the scrim is set by the luminance behind the type; a
      photo too light for any scrim gives way to the gradient cover; the
      finisher's net deepens the scrim on a deck from any builder.
  [7] shares and bases: a share printed beside a count is the count over
      the base its label names; a mismatch is recomputed before the page
      is written, and counts are never changed.
  [8] stock phrasing: "does the work" is flagged by the check and
      replaced; a phrase on three pages is thinned to two.
  [9] cover photo candidates: one address counts once; an interface
      screenshot, an icon or clip-art site and a logo file are turned
      away; nothing left means the gradient cover.

Hermetic: no network, no model (the rewriter is a stub), the packaged
design pack inside bg-webapp.

Run: python3 scripts/test_deck_clean_no_hand_repair.py
"""
import copy
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ["PROMETHEUS_DECK_PHOTOS"] = "0"

import numpy as np  # noqa: E402
import deck_builder as db  # noqa: E402
import prometheus_analysis as pma  # noqa: E402
from migration import design_skill as ds  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402
from PIL import Image  # noqa: E402

FAIL = 0
PY = sys.executable
SCRIPTS = str(ds.scripts_dir() or "")
TMP = tempfile.mkdtemp(prefix="pm_deck_clean_")
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
WHITE = RGBColor(0xE9, 0xE8, 0xE1)
BODY = RGBColor(0x9A, 0xA0, 0x9B)
INK = RGBColor(0x0C, 0x16, 0x18)


def check(name, ok, detail=""):
    global FAIL
    print(("PASS" if ok else "FAIL"), name, detail if not ok else "")
    if not ok:
        FAIL += 1


def run_tool(script, path, *extra):
    r = subprocess.run([PY, os.path.join(SCRIPTS, script), path, *extra], capture_output=True, text=True, timeout=600)
    return (r.stdout or "") + (r.stderr or "")


def flags_of(path):
    out = run_tool("readability_check.py", path)
    m = re.search(r"(\d+) flag\(s\)", out)
    return (int(m.group(1)) if m else -1), out


def texts_of(path):
    out = []
    for si, s in enumerate(Presentation(path).slides, 1):
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                out.append((si, re.sub(r"\s+", " ", sh.text_frame.text.replace("\u00a0", " ")).strip()))
    return out


def photo(mean=120, spread=70, size=(1600, 900), seed=3):
    """A picture with the statistics of a photograph: soft shapes, many colors."""
    rng = np.random.default_rng(seed)
    small = rng.integers(max(0, mean - spread), min(255, mean + spread) + 1, size=(18, 32, 3), dtype=np.uint8)
    im = Image.fromarray(small, "RGB").resize(size, Image.BICUBIC)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def box(slide, x, y, w, h, text, size=11.5, color=INK, bold=False):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    r = tf.paragraphs[0].add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.name = "Crosswalk Inter"
    r.font.color.rgb = color
    return tb


def blank_deck():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    return prs


def page(prs, fill=RGBColor(0xE9, 0xE8, 0xE1)):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = fill
    return s


if not SCRIPTS or ds.copy_fix_module() is None:
    print("skip: the crosswalk-design pack (with cw_copy_fix.py) is not on this machine")
    sys.exit(0)
cf = ds.copy_fix_module()

# ============================================================== [1] readability on entry -> zero flags on exit
print("[1] a deck that fails readability on entry leaves the finisher with zero flags")
PHRASE = "hold a four year degree or better"
LONG = ("Creative naming the hosts reads 2.41% CTR across every placement we tracked in the window, and journeys "
        "with the special on the path convert at 11.1% against 3.6% for sessions that never touch the talent at any step.")


def failing_deck(path):
    """A deck from 'another builder' carrying every kind of flag the check raises."""
    prs = blank_deck()
    s1 = page(prs)
    box(s1, 0.92, 1.05, 11.4, 0.6, "This audience is small in count and premium in behavior.", 30, bold=True)
    box(s1, 0.92, 2.4, 5.2, 0.8, f"52.1% are under 35 and 68.5% {PHRASE}. Income at $100,000 or more reads 45.5%.")
    box(s1, 6.6, 2.4, 5.2, 0.8, "First-impression clicking at 38.2% means the first exposure does the work, and 26.4% return inside 90 days.")
    s2 = page(prs)
    box(s2, 0.92, 1.05, 11.4, 0.6, "They click early: where the money hides?", 30, bold=True)
    box(s2, 0.92, 2.4, 3.4, 1.2, "38.2% of engagers who ever clicked did it on the first unit served to them in the window.")
    box(s2, 4.7, 2.4, 3.4, 1.2, "26.4% of completed buyers placed a second order with the same retailer within 90 days.")
    box(s2, 8.5, 2.4, 3.4, 1.2, "11.1% of sessions with the special on the path converted to a purchase inside the day.")
    box(s2, 0.92, 4.4, 11.0, 0.9, LONG)
    s3 = page(prs)
    box(s3, 0.92, 1.05, 11.4, 0.6, "They cart the same day and come back within ninety days.", 30, bold=True)
    box(s3, 0.92, 2.4, 6.0, 0.6, f"Education, 68.5%. Viewers here {PHRASE}, against a peer median of 24.1%.")
    box(s3, 0.92, 3.4, 6.0, 0.6, "She opens the door to new viewers in the 25 to 34 band, the largest in the file.")
    s4 = page(prs)
    box(s4, 0.92, 1.05, 11.4, 0.6, "They buy beauty, travel and entertainment above the average.", 30, bold=True)
    box(s4, 0.92, 2.4, 6.0, 0.6, f"The premium read in one line: 68.5% {PHRASE} and 45.5% earn $100,000 or more.")
    box(s4, 0.92, 3.4, 6.0, 0.6, "We leverage the avid tier of 160,529 viewers as the anchor for every buy in this plan.")
    prs.save(path)


src = os.path.join(TMP, "failing.pptx")
failing_deck(src)
n0, out0 = flags_of(src)
kinds0 = "\n".join(l for l in out0.splitlines() if "FLAG" in l)
check("the fixture fails the check on entry with every kind of flag", n0 >= 7 and "appears in 3 places" in out0
      and "stock phrase" in out0 and "cards open on a number" in out0 and "consecutive headers open on 'they'" in out0
      and "colon reveal" in out0 and "rhetorical question" in out0 and "door metaphor" in out0 and " words: " in out0
      and "'leverage'" in out0, f"({n0})\n{kinds0}")

# (a) no rewriter: the fixed repairs alone
a = os.path.join(TMP, "fixed_by_rule.pptx")
shutil.copyfile(src, a)
rep = ds.finish(a, copy_repair=True)
n1, out1 = flags_of(a)
check("fixed repairs alone: the pack's check reports zero flags on the finished file", n1 == 0,
      "\n".join(l for l in out1.splitlines() if "FLAG" in l))
check("fixed repairs alone: finish is READY, copy_ok, and says how many flags it repaired",
      rep.ready and rep.copy_ok and rep.counts.get("readability_flags") == 0
      and rep.fixes.get("copy_flags_before", 0) >= 6 and rep.fixes.get("copy_units_repaired", 0) >= 5
      and "readability: ok" in rep.summary and "repaired in place" in rep.summary and "READY" in rep.summary
      and "NOT READY" not in rep.summary, rep.summary + " " + str(rep.fixes))
before, after = texts_of(src), texts_of(a)
figs = lambda t: sorted(re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", t))  # noqa: E731
changed = [(o, n) for (_i, o), (_j, n) in zip(before, after) if o != n]
check("fixed repairs alone: the deck keeps its shape (same boxes) and only flagged lines changed",
      len(before) == len(after) and 5 <= len(changed) <= 12, f"{len(before)} {len(after)} {len(changed)}")
all_after = " ".join(t for _i, t in after)
check("fixed repairs alone: the long sentence is two sentences of 30 words or fewer, every figure kept",
      all(len(s.split()) <= 30 for s in re.split(r"(?<=[.!?])\s+", all_after))
      and all(f in all_after for f in ("2.41%", "11.1%", "3.6%")))
check("fixed repairs alone: the phrase used three times reads on two at most",
      all_after.lower().count(PHRASE) <= 2 and all_after.count("68.5%") == 3, str(all_after.lower().count(PHRASE)))
check("fixed repairs alone: no stock filler, no door, no jargon left",
      "does the work" not in all_after and not re.search(r"\bdoors?\b", all_after, re.I) and "leverage" not in all_after.lower())
check("fixed repairs alone: a header is one plain sentence (no colon, no question)",
      not any(":" in t or t.endswith("?") for i, t in after if t.startswith("They click early")), str([t for i, t in after if "click early" in t]))

# (b) a rewriter: only the flagged units go to it; a rewrite that changes a figure is refused
b = os.path.join(TMP, "fixed_by_rewriter.pptx")
shutil.copyfile(src, b)
calls = []


def stub_rewriter(items):
    calls.append(items)
    out = {}
    for it in items:
        t = it["text"]
        if "does the work" in t:
            out[it["id"]] = t.replace("means the first exposure does the work", "means one exposure earns the click")
        elif PHRASE in t and "premium read" in t:
            out[it["id"]] = "The premium read in one line: 68.5% finished college and 45.5% earn $100,000 or more."
        elif PHRASE in t:
            # a rewrite that drops a figure: must be refused, the fixed repair takes it instead
            out[it["id"]] = "Viewers here finished college at a high rate against their peers."
        elif t.startswith("26.4% of completed buyers"):
            out[it["id"]] = "Within 90 days, 26.4% of completed buyers placed a second order with the same retailer."
    return out


rep_b = ds.finish(b, copy_repair=True, copy_rewriter=stub_rewriter)
n2, out2 = flags_of(b)
sent = [it for c in calls for it in c]
check("rewriter: called no more than twice, and only with flagged units, each with its problems and its figures",
      1 <= len(calls) <= 2 and sent and all(it.get("problems") and "keep" in it and it.get("role") in ("header", "body") for it in sent)
      and not any(it["text"].startswith(("This audience is small", "They cart the same day")) for it in sent),
      f"{len(calls)} calls, {len(sent)} items")
tb = " ".join(t for _i, t in texts_of(b))
check("rewriter: an acceptable rewrite is written into the deck", "means one exposure earns the click" in tb
      and "68.5% finished college and 45.5% earn $100,000 or more." in tb)
check("rewriter: a rewrite that drops a figure is refused (the figure is still on the page)",
      "finished college at a high rate against their peers" not in tb and "24.1%" in tb)
check("rewriter: what it left is repaired by rule, zero flags, READY", n2 == 0 and rep_b.ready
      and rep_b.fixes.get("copy_units_rewritten", 0) >= 2, "\n".join(l for l in out2.splitlines() if "FLAG" in l) + rep_b.summary)

# (c) the status is the truth: a flag nothing can repair is NOT READY, and the file still ships
c = os.path.join(TMP, "no_headers.pptx")
prs = blank_deck()
for _k in range(2):
    s = page(prs)
    box(s, 0.92, 2.0, 8.0, 0.5, "A page of body copy with no header on it at all, set at body size.", 11.5)
prs.save(c)
rep_c = ds.finish(c, copy_repair=True)
check("an unrepairable flag: NOT READY in the summary with the count left, never READY",
      (not rep_c.ready) and (not rep_c.copy_ok) and rep_c.summary.count("NOT READY") == 1
      and "readability: FAIL (1 flag(s) left)" in rep_c.summary, rep_c.summary)
check("an unrepairable flag: the file is not held (it is on disk, opens, fonts embedded)",
      os.path.getsize(c) > 0 and len(Presentation(c).slides) == 2
      and any(n == "embed" and ok for n, ok, _o in rep_c.steps))
rec = rep_c.job_record()
check("the job record carries the same truth", rec["ready"] is False and rec["copy_ok"] is False
      and rec["steps"].get("readability") == "FAIL" and rec["counts"].get("readability_flags") == 1, str(rec)[:300])
# a deck whose copy is data (a focus group export, verbatim answers) and an attachment at the mail door are
# checked and reported, never rewritten: that is the default, and the status is still the truth
d_ = os.path.join(TMP, "report_only.pptx")
shutil.copyfile(src, d_)
rep_d = ds.finish(d_)
check("without copy_repair the flags are reported, the copy is left alone, and the status is NOT READY",
      (not rep_d.ready) and (not rep_d.copy_ok) and "NOT READY" in rep_d.summary and "flag(s) left)" in rep_d.summary
      and "copy_fix" not in rep_d.summary and "casing_lowered" not in rep_d.fixes
      and [t for _i, t in texts_of(d_) if "does the work" in t] != [] and [t for _i, t in texts_of(d_) if LONG in t] != [],
      rep_d.summary)
gated, rep_g = ds.gate_attachment("attached.pptx", open(src, "rb").read())
gp = os.path.join(TMP, "gated.pptx")
open(gp, "wb").write(gated)
check("the mail door's gate never rewrites an attached deck's copy, and says what state it is in",
      (not rep_g.ready) and [t for _i, t in texts_of(gp) if "does the work" in t] != [] and "NOT READY" in rep_g.summary, rep_g.summary)
# every rule repair is checked before it is written: a cut that doubles a word is not made
twin = os.path.join(TMP, "norm_lines.pptx")
prs = blank_deck()
for kname in ("pilot", "comedy", "drama", "finale"):
    s = page(prs)
    box(s, 0.92, 1.05, 11.0, 0.6, f"The {kname} read for this room of twelve seats.", 30, bold=True)
    box(s, 0.92, 2.4, 9.0, 0.5, f"No {kname} norm yet; it fills as {kname} tests are filed, 0 of 5 so far.")
prs.save(twin)
ds.finish(twin, copy_repair=True)
tw = " ".join(t for _i, t in texts_of(twin))
check("a rule repair never doubles a word or leaves loose punctuation ('pilot pilot')",
      not re.search(r"\b([A-Za-z]{2,})\s+\1\b", tw, re.I) and not re.search(r",\s*[,.;]|\s[,.;:]", tw), tw[:500])
check("sane(): a cut that doubles a word, adds a figure or drops the full stop is refused",
      not cf.sane("No pilot norm yet; it fills as pilot tests are filed.", "No pilot pilot tests are filed.")
      and not cf.sane("Comedy leads at 32.6%.", "Comedy leads at 33.0%.") and not cf.sane("Comedy leads the five.", "Comedy leads the five")
      and cf.sane("Comedy leads the five, and Faith trails.", "Comedy leads the five."))

# (d) through the Prometheus builder: a plan whose copy flags comes out clean, laid out on the repaired words
PLAN_FLAGGED = {
    "title": "This audience is small in count and premium in behavior.", "filename_stem": "clean_fixture",
    "slides": [
        {"type": "cover", "eyebrow": "FIXTURE VIEWERS · AUDIENCE READ", "title": "This audience is small in count and premium in behavior.",
         "intro": "The viewer read for the fixture: who they are, what they buy and how they respond.",
         "stats": [{"big": "581,977", "label": "Projected US viewers"}, {"big": "11.1%", "label": "Conversion with the special on the path"},
                   {"big": "2.41%", "label": "CTR on talent-tagged units"}], "accent_index": 1},
        {"type": "argument", "ground": "dark", "eyebrow": "Argument", "title": "Four things to take from this audience.",
         "sub": "Who they are, what makes them rare, what they buy and what to run.",
         "cards": [{"head": "Small universe, dense core", "body": "581,977 US viewers, with an avid tier of 160,529 watching four or more times."},
                   {"head": "Young, credentialed, premium", "body": f"52.1% are under 35 and 68.5% {PHRASE}. Income at $100,000 or more reads 45.5%."},
                   {"head": "Culture spend", "body": "Entertainment purchase reads 77.4%, beauty 56.5% and travel 53.5% of this file."},
                   {"head": "Talent is the unit", "body": "Creative naming the hosts reads 2.41% CTR, and journeys with the special convert at 11.1%."}]},
        {"type": "tiles_facts", "eyebrow": "Universe", "title": "The audience is 581,977 viewers with a millennial core.",
         "sub": "Projected US people who watched in the window, and the shape underneath them.",
         "tiles": [{"big": "581,977", "label": "Projected US viewers"}, {"big": "160,529", "label": "Avid viewers, 27.6% of the audience"},
                   {"big": "57.6%", "label": "Female share of the audience"}],
         "facts": [{"label": "Largest age band", "fig": "32.5%", "note": "25 To 34, 188,901 people. 18 To 34 carries 52.1%."},
                   {"label": "Education", "fig": "68.5%", "note": f"Viewers {PHRASE}, against a peer median of 24.1% at high school."},
                   {"label": "Income band", "fig": "45.5%", "note": "Earn $100,000 or more; $75,000 to $99,999 adds 14.2%."}],
         "read": "Young and credentialed, with no children for 57.8% of the audience, so spend leans discretionary."},
        {"type": "tiles_row", "eyebrow": "Response", "title": "They click early, cart the same day and come back.",
         "sub": "What viewers did after a talent-tagged unit was served to them in the window.",
         "tiles": [{"big": "38.2%", "label": "Click on first impression", "body": "38.2% of engagers who ever clicked did it on the first unit served to them in the window."},
                   {"big": "4h 11m", "label": "Median time to cart", "body": "The median gap from click to cart start is 4 hours 11 minutes, inside the same viewing day."},
                   {"big": "26.4%", "label": "Repeat buyers in 90 days", "body": "26.4% of completed buyers placed a second order with the same retailer within 90 days."}],
         "read": "First-impression clicking at 38.2% means the first exposure does the work, and 26.4% return inside 90 days."},
        {"type": "hero", "ground": "accent", "eyebrow": "Signal", "title": "Talent-tagged units out-click the benchmark.",
         "sub": "Click-through rate on units that name the hosts.", "big": "2.41%",
         "line": "Units naming the hosts clear 2.41% CTR against 0.97% for general entertainment units.",
         "support": "The gap holds across every placement tracked in the window."},
        {"type": "table", "eyebrow": "Shape", "title": "Each category trades reach against depth differently.", "sub": "Size.",
         "columns": ["Category", "US viewers", "Share of viewing", "Platform lean", "Audience shape"],
         "rows": [["Comedy", "6,579,974", "32.6%", "YouTube 19.3 / TikTok 13.3", "18 To 34, male-leaning, a weekly habit"],
                  ["Lifestyle / Arts And Culture", "5,142,863", "25.4%", "YouTube 15.2 / TikTok 10.2", "Older, even on gender, highest income"],
                  ["Horror", "3,418,207", "16.9%", "YouTube 9.3 / TikTok 7.6", "Long sessions, late at night"],
                  ["Young Adult", "2,874,591", "14.2%", "TikTok 7.4 / YouTube 6.8", "Under 25, female-leaning"],
                  ["Faith", "2,196,478", "10.9%", "YouTube 7.1 / TikTok 3.8", "Daily by design, the stickiest of the five"]],
         "read": "Comedy and Lifestyle carry the reach at 58.0% of viewing between them."},
        {"type": "paths", "eyebrow": "Paths", "title": "Sessions run from a search to a play.", "sub": "Paths.",
         "rows": [{"kind": "search", "url": "google.com/search?q=hot+ones+new+season"}, {"kind": "click", "url": "youtube.com/@firstwefeast"},
                  {"kind": "play", "url": "youtube.com/watch?v=hot-ones-season-finale", "lit": True}]},
        {"type": "close", "eyebrow": "Close", "title": "A compact audience that pays back at every stage.",
         "cards": [{"head": "The scale is qualified", "body": "581,977 US viewers and an avid tier of 160,529 who came back four or more times."},
                   {"head": "The demo is premium", "body": f"Of this file, 68.5% {PHRASE} and 45.5% earn $100,000 or more."},
                   {"head": "The creative works", "body": "Talent-tagged units clear 2.41% CTR in the window against 0.97% for the benchmark."},
                   {"head": "The purchase follows", "body": "Sessions with the special on the path convert at 11.1% and buyers return inside 90 days."}]},
    ],
}
db._PHOTO_CACHE.clear()
plan = copy.deepcopy(PLAN_FLAGGED)
out = os.path.join(TMP, "builder.pptx")
n = db.render_insights_deck(plan, out, static_dir=os.path.join(ROOT, "static"))
fin = plan.get("_finish") or {}
nb, outb = flags_of(out)
lint = run_tool("cw_brand_lint.py", out)
emb = run_tool("cw_embed_fonts.py", out, "--verify")
check("builder: renders every slide", n == len(PLAN_FLAGGED["slides"]), str(n))
check("builder: zero readability flags on the finished deck (the pack's own check)", nb == 0,
      "\n".join(l for l in outb.splitlines() if "FLAG" in l))
check("builder: zero brand-lint FAIL, fonts embedded", re.search(r"\b0 failure\(s\)", lint) is not None and "READY" in emb,
      lint[-400:])
check("builder: the job record says ready, copy_ok, zero flags", fin.get("ready") is True and fin.get("copy_ok") is True
      and fin.get("counts", {}).get("readability_flags") == 0 and fin.get("steps", {}).get("readability") == "ok", str(fin)[:500])
check("builder: the repairs went into the plan before the final layout (the finisher had no copy left to repair)",
      not fin.get("fixes", {}).get("copy_units_repaired") and any("plain-English check" in x for x in fin.get("copy_notes") or []),
      str(fin.get("fixes")) + str(fin.get("copy_notes"))[:400])
check("builder: the caller's plan is not mutated", plan["slides"][3]["read"] == PLAN_FLAGGED["slides"][3]["read"]
      and plan["slides"][5]["sub"] == "Size.")
T = texts_of(out)
ALL = " ".join(t for _i, t in T)

# with a rewriter at hand a phrase on three pages goes to it; the closer is not rebuilt from figures by rule
seen_items = []


def phrase_rewriter(items):
    seen_items.extend(items)
    return {it["id"]: it["text"].replace(PHRASE, "finished college") for it in items if PHRASE in it["text"]}


plan_rw = copy.deepcopy(PLAN_FLAGGED)
out_rw = os.path.join(TMP, "builder_rewriter.pptx")
db.render_insights_deck(plan_rw, out_rw, static_dir=os.path.join(ROOT, "static"), copy_rewriter=phrase_rewriter)
t_rw = " ".join(t for _i, t in texts_of(out_rw))
check("builder with a rewriter: the flagged line goes to the model pass and its rewrite is laid out",
      any(PHRASE in it["text"] for it in seen_items) and "finished college" in t_rw and t_rw.lower().count(PHRASE) <= 2
      and plan_rw["_finish"].get("ready") is True and plan_rw["_finish"].get("counts", {}).get("readability_flags") == 0,
      str(plan_rw["_finish"])[:300])

# (e) the deck lane hands the finisher a model pass over only the flagged lines
chat_src = open(os.path.join(ROOT, "prometheus", "legacy", "chat.py"), encoding="utf-8").read()
check("deck lane: the deck job builds the rewriter from its own model call and passes it to the builder",
      "pma.make_deck_copy_rewriter(_pm_claude_json, usage_extras=_ppu_extras)" in chat_src
      and "copy_rewriter=_copy_rewriter)" in chat_src)
asked_model = []


def fake_call(system, user, **kw):
    asked_model.append((system, user, kw))
    return {"success": True, "data": {"rewrites": [{"id": "u1", "text": "One exposure earns the click, and 26.4% return inside 90 days."},
                                                   {"id": "u9", "text": 7}]}}


rw = pma.make_deck_copy_rewriter(fake_call, usage_extras={"user": "canary:deck"})
check("deck lane: under the hermetic flag the rewriter makes no model call", rw([{"id": "u1", "text": "x", "problems": ["p"]}]) == {}
      and asked_model == [])
os.environ.pop("REGRESSION_TEST_MODE", None)
try:
    got_rw = rw([{"id": "u1", "role": "body", "text": "The first exposure does the work, and 26.4% return inside 90 days.",
                  "problems": ["stock filler 'does the work': say what happened instead"], "keep": ["26.4%", "90"]}])
finally:
    os.environ["REGRESSION_TEST_MODE"] = "1"
check("deck lane: the rewriter sends the flagged lines with their problems and returns {id: text}",
      got_rw == {"u1": "One exposure earns the click, and 26.4% return inside 90 days."} and len(asked_model) == 1
      and "does the work" in asked_model[0][1] and "stock filler" in asked_model[0][1]
      and asked_model[0][2].get("surface") == "deck" and asked_model[0][2].get("usage_extras") == {"user": "canary:deck"}
      and "Keep every figure exactly as written" in asked_model[0][0], str(got_rw))
dark_only = {"title": "t", "slides": [copy.deepcopy(PLAN_FLAGGED["slides"][0]),
                                      dict(copy.deepcopy(PLAN_FLAGGED["slides"][4]), ground="dark")]}
out_d = os.path.join(TMP, "dark_hero.pptx")
db.render_insights_deck(dark_only, out_d, static_dir=os.path.join(ROOT, "static"))
check("builder: a plan with only dark statement pages still carries one color page (lint has no FAIL)",
      dark_only["_finish"].get("counts", {}).get("lint_fail") == 0 and dark_only["_finish"].get("ready") is True,
      str(dark_only["_finish"])[:400])

# ============================================================== [2] casing
print("[2] ranges and labels keep their small words small")
check("deck: '25 to 34' and '18 to 34' print lower case", "25 to 34, 188,901 people. 18 to 34 carries 52.1%." in ALL
      and "25 To 34" not in ALL and "18 To 34" not in ALL, str([t for _i, t in T if "188,901" in t]))
check("deck: a title-cased label reads 'Arts and Culture'", "Lifestyle / Arts and Culture" in ALL and "Arts And Culture" not in ALL)
check("deck: an address keeps its lower-case opening letter (never 'Google.com')",
      "google.com/search?q=hot+ones+new+season" in ALL and "Google.com" not in ALL and "Youtube.com" not in ALL)
for raw, want in (("25 To 34", "25 to 34"), ("$75,000 To $99,999", "$75,000 to $99,999"), ("Arts And Culture Fans", "Arts and Culture Fans"),
                  ("Comedy Vs Horror Viewers", "Comedy vs Horror Viewers"), ("Lord Of The Rings", "Lord of the Rings"),
                  ("Black Or African American", "Black or African American"),
                  ("The audience is young. The Office leads.", "The audience is young. The Office leads."),
                  ("To the point", "To the point"), ("OF THE AUDIENCE", "OF THE AUDIENCE")):
    check(f"fix_label_casing({raw!r})", ds.fix_label_casing(raw) == want, repr(ds.fix_label_casing(raw)))
for raw in ("25 to 34, 188,901 people.", "google.com/search?q=hot+ones", "18 to 34, male-leaning", "3 in 4 watch weekly"):
    check(f"door scrub leaves {raw!r} byte for byte", ds.scrub_door_metaphor(raw) == raw, repr(ds.scrub_door_metaphor(raw)))
check("door scrub still raises the letter when it removes the head of a capitalized line",
      ds.scrub_door_metaphor("01 The door to new viewers.") == "01 New viewers.", ds.scrub_door_metaphor("01 The door to new viewers."))
other = os.path.join(TMP, "casing_net.pptx")
prs = blank_deck()
s = page(prs)
box(s, 0.92, 1.05, 11.0, 0.6, "The largest band in this audience is the young one.", 30, bold=True)
box(s, 0.92, 2.4, 6.0, 0.4, "Ages 25 To 34, 188,901 people")
box(s, 0.92, 3.0, 6.0, 0.4, "Lifestyle / Arts And Culture")
prs.save(other)
rep_o = ds.finish(other, copy_repair=True)
to = " ".join(t for _i, t in texts_of(other))
check("finisher net: a deck from any builder has its ranges and labels lowered in place",
      "Ages 25 to 34, 188,901 people" in to and "Arts and Culture" in to and rep_o.fixes.get("casing_lowered", 0) >= 2,
      to + str(rep_o.fixes))

# ============================================================== [3] bands and thresholds
print("[3] band labels and thresholds print exactly; counts stay messy")
for raw in ("45.5% earn $100,000 or more.", "Income at $100,000 or more", "$75,000 to $99,999", "Under $25,000",
            "$50,000-$74,999", "between $50,000 and $75,000", "Household income over $150,000", "Accounts with 10,000+ followers",
            "25,000 to 49,999 followers", "Earn $100,000 or more"):
    check(f"threshold kept: {raw!r}", pma._messy_int_in_text("Fixture", raw) == raw, repr(pma._messy_int_in_text("Fixture", raw)))
# a dollar figure the deck uses as a band edge is that band edge wherever the deck writes it
ep2 = pma.enforce_insights_plan({"title": "t", "slides": [
    {"type": "split_stats_bars", "title": "Income skews up.", "stat_cards": [{"kicker": "Income", "big": "45.5%", "label": "earn $100,000 or more"}],
     "rows": [{"label": "$100,000 to $149,999", "value": 23.3}, {"label": "$75,000 to $99,999", "value": 17.5}],
     "read": "Spending power arrives early here: 45.5% sit past $100,000 while 52.1% are under 35, on $1,250,000 of tracked spend."}]}, "Fixture")
r2 = ep2["slides"][0]["read"]
check("a band edge is exact in running copy too ('45.5% sit past $100,000 while ...'), and a dollar count beside it is still messy",
      "sit past $100,000 while" in r2 and "$1,250,000" not in r2, r2)
check("'clear $100,000' reads as a threshold on its own, and a spend figure does not",
      pma._messy_int_in_text("Fixture", "45.5% clear $100,000 while 52.1% are under 35.") == "45.5% clear $100,000 while 52.1% are under 35."
      and pma._messy_int_in_text("Fixture", "spend $120,000") != "spend $120,000")
for raw in ("Reach of 4,190,000 viewers", "more than 1,250,000 viewers", "$1,500,000 in ad revenue", "A base of 14,860,000 people"):
    got = pma._messy_int_in_text("Fixture", raw)
    num = re.search(r"\d[\d,]{4,}", got).group(0)
    check(f"count still messy: {raw!r}", got != raw and not num.endswith("0"), got)
ep = pma.enforce_insights_plan({"title": "t", "slides": [{"type": "tiles_facts", "title": "Income.", "tiles": [
    {"big": "45.5%", "label": "Earn $100,000 or more"}], "facts": [{"label": "$75,000 to $99,999", "fig": "14.2%", "note": "the next band down"}]}]},
    "Fixture")
check("enforce_insights_plan leaves an income band exactly as written",
      ep["slides"][0]["tiles"][0]["label"] == "Earn $100,000 or more" and ep["slides"][0]["facts"][0]["label"] == "$75,000 to $99,999",
      str(ep["slides"][0]))
check("deck: the income threshold prints '$100,000' on every page that names it",
      ALL.count("$100,000") >= 3 and "$75,000 to $99,999" in ALL and not re.search(r"\$99,5\d\d|\$100,\d*[1-9]\b", ALL))

# ============================================================== [4] subheads
print("[4] a subhead is a sentence or a content label, never a one-word fragment")
subs = [(si, t) for si, t in T if t in ("Size.", "Paths.", "Size", "Paths")]
check("deck: 'Size.' and 'Paths.' are not on any page", not subs, str(subs))
check("deck: the table page's subhead names its columns",
      "US viewers, share of viewing, platform lean and audience shape for each category." in ALL
      or "Each category by US viewers, share of viewing, platform lean and audience shape." in ALL,
      str([t for si, t in T if si == 6][:6]))
check("deck: the paths page's subhead says what the rows are",
      "Example sessions from the window: search, then click, then play." in ALL
      or "What a session looks like, step by step: search, click, play." in ALL, str([t for si, t in T if si == 7][:6]))
sl = [{"type": "hero", "title": "One figure.", "sub": "Signal.", "big": "3.1x"}]
notes = db.retire_fragment_subs(sl)
check("a fragment with nothing to name is dropped, not kept", "sub" not in sl[0] and notes, str(sl))
tmpl = "{lead}, penetration of the audience with index against the average American."
sl2 = [{"type": "bars", "eyebrow": "Interests", "title": "A.", "sub": tmpl.format(lead="Stated interests"), "show_index": True,
        "rows": [{"label": "Comedy", "value": 57.9, "index": 194}, {"label": "Podcasts", "value": 56.8, "index": 239}]},
       {"type": "table", "eyebrow": "Size", "title": "B.", "sub": tmpl.format(lead="Size"),
        "columns": ["Category", "US viewers", "Share of viewing"], "rows": [["Comedy", "6,579,974", "32.6%"]]},
       {"type": "hero", "eyebrow": "Signal", "title": "C.", "sub": tmpl.format(lead="Signal"), "big": "3.1x"}]
db.dedupe_plan_copy(sl2)
check("a retired subhead template never leaves a fragment behind",
      all(len(str(x.get("sub") or "one two three").split()) >= 3 for x in sl2) and "sub" not in sl2[2]
      and sl2[1]["sub"] == "US viewers and share of viewing for each category.", str([x.get("sub") for x in sl2]))

# ============================================================== [5] table cells
print("[5] a table cell ships whole or shortened at a word, never cut")
cells = [c for row in PLAN_FLAGGED["slides"][5]["rows"] for c in row]
on_page = {t for si, t in T if si == 6}
want = [ds.fix_label_casing(c) for c in cells]
check("deck: every cell of the five-column table is on the page whole", all(c in on_page for c in want),
      str([c for c in want if c not in on_page]))
check("deck: no cell trails off ('YouTube 19.3 / TikTok', 'Daily by design, the', 'Under 25,')",
      not any(t.endswith((",", " the", "/ TikTok", "/ YouTube", " /")) for t in on_page), str(sorted(on_page))[:400])
sizes = set()
for sh in Presentation(out).slides[5].shapes:
    if sh.has_text_frame and re.sub(r"\s+", " ", sh.text_frame.text.replace("\u00a0", " ")).strip() in want:
        sizes |= {r.font.size.pt for p in sh.text_frame.paragraphs for r in p.runs if r.font.size}
check("deck: the table is set in one size from the type scale", len(sizes) == 1 and sizes <= set(db._TABLE_SIZES), str(sizes))
# a table that cannot fit even at the smallest size: shortened at a clause or a word
LONG_CELLS = [
    ("Eighteen to thirty four, male-leaning, a weekly habit built on sketch shows and late interviews that run past midnight",
     "Buy the long interview formats first, because the same viewers return every week and finish most of what they start"),
    ("Older than every other block, close to even on gender, with the highest incomes and the longest average visit of all",
     "Lead with performance and home tours, where brand fit is easiest and a single placement reaches two platforms at once"),
    ("Long sessions late at night, heavy on YouTube, split nearly evenly between series fans and one-time story seekers",
     "Treat the short cut as a trailer and place the message beside the payoff episode where attention is deepest overall"),
    ("Concentrated under twenty five, female-leaning, serialized stories taken vertically before anything longer is opened",
     "Plan vertical creative before any horizontal cut, since this group meets a story on a phone long ahead of a desktop"),
    ("Smallest of the group and the most loyal, a daily routine in the morning with a second visit on most evenings too",
     "Sponsor the daily devotional rather than a single premiere, as frequency here comes from routine and not from novelty"),
]
tight = {"title": "t", "slides": [{"type": "table", "eyebrow": "Shape", "title": "Each category trades reach against depth differently.",
                                   "sub": "Viewers and shape for each of the five categories in the window.",
                                   "columns": ["Category", "US viewers", "Audience shape", "What it means for a buyer"],
                                   "rows": [[f"Category {k + 1}", "6,579,974", a_, b_] for k, (a_, b_) in enumerate(LONG_CELLS)],
                                   "read": "Comedy and Lifestyle carry the reach.", "read2": "Faith is the smallest and the stickiest."}]}
out_t = os.path.join(TMP, "tight_table.pptx")
db.render_insights_deck(copy.deepcopy(tight), out_t, static_dir=os.path.join(ROOT, "static"))
page_t = [t for _i, t in texts_of(out_t)]
flat = [c for pair in LONG_CELLS for c in pair]


def as_shipped(cell):
    """The text on the page that opens the way this cell opens."""
    head = " ".join(cell.split()[:3])
    return next((t for t in page_t if t.startswith(head)), None)


def whole(cell, t):
    w, src_w = t.split(), cell.split()
    return w == src_w[:len(w)] and not t.endswith((",", ";", ":", "/", "...", "\u2026")) \
        and w[-1].lower().strip(",") not in db._TRAIL_WORDS


shipped = [(c, as_shipped(c)) for c in flat]
check("a cell too long for its box is shortened at a clause or a word boundary, never inside a word, no ellipsis",
      all(t is not None and whole(c, t) for c, t in shipped) and any(len(t) < len(c) for c, t in shipped)
      and all(len(t.split()) >= 3 for _c, t in shipped), str([t for _c, t in shipped][:4]))
check("_shorten_whole: clause first, then word, and a sentence keeps its full stop",
      db._shorten_whole("Daily by design, the stickiest of the five", lambda t: len(t) <= 22) == "Daily by design"
      and db._shorten_whole("This is a sentence that is far too long for the box it is in.", lambda t: len(t) <= 30) == "This is a sentence."
      and db._shorten_whole("Short.", lambda t: True) == "Short.")
check("values in one column share a precision ('3.0%' beside '2.4%', never '3%')",
      db._fmt_val(3.0, "%", db._row_decimals([{"value": 3.0}, {"value": 2.4}])) == "3.0%"
      and db._fmt_val(80, "%", db._row_decimals([{"value": 80}, {"value": 76}])) == "80%")

# ============================================================== [6] cover photos and the scrim
print("[6] the scrim is set by what is behind the type; too light a photo gives way to the gradient")
ZONES = [z + (tuple(db.BODY_DK),) for z in db._PHOTO_ZONES["cover"]]


def cover_bg(path):
    z = zipfile.ZipFile(path)
    slides, _sw, _sh = ds._slide_parts(z)
    from lxml import etree
    root = etree.fromstring(z.read(slides[0]))
    blip = root.find(f".//{NS_P}bg//{NS_A}blip")
    rels = z.read(slides[0].replace("slides/", "slides/_rels/") + ".rels").decode("utf8")
    rmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="\.\./media/([^"]+)"', rels))
    rmap.update({a_: b_ for b_, a_ in re.findall(r'Target="\.\./media/([^"]+)"[^>]*Id="(rId\d+)"', rels)})
    return Image.open(io.BytesIO(z.read("ppt/media/" + rmap[blip.get(f"{NS_R}embed")]))).convert("RGB")


def cover_deck(blob, name):
    db._PHOTO_CACHE.clear()
    db._BAKED_CACHE.clear()
    db._PHOTO_CACHE["cover fixture|title"] = [blob]
    os.environ["PROMETHEUS_DECK_PHOTOS"] = "1"
    try:
        p = {"title": "t", "image_subject": "Cover Fixture", "image_kind": "title", "slides": [copy.deepcopy(PLAN_FLAGGED["slides"][0])]}
        path = os.path.join(TMP, name)
        db.render_insights_deck(p, path, static_dir=os.path.join(ROOT, "static"))
        return path, p
    finally:
        os.environ["PROMETHEUS_DECK_PHOTOS"] = "0"
        db._PHOTO_CACHE.clear()


mid, light, white = photo(105, 70), photo(172, 40), photo(250, 5)
for label, blob, base in (("a mid-tone photo", mid, 0.64), ("a light photo", light, 0.64)):
    baked, used = ds.bake_photo_for_text(blob, ZONES, alpha=base)
    im = Image.open(io.BytesIO(baked))
    plain = Image.open(io.BytesIO(ds.bake_photo_background(blob, alpha=base)))
    check(f"{label}: body type clears 4.5:1 against the bright part behind it (alpha {used})",
          baked is not None and ds.zone_contrast(im, ZONES) >= ds.MIN_TEXT_CONTRAST - 0.15 and base <= used <= ds.MAX_SCRIM_ALPHA,
          f"contrast {ds.zone_contrast(im, ZONES):.2f} alpha {used}")
    if label == "a light photo":
        check("a light photo: the standing 64% scrim would have failed, so the scrim was deepened",
              ds.zone_contrast(plain, ZONES) < ds.MIN_TEXT_CONTRAST and used > base + 0.05,
              f"plain {ds.zone_contrast(plain, ZONES):.2f} used {used}")
b_white, u_white = ds.bake_photo_for_text(white, ZONES, alpha=0.64)
check("a photo too light for any scrim short of the cap: no photo comes back", b_white is None and u_white is None, str(u_white))
p_mid, _pm = cover_deck(mid, "cover_mid.pptx")
im_mid = cover_bg(p_mid)
check("deck: a mid-tone photo is the cover, scrimmed until the type reads",
      ds.image_detail(im_mid) >= ds.PHOTO_DETAIL_MIN and ds.zone_contrast(im_mid, ZONES) >= ds.MIN_TEXT_CONTRAST - 0.2,
      f"detail {ds.image_detail(im_mid):.2f} contrast {ds.zone_contrast(im_mid, ZONES):.2f}")
p_wh, _pw = cover_deck(white, "cover_white.pptx")
im_wh = cover_bg(p_wh)
grad = Image.open(io.BytesIO(ds.gradient_image_bytes("graphiteWash")))
check("deck: a photo that cannot be scrimmed gives way to the gradient cover (no photo)",
      ds.image_detail(im_wh) < ds.PHOTO_DETAIL_MIN and max(im_wh.resize((1, 1)).getpixel((0, 0))) < 60
      and ds.zone_contrast(im_wh, ZONES) >= ds.MIN_TEXT_CONTRAST and im_wh.size == grad.size,
      f"detail {ds.image_detail(im_wh):.2f} mean {im_wh.resize((1, 1)).getpixel((0, 0))}")
# the finisher's net: a deck from another builder, white type over a light photo
netp = os.path.join(TMP, "photo_net.pptx")
prs = blank_deck()
s = prs.slides.add_slide(prs.slide_layouts[6])
ds.set_slide_background_image(s, ds.bake_photo_background(light, alpha=0.30))
box(s, 0.92, 1.05, 11.0, 0.9, "A light photograph sits behind this headline.", 34, WHITE, bold=True)
box(s, 0.92, 2.4, 8.0, 0.5, "Body copy in the house grey, over the same photograph, has to read as well.", 16, BODY)
s2 = prs.slides.add_slide(prs.slide_layouts[6])
ds.set_slide_background_image(s2, ds.gradient_image_bytes("graphiteWash"))
box(s2, 0.92, 1.05, 11.0, 0.9, "A gradient page is left exactly as it was.", 34, WHITE, bold=True)
prs.save(netp)


def bg_of(path, idx):
    z = zipfile.ZipFile(path)
    slides, _sw, _sh = ds._slide_parts(z)
    from lxml import etree
    root = etree.fromstring(z.read(slides[idx]))
    blip = root.find(f".//{NS_P}bg//{NS_A}blip")
    rels = z.read(slides[idx].replace("slides/", "slides/_rels/") + ".rels").decode("utf8")
    m = re.search(r'<Relationship\b[^>]*\bId="%s"[^>]*>' % blip.get(f"{NS_R}embed"), rels)
    return z.read("ppt/media/" + re.search(r'Target="\.\./media/([^"]+)"', m.group(0)).group(1))


before_grad = bg_of(netp, 1)
zones_net = [(0.92 / 13.333, 1.05 / 7.5, 11.92 / 13.333, 1.95 / 7.5, (0xE9, 0xE8, 0xE1)),
             (0.92 / 13.333, 2.4 / 7.5, 8.92 / 13.333, 2.9 / 7.5, (0x9A, 0xA0, 0x9B))]
c_before = ds.zone_contrast(Image.open(io.BytesIO(bg_of(netp, 0))), zones_net)
rep_n = ds.finish(netp)
c_after = ds.zone_contrast(Image.open(io.BytesIO(bg_of(netp, 0))), zones_net)
check("finisher net: type over a light photo is under 4.5:1 on entry and clears it on exit",
      c_before < ds.MIN_TEXT_CONTRAST and c_after >= ds.MIN_TEXT_CONTRAST - 0.2
      and (rep_n.fixes.get("photo_deepened") or rep_n.fixes.get("photo_gradient")), f"{c_before:.2f} -> {c_after:.2f} {rep_n.fixes}")
check("finisher net: a gradient page is not touched", bg_of(netp, 1) == before_grad)

# ============================================================== [7] shares and the counts beside them
print("[7] a share is the count beside it over the base its label names")
BASES = [
    {"type": "cover", "title": "Comedy leads digital originals with 6.6M US viewers.",
     "stats": [{"big": "14.9M", "label": "US people watched at least one of the five"}]},
    {"type": "tiles_facts", "eyebrow": "Universe", "title": "The five categories reach 14.9M US people.",
     "sub": "Unduplicated across the five categories, each person counted once.",
     "tiles": [{"big": "14,862,537", "label": "US people watched at least one of the five"},
               {"big": "20.2M", "label": "Five-category viewing signals in the window"},
               {"big": "3.0%", "label": "US penetration for comedy, the widest"}],
     "facts": [{"label": "Comedy", "fig": "6,579,974", "note": "32.6% of viewing, 3.0% penetration"},
               {"label": "Lifestyle / Arts & Culture", "fig": "5,142,863", "note": "25.4% of viewing, 2.4% penetration"},
               {"label": "Horror", "fig": "3,418,207", "note": "16.9% of viewing, 1.6% penetration"},
               {"label": "Young Adult", "fig": "2,874,591", "note": "14.2% of viewing, 1.3% penetration"},
               {"label": "Faith", "fig": "2,196,478", "note": "10.9% of viewing, 1.0% penetration"}],
     "read": "Comedy and Lifestyle together hold 58.0% of viewing across the five categories."},
    {"type": "bars", "eyebrow": "Reach", "title": "Comedy reaches 3.0% of US people, the widest.",
     "sub": "Penetration of each category against the US, Oct 21 2025 to Oct 21 2026.",
     "rows": [{"label": "Comedy", "value": 3.0}, {"label": "Lifestyle", "value": 2.4}, {"label": "Horror", "value": 1.6},
              {"label": "Young Adult", "value": 1.3}, {"label": "Faith", "value": 1.0}],
     "read": "Penetration runs from 3.0% for comedy down to 1.0% for faith, inside a 14,862,537 person universe."},
]
probe = copy.deepcopy(BASES)
left0 = db.assert_page_bases(probe)
check("the page as planned fails the check: ten shares do not match their counts over the US", len(left0) == 10, str(left0[:3]))
notes, changes = db.reconcile_page_bases(probe)
check("every mismatched share is recomputed from its count (6,579,974 over 329.9M is 2.0%)",
      probe[1]["facts"][0]["note"] == "32.6% of viewing, 2.0% penetration" and probe[1]["facts"][4]["note"] == "10.9% of viewing, 0.7% penetration"
      and [r["value"] for r in probe[2]["rows"]] == [2.0, 1.6, 1.0, 0.9, 0.7], str(probe[1]["facts"][0]) + str(probe[2]["rows"]))
check("the same figure is corrected wherever the deck repeats it for that row",
      probe[1]["tiles"][2]["big"] == "2.0%" and probe[2]["title"] == "Comedy reaches 2.0% of US people, the widest."
      and probe[2]["read"] == "Penetration runs from 2.0% for comedy down to 0.7% for faith, inside a 14,862,537 person universe.",
      str(probe[1]["tiles"][2]) + probe[2]["title"] + probe[2]["read"])
check("counts are never changed, and a share on its own base is left alone",
      [f["fig"] for f in probe[1]["facts"]] == [f["fig"] for f in BASES[1]["facts"]] and probe[1]["tiles"][0] == BASES[1]["tiles"][0]
      and all(n_["note"].startswith(o["note"].split(",")[0]) for n_, o in zip(probe[1]["facts"], BASES[1]["facts"]))
      and probe[1]["read"] == BASES[1]["read"])
check("after the correction the check passes", db.assert_page_bases(probe) == [] and len(notes) >= 10, str(db.assert_page_bases(probe)))
prof = [{"type": "tiles_facts", "title": "The audience is 581,977 US people with a millennial core.",
         "tiles": [{"big": "581,977", "label": "Projected US viewers"}, {"big": "160,529", "label": "Avid viewers, 27.6% of the audience"}],
         "facts": [{"label": "Peacock subscribers", "fig": "354,123", "note": "60.8% penetration, index 190"},
                   {"label": "Largest age band", "fig": "32.5%", "note": "25 to 34, 188,901 people. 18 to 34 carries 52.1%."},
                   {"label": "Repeat buyers", "fig": "26.4%", "note": "of 48,211 completed buyers"},
                   {"label": "Bravo viewers", "fig": "212,345", "note": "55.0% penetration"}]}]
keep = copy.deepcopy(prof)
n2_, ch2 = db.reconcile_page_bases(prof)
check("an audience share that agrees with its count is untouched, and one that does not is recomputed over the audience",
      prof[0]["tiles"] == keep[0]["tiles"] and prof[0]["facts"][:3] == keep[0]["facts"][:3]
      and prof[0]["facts"][3]["note"] == "36.5% penetration" and len(ch2) == 1, str(prof[0]["facts"]) + str(ch2))
# the same read as a table: the count column is "US viewers", so a bare "Penetration" beside it is of the US
TBL = [{"type": "cover", "title": "Five categories reach 14.9M US viewers.", "stats": [{"big": "14.9M", "label": "US people watched one of the five"}]},
       {"type": "table", "eyebrow": "Scoreboard", "title": "Reach ranks the same way viewing does.", "sub": "Viewers and penetration by category.",
        "columns": ["Category", "US viewers", "Penetration", "Lead titles"],
        "rows": [["Comedy", "6,579,974", "3.0%", "Game Changer, Smosh, Kill Tony"], ["Faith", "2,196,478", "1.0%", "BibleProject, The Bible Recap"]],
        "read": "Comedy reaches 6,579,974 US viewers at 3.0% penetration, the largest of the five on both measures.",
        "read2": "Faith is the smallest at 2,196,478 viewers and 1.0% penetration."}]
db.reconcile_page_bases(TBL)
check("a penetration column beside a US viewers column is recomputed over the US, and the reads follow",
      [r[2] for r in TBL[1]["rows"]] == ["2.0%", "0.7%"] and "at 2.0% penetration" in TBL[1]["read"]
      and "0.7% penetration" in TBL[1]["read2"] and [r[1] for r in TBL[1]["rows"]] == ["6,579,974", "2,196,478"],
      str(TBL[1]["rows"]) + TBL[1]["read"])
# a share that matches no base it could be measured against comes off the page
ODD = [{"type": "tiles_facts", "title": "The audience is 581,977 viewers.", "tiles": [{"big": "581,977", "label": "Projected viewers"}],
        "facts": [{"label": "Bravo viewers", "fig": "212,345", "note": "index 190, 4.1% penetration"}]},
       {"type": "table", "title": "A table.", "columns": ["Group", "Viewers", "Penetration", "Index"],
        "rows": [["Bravo", "212,345", "4.1%", "190"], ["Peacock", "354,123", "6.2%", "269"]]}]
n3_, _c3 = db.reconcile_page_bases(ODD)
check("a share with no base it could be measured against is taken off the page (clause and column), counts stay",
      ODD[0]["facts"][0]["note"] == "index 190" and ODD[1]["columns"] == ["Group", "Viewers", "Index"]
      and ODD[1]["rows"] == [["Bravo", "212,345", "190"], ["Peacock", "354,123", "269"]]
      and any("taken off the page" in x for x in n3_), str(ODD[0]["facts"]) + str(ODD[1]) + str(n3_))
ODD2 = [{"type": "tiles_facts", "title": "The audience is 581,977 viewers.", "tiles": [{"big": "581,977", "label": "Projected viewers"}],
         "facts": [{"label": "Bravo viewers", "fig": "212,345", "note": "4.1% penetration, 188,901 of them weekly, index 190."}]}]
db.reconcile_page_bases(ODD2)
check("taking a share off a line leaves the figures around it whole (a comma inside a figure is not a clause)",
      ODD2[0]["facts"][0]["note"] == "188,901 of them weekly, index 190.", ODD2[0]["facts"][0]["note"])
# a page about one row: its share follows the correction made where the count sits
ABOUT = copy.deepcopy(BASES) + [{"type": "tiles_facts", "eyebrow": "Wallet", "title": "Lifestyle and arts is the premium block.",
                                 "tiles": [{"big": "5,142,863", "label": "US viewers"}, {"big": "25.4%", "label": "Share of five-category viewing"}],
                                 "facts": [{"label": "Hot Ones", "fig": "2,418,963", "note": "Largest single title across the five"},
                                           {"label": "Penetration", "fig": "2.4%", "note": "Share of US people reached"}]}]
db.reconcile_page_bases(ABOUT)
check("a page about one category carries that category's corrected share (2.4% of US people reads 1.6% there too)",
      ABOUT[3]["facts"][1]["fig"] == "1.6%" and ABOUT[3]["tiles"][1]["big"] == "25.4%" and ABOUT[3]["facts"][0]["fig"] == "2,418,963",
      str(ABOUT[3]["facts"]) + str(ABOUT[3]["tiles"]))
pb = {"title": "t", "slides": copy.deepcopy(BASES)}
out_b = os.path.join(TMP, "bases.pptx")
db.render_insights_deck(pb, out_b, static_dir=os.path.join(ROOT, "static"))
tb_ = " ".join(t for _i, t in texts_of(out_b))
check("deck: the page is written with the corrected shares and the job record says what changed",
      "2.0% penetration" in tb_ and "3.0%" not in tb_ and "Comedy reaches 2.0% of US people" in tb_ and "6,579,974" in tb_
      and (pb["_finish"].get("base_check") == "ok") and len(pb["_finish"].get("base_notes") or []) >= 10,
      str(pb["_finish"].get("base_check")) + tb_[:300])

# ============================================================== [8] stock phrasing
print("[8] stock filler and a phrase on three pages")
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("readability_check_t", os.path.join(SCRIPTS, "readability_check.py"))
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
res = rc.analyze(["slide 1: A plain header that passes."], ["The first exposure does the work, and the rest follows."],
                 [("slide 1", ["The first exposure does the work, and the rest follows."])], 1)
check("the check flags 'does the work' as stock filler", res["count"] == 1 and res["flags"][0]["kind"] == "stock_phrase"
      and "does the work" in res["flags"][0]["phrase"], str(res["flags"]))
for raw, want_ in (("means the first exposure does the work, and", "means the first exposure is enough, and"),
                   ("Two brands do the heavy lifting here.", "Two brands carry most of it here."),
                   ("The index does the talking.", "The index makes the case."),
                   ("The first served unit does the conversion work.", "The first served unit carries the conversion."),
                   ("It does the work of three channels.", "It does the work of three channels."),
                   ("with serialized drama doing most of the repeat work.", "with serialized drama carrying most of the repeat."),
                   ("Nothing here to change.", "Nothing here to change.")):
    check(f"scrub_stock_phrases({raw!r})", ds.scrub_stock_phrases(raw) == want_, ds.scrub_stock_phrases(raw))
check("deck: 'does the work' is not on any page of the builder's deck, and its sentence still carries both figures",
      "does the work" not in ALL and any("38.2%" in t and "26.4%" in t and "is enough" in t for _i, t in T),
      str([t for _i, t in T if "First-impression" in t]))
check("deck: the phrase the plan carried on three pages reads on two at most", ALL.lower().count(PHRASE) <= 2 and ALL.count("68.5%") >= 3,
      str(ALL.lower().count(PHRASE)))
check("drop_phrase rewords first, and never loses the figures outside the phrase",
      "68.5%" in cf.drop_phrase(f"52.1% are under 35 and 68.5% {PHRASE}. Income at $100,000 or more reads 45.5%.", PHRASE)
      and PHRASE not in cf.drop_phrase(f"52.1% are under 35 and 68.5% {PHRASE}. Income at $100,000 or more reads 45.5%.", PHRASE))

# ============================================================== [9] cover photo candidates
print("[9] cover photo candidates: one address once; no interface, icon, clip-art or logo")


def png(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


shot = Image.new("RGB", (1600, 900), (255, 255, 255))            # an interface: a white page with thumbnails
thumb = Image.open(io.BytesIO(photo(120, 70, (240, 135))))
for k in range(6):
    shot.paste(thumb, (60 + (k % 3) * 300, 80 + (k // 3) * 220))
logo = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))             # a logo: a flat mark on a transparent ground
logo.paste((230, 30, 30, 255), (300, 400, 900, 800))
wall = Image.new("RGB", (1600, 900), (190, 20, 20))              # a flat graphic: two colors
wall.paste((255, 255, 255), (700, 380, 900, 520))
real = photo(110, 80, (1600, 1000), seed=9)
store = {
    "https://upload.wikimedia.org/wikipedia/commons/f/fd/Youtube_interface%2C_showing_search_results.png?utm_campaign=ap": png(shot),
    "https://upload.wikimedia.org/wikipedia/commons/f/fd/Youtube_interface%2C_showing_search_results.png?utm_campaign=api": png(shot),
    "https://www.freeiconspng.com/uploads/youtube-logo-png-photo-0.png": png(logo),
    "https://cdn.example.com/press/brand-logo-2026.png": png(logo),
    "https://cdn.example.com/img/a81f.png": png(shot),               # a screenshot with nothing in its name
    "https://cdn.example.com/img/b22c.png": png(logo),               # a cut-out with nothing in its name
    "https://cdn.example.com/img/c93d.jpg": png(wall),               # a flat graphic with nothing in its name
    "https://cdn.example.com/photos/stage.jpg": real,
    "https://CDN.example.com/photos/stage.jpg?size=large": real,     # the same picture, written another way
}
asked = []


def fetch(u):
    asked.append(u)
    return store[u]


blobs, kept, rejected = db._vet_photo_candidates(list(store), 3, fetch)
why = {u: w for u, w in rejected}
check("the one photograph is kept, once", blobs == [real] and kept == ["https://cdn.example.com/photos/stage.jpg"], str(kept))
check("one address written two ways is asked for once (the duplicate is never fetched)",
      asked.count("https://CDN.example.com/photos/stage.jpg?size=large") == 0
      and sum(1 for u in asked if "Youtube_interface" in u) == 0, str(asked))
check("an interface screenshot, an icon site and a logo file are turned away by their address",
      "interface" in why.get(list(store)[0], "") and "freeiconspng" in why.get(list(store)[2], "")
      and "logo" in why.get("https://cdn.example.com/press/brand-logo-2026.png", ""), str(why)[:500])
check("a screenshot, a cut-out and a flat graphic are turned away by their pixels when the address says nothing",
      "white" in why.get("https://cdn.example.com/img/a81f.png", "") and "cut-out" in why.get("https://cdn.example.com/img/b22c.png", "")
      and ("colors" in why.get("https://cdn.example.com/img/c93d.jpg", "") or "flat" in why.get("https://cdn.example.com/img/c93d.jpg", "")),
      str(why)[:600])
check("a wallpaper or a background is a graphic made to sit behind something, not a picture of the subject",
      "background" in (ds.photo_url_reject_reason("https://cdn.pixabay.com/photo/2022/12/01/03/08/youtube-background-7628193_1280.jpg") or "")
      and "wallpaper" in (ds.photo_url_reject_reason("https://walls.example.com/w/full/youtube-wallpaper-4k.jpg") or "")
      and ds.photo_url_reject_reason("https://images.justwatch.com/poster/334112052/s718/las-culturistas-culture-awards-2025.jpg") is None
      and ds.photo_url_reject_reason("https://upload.wikimedia.org/wikipedia/en/6/67/Las_Culturistas_2026.jpeg?utm_content=thumbnail_unscaled") is None)
check("a photograph passes both reads", ds.photo_url_reject_reason("https://cdn.example.com/photos/stage.jpg") is None
      and ds.photo_blob_reject_reason(real) is None and ds.photo_blob_reject_reason(mid) is None)
check("a subject of general words names nothing to photograph; a named subject keeps its own words",
      db._subject_photo_tokens("Digital Original Content Categories") == []
      and db._subject_photo_tokens("Las Culturistas Culture Awards Viewers") == ["culturistas", "culture", "awards"]
      and db._subject_photo_tokens("YouTube") == ["youtube"])
lc_toks = db._subject_photo_tokens("Las Culturistas Culture Awards Viewers")
check("an open-web picture must be named for the subject, outside the folders every site has",
      db._url_names_subject("https://images.justwatch.com/poster/1/s718/las-culturistas-culture-awards-2025.jpg", lc_toks)
      and not db._url_names_subject("https://blog.example.com/wp-content/uploads/2025/03/image_1N7B6.jpeg", ["content", "creator"])
      and not db._url_names_subject("https://blog.example.com/wp-content/uploads/culture.jpeg", lc_toks)
      and db._url_names_subject("https://upload.wikimedia.org/wikipedia/en/6/67/Las_Culturistas_2026.jpeg", lc_toks, need=1)
      and not db._url_names_subject("https://upload.wikimedia.org/wikipedia/commons/5/54/MrBeast_2023.jpg", lc_toks, need=1))


class _NoNetwork:
    """image_backfill stand-in: any lookup here is a test failure."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError(f"image_backfill.{name} was reached for a subject with nothing to photograph")


_saved_ib = sys.modules.get("image_backfill")
sys.modules["image_backfill"] = _NoNetwork()
try:
    got_generic = db._fetch_photos("Digital Original Content Categories", "brand", 3)
    why_generic = db._PHOTO_REJECTS.get("digital original content categories")
finally:
    if _saved_ib is not None:
        sys.modules["image_backfill"] = _saved_ib
    else:
        sys.modules.pop("image_backfill", None)
check("a topic subject runs no image search at all and says why", got_generic == [] and why_generic
      and "names no person, title or brand" in why_generic[0][1], str(why_generic))
none_b, none_k, _r = db._vet_photo_candidates([u for u in store if "stage" not in u], 3, fetch)
check("nothing good left means no photo at all", none_b == [] and none_k == [])
db._PHOTO_CACHE.clear()
db._BAKED_CACHE.clear()
pn = {"title": "t", "image_subject": "Nothing Good", "image_kind": "brand", "slides": [copy.deepcopy(PLAN_FLAGGED["slides"][0])]}
db._PHOTO_CACHE["nothing good|brand"] = none_b
out_n = os.path.join(TMP, "no_photo.pptx")
os.environ["PROMETHEUS_DECK_PHOTOS"] = "1"
db.render_insights_deck(pn, out_n, static_dir=os.path.join(ROOT, "static"))
os.environ["PROMETHEUS_DECK_PHOTOS"] = "0"
im_n = cover_bg(out_n)
check("deck: with no photograph the cover is the gradient", ds.image_detail(im_n) < ds.PHOTO_DETAIL_MIN and im_n.size == grad.size
      and max(im_n.resize((1, 1)).getpixel((0, 0))) < 60, f"{ds.image_detail(im_n):.2f}")

# ============================================================== layout: the type is counted the way it is laid
print("[10] a headline the widow fit sets on two lines is budgeted two lines (no text over text)")
from migration.bpiq_deck_builder import check_layout  # noqa: E402


def collisions(path):
    return [i for i in check_layout(Presentation(path)) if "collides" in i or "off the page" in i]


HEAD = "Each category carries a different audience shape."
check("the builder counts lines with the pack's wrap model when it needs more than the plain measure",
      db._line_count(HEAD, 34, 11.493, True) == 2 and db._line_count("Short title.", 34, 11.493, True) == 1,
      str(db._line_count(HEAD, 34, 11.493, True)))
two = {"title": "t", "slides": [copy.deepcopy(PLAN_FLAGGED["slides"][0]),
                                {"type": "table", "eyebrow": "Audience", "title": HEAD,
                                 "sub": "Who watches, how deep they go, and what it reads as.",
                                 "columns": ["Category", "Skew", "Depth"], "rows": [["Comedy", "18 to 34, male-leaning", "24.0% avid"],
                                                                                   ["Faith", "Daily by design", "31.3% avid"]],
                                 "read": "Reach concentrates in comedy and frequency sits with Faith."},
                                copy.deepcopy(PLAN_FLAGGED["slides"][4])]}
out_two = os.path.join(TMP, "two_line_title.pptx")
db.render_insights_deck(two, out_two, static_dir=os.path.join(ROOT, "static"))
check("deck: the subhead sits under a two-line headline, nothing collides", collisions(out_two) == [], str(collisions(out_two))[:400])
# every page type under a two-line headline: the content stops above the source line
from migration.bpiq_deck_builder import _frame_ink  # noqa: E402
TALL = "Search demand is concentrated on where to watch the special."      # two lines at 34 pt
SRC = "Crosswalk Unique ID reads, Nov 10 2025 to Nov 10 2026."
SUB = "Unique searchers and the mix of what they typed."
READ2L = ("A third of all queries, 34.1%, ask where to watch, so distribution messaging answers the largest block of stated demand "
          "in the window and the next block is the winners list.")
tall = {"title": "t", "slides": [
    copy.deepcopy(PLAN_FLAGGED["slides"][0]), copy.deepcopy(PLAN_FLAGGED["slides"][4]),
    {"type": "split_stats_bars", "eyebrow": "Search", "title": TALL, "sub": SUB, "source": SRC,
     "stat_cards": [{"kicker": "Twelve months", "big": "742,183", "label": "unique US searchers on the special"},
                    {"kicker": "Peak week", "big": "181,427", "label": "searchers in the single peak week", "accent": True}],
     "bars_title": "Query mix, share of searches", "rows": [{"label": "Where to watch", "value": 34.1, "accent": True},
                                                           {"label": "Winners list", "value": 22.7}, {"label": "Host clips", "value": 18.4},
                                                           {"label": "Carpet looks", "value": 14.2}, {"label": "Live and tickets", "value": 10.6}],
     "read": READ2L},
    {"type": "hero_proof", "eyebrow": "Spend", "title": TALL, "sub": SUB, "source": SRC, "big": "$348.66",
     "line": "Each buying engager spends $348.66 a year across the tracked categories.",
     "proofs": [{"fig": "$112.47", "label": "Average order value"}, {"fig": "3.1", "label": "Orders per buyer per year"},
                {"fig": "26.4%", "label": "Repeat buyers inside 90 days"}]},
    {"type": "tiles_row", "eyebrow": "Response", "title": TALL, "sub": SUB, "source": SRC,
     "tiles": [{"big": "38.2%", "label": "Click on first impression", "body": "Close to four in ten clicked the first unit served to them."},
               {"big": "71.4%", "label": "Cart opened the same day", "body": "Most viewers who carted did it inside the day of the click."},
               {"big": "26.4%", "label": "Repeat buyers in 90 days", "body": "A quarter of first-time buyers placed a second order."}],
     "read": READ2L},
    {"type": "tiles_facts", "eyebrow": "Universe", "title": TALL, "sub": SUB, "source": SRC,
     "tiles": [{"big": "581,977", "label": "Projected US viewers"}, {"big": "160,529", "label": "Avid viewers"}, {"big": "57.6%", "label": "Female share"}],
     "facts": [{"label": f"Fact row {k}", "fig": f"{20 + k}.5%", "note": "A note that runs to the far side of its column in the row."} for k in range(6)],
     "read": READ2L},
    {"type": "table", "eyebrow": "Journeys", "title": TALL, "sub": SUB, "source": SRC,
     "columns": ["Path type", "Sessions", "Conversion", "Index"],
     "rows": [[f"Path type number {k}", f"{84 + k},213", f"{11 - k}.1%", str(374 - 40 * k)] for k in range(5)],
     "read": "Sessions carrying the special convert at 11.1% against 3.4% with no talent node.", "read2": "Peer specials land in between at 6.8%."},
    {"type": "argument", "ground": "light", "eyebrow": "Buy", "title": TALL, "sub": SUB, "source": SRC,
     "cards": [{"head": f"Card head {k}", "body": "Two lines of card copy that say one thing about the group and carry one figure, 24.4%."} for k in range(4)]},
    {"type": "bars", "ground": "light", "eyebrow": "Retail", "title": TALL, "sub": SUB, "source": SRC, "show_index": True,
     "rows": [{"label": f"Category {k}", "value": 77.4 - 4 * k, "index": 147 - k} for k in range(9)], "read": READ2L},
]}
out_tall = os.path.join(TMP, "tall_headlines.pptx")
db.render_insights_deck(tall, out_tall, static_dir=os.path.join(ROOT, "static"))
low = []
for si, sld in enumerate(Presentation(out_tall).slides, 1):
    shapes = list(sld.shapes)
    src_top = min([sh.top / 914400 for sh in shapes if sh.has_text_frame and sh.text_frame.text.strip() == SRC] or [99])
    if src_top > 90:
        continue
    for sh in shapes:
        txt_ = sh.text_frame.text.strip() if sh.has_text_frame else ""
        if txt_ == SRC or (sh.top / 914400) >= 7.0:
            continue
        ink = _frame_ink(sh) if txt_ else None
        bottom = ink[3] if ink else (sh.top + sh.height) / 914400
        if not txt_ and sh.shape_type != 1:          # pictures (the lockup) sit at the top
            continue
        if bottom > src_top + 0.02:
            low.append((si, round(bottom, 2), txt_[:40] or "card"))
check("under a two-line headline nothing (a card, a row, a read) runs past the source line, on every page type",
      not low and collisions(out_tall) == [], str(low[:6]) + str(collisions(out_tall))[:300])
check("deck: the tall-headline fixture has no lint FAIL (widows, backgrounds, type)",
      tall["_finish"].get("counts", {}).get("lint_fail") == 0, str(tall["_finish"].get("counts")))

# a last word too long to bind: the same copy at a width where the natural wrap leaves two words on the last line
WID = "Vertical serialized drama does the repeat work with an under-25, female-leaning audience."
fitw = ds.widow_fit(WID, 11.5, 3.164, height_in=1.9)
check("a widow the binding cannot fix is fitted without it (same words, same size, two words on the last line)",
      fitw.action in ("narrowed", "widened", "bound") and fitw.size_pt == 11.5 and fitw.text.replace("\u00a0", " ") == WID
      and ds.widow_lines(fitw.text, fitw.size_pt, fitw.width_in)[0] is False, f"{fitw.action} {fitw.width_in} {fitw.size_pt}")
wid_plan = {"title": "t", "slides": [copy.deepcopy(PLAN_FLAGGED["slides"][0]), copy.deepcopy(PLAN_FLAGGED["slides"][4]),
                                     {"type": "tiles_row", "eyebrow": "Repeat", "title": "Faith repeats hardest at 31.3%.",
                                      "sub": "Share of each audience at four or more views.",
                                      "tiles": [{"big": "31.3%", "label": "Faith", "body": "Daily by design: devotional audio and read-along plans, so the habit renews most days."},
                                                {"big": "26.7%", "label": "Young Adult", "body": WID},
                                                {"big": "24.0%", "label": "Comedy", "body": "Reads as a weekly habit across an 18 to 34, male-leaning base."}],
                                      "read": "Faith is the smallest of the five and the one its viewers return to most."}]}
out_w = os.path.join(TMP, "long_last_word.pptx")
db.render_insights_deck(wid_plan, out_w, static_dir=os.path.join(ROOT, "static"))
check("deck: a card whose copy ends on a long hyphenated word passes the lint's widow check",
      wid_plan["_finish"].get("counts", {}).get("lint_fail") == 0 and wid_plan["_finish"].get("ready") is True,
      str(wid_plan["_finish"])[:300])
check("the check flags the filler with a word in the middle, and not a comparison",
      rc.stock_hits("the first served unit does the conversion work") == ["does the conversion work"]
      and rc.stock_hits("It does the work of three channels.") == [] and rc.stock_hits("Nike does the heavy lifting for this buy") != [])
check("the cover eyebrow stays on one line (it gives up its last segments, never its subject)",
      db._one_line_eyebrow("Las Culturistas Culture Awards Viewers · Audience Insights · Nov 10 2025 to Nov 10 2026")
      == "LAS CULTURISTAS CULTURE AWARDS VIEWERS  \u00b7  AUDIENCE INSIGHTS"
      and db._one_line_eyebrow("Digital original content · Category read") == "DIGITAL ORIGINAL CONTENT  \u00b7  CATEGORY READ")
# a brand whose name repeats itself is a name, not a doubled word
miu = {"title": "t", "slides": [copy.deepcopy(PLAN_FLAGGED["slides"][0]), copy.deepcopy(PLAN_FLAGGED["slides"][4]),
                                {"type": "bars", "ground": "light", "eyebrow": "Retail", "title": "They over-buy luxury accessories.",
                                 "sub": "Share of the audience buying each brand in the window.", "value_suffix": "%",
                                 "rows": [{"label": "Miu Miu", "value": 14.2, "accent": True}, {"label": "Coach", "value": 11.8},
                                          {"label": "Duran Duran", "value": 9.4}],
                                 "read": "Miu Miu leads the accessories set at 14.2% of this audience."}]}
out_m = os.path.join(TMP, "miu_miu.pptx")
db.render_insights_deck(miu, out_m, static_dir=os.path.join(ROOT, "static"))
check("deck: a brand named 'Miu Miu' does not fail the doubled-word check (the deck is READY)",
      miu["_finish"].get("ready") is True and miu["_finish"].get("steps", {}).get("embed") == "ok", str(miu["_finish"])[:300])
dbl = os.path.join(TMP, "doubled.pptx")
prs = blank_deck()
s = page(prs)
box(s, 0.92, 1.05, 11.0, 0.6, "A header that reads as one plain sentence.", 30, bold=True)
box(s, 0.92, 2.4, 9.0, 0.5, "No pilot pilot tests are filed in this room so far.")
prs.save(dbl)
rep_dbl = ds.finish(dbl)
check("a real doubled word still fails the check, and the status says NOT READY",
      (not rep_dbl.ready) and any(n == "embed" and not ok and "repeated word 'pilot pilot'" in o for n, ok, o in rep_dbl.steps),
      rep_dbl.summary)
for label, path in (("the builder fixture", out), ("the share fixture", out_b), ("the tight table", out_t), ("the cover fixture", p_mid)):
    check(f"deck: no text collides on {label}", collisions(path) == [], str(collisions(path))[:400])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{FAIL} failure(s)")
sys.exit(1 if FAIL else 0)
