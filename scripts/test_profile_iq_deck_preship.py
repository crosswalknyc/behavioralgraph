#!/usr/bin/env python3
"""Every Profile IQ insights deck passes the house pre-ship checks
(2026-11-10). The Las Culturistas Culture Awards Viewers deck reached a
Lionsgate seat with 14 brand-lint FAILs (twelve widows, two full-page
shapes acting as the background), two low-resolution cover images and two
readability flags (one subhead template on four slides, one phrase on
three). This renders a fixture plan that carries every one of those
patterns through the fixed builder and asserts, with the pack's own
tools, that the finished deck is clean:

  [1] zero brand-lint FAIL, zero readability flags, fonts embedded
      (the pack's cw_brand_lint.py, readability_check.py and
      cw_embed_fonts.py --verify on the saved file)
  [2] backgrounds are slide backgrounds: every slide carries p:bg and no
      full-page shape or picture sits under the content; a photo moment
      is baked (scrim included) into one background image at 3840 px
  [3] no image under 288 ppi (the lockup, the gradient, the photo)
  [4] widows: a non-breaking space appears only between the last two
      words of a paragraph, never anywhere else; the pack's wrap model
      agrees the last line carries two words
  [5] repeated phrases: the subhead template is retired (index and
      penetration defined once, later slides name their own content),
      a closer carried three times is rebuilt from the slide's figures;
      titles and card bodies are untouched and the caller's plan is not
      mutated
  [6] Geist Mono only on raw clickstream rows (a brand name in a paths
      row is set in Crosswalk Inter)
  [7] the finish report rides on the plan for the job record
  [8] the finisher's own net: a deck from any builder with a full-page
      rectangle, a photo under a scrim rectangle and a widowed paragraph
      comes out of design_skill.finish with zero lint FAIL

Hermetic: no network (photos come from the in-process cache), the
packaged design pack inside bg-webapp.

Run: python3 scripts/test_profile_iq_deck_preship.py
"""
import copy
import io
import os
import re
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ["PROMETHEUS_DECK_PHOTOS"] = "0"

import deck_builder as db  # noqa: E402
from migration import design_skill as ds  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Emu, Inches, Pt  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE  # noqa: E402
from PIL import Image  # noqa: E402

FAIL = 0
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PY = sys.executable
SCRIPTS = str(ds.scripts_dir() or "")
TMP = tempfile.mkdtemp(prefix="pm_deck_preship_")


def check(name, ok, detail=""):
    global FAIL
    print(("PASS" if ok else "FAIL"), name, detail)
    if not ok:
        FAIL += 1


def run_tool(script, path, *extra):
    r = subprocess.run([PY, os.path.join(SCRIPTS, script), path, *extra],
                       capture_output=True, text=True, timeout=600)
    return (r.stdout or "") + (r.stderr or "")


TEMPLATE_SUB = "{lead}, penetration of the audience with index against the average American."
REPEAT = "Units carrying the awards or its hosts clear 2.41% CTR"

PLAN = {
    "title": "This audience turns award-show fandom into purchase.",
    "filename_stem": "preship_fixture",
    "image_subject": "Preship Fixture Subject", "image_kind": "title",
    "slides": [
        {"type": "cover", "eyebrow": "PRESHIP FIXTURE VIEWERS · AUDIENCE READ · NOV 2025 TO NOV 2026",
         "title": "This audience turns award-show fandom into purchase.",
         "intro": "The full viewer read for the fixture, Nov 10 2025 to Nov 10 2026: who they are, what they buy, and how they convert.",
         "stats": [{"big": "4.19M", "label": "Projected US viewers"},
                   {"big": "11.8%", "label": "Conversion with the awards on the path"},
                   {"big": "2.41%", "label": "CTR on awards-tagged units"}], "accent_index": 1},
        {"type": "argument", "ground": "dark", "eyebrow": "Argument",
         "title": "Small universe, dense fandom, premium wallet.",
         "sub": "Four things a buyer needs from this profile, in order.",
         "cards": [
             {"head": "The universe is compact",
              "body": "4,194,382 US viewers, with a hard avid core of 1,154,947 people watching four or more times. Avid share reads 27.4%."},
             {"head": "The demo is the one brands pay for",
              "body": "Millennial, educated, urban, queer-leaning. 2,380,031 viewers sit in 25 to 44, 68.5% hold a bachelors degree or higher."},
             {"head": "Fandom is already commercial",
              "body": "Entertainment purchasing indexes 147, beauty 147, accessories 143. Shopping is not a stretch for this audience, it is the habit."},
             {"head": "The buy is talent-led",
              "body": REPEAT + ", and awards-path sessions convert at 11.8% against 3.6% with no talent node."}]},
        {"type": "tiles_facts", "eyebrow": "Universe",
         "title": "Four million viewers, concentrated in the big coastal metros.",
         "sub": "Projected US audience and its defining shape, Nov 10 2025 to Nov 10 2026.",
         "source": "Profile IQ audience and demographic rows, Nov 10 2025 to Nov 10 2026.",
         "tiles": [{"big": "4.19M", "label": "Projected US viewers"},
                   {"big": "1.15M", "label": "Avid viewers, 27.4% of the audience"},
                   {"big": "68.5%", "label": "Bachelors degree or higher"},
                   {"big": "57.6%", "label": "Female share of the audience"}], "accent_index": 1,
         "facts": [{"label": "Core age band", "fig": "32.5%", "note": "25-34 leads; 25-44 carries 2,380,031 viewers"},
                   {"label": "LGBTQ+ share", "fig": "33.9%", "note": "Against a peer audience median of 13.3%"},
                   {"label": "Income at $100K or more", "fig": "45.5%", "note": "Top three income bands combined"},
                   {"label": "New York DMA", "fig": "12.3%", "note": "Index 199; Los Angeles adds 7.7% at index 167"}],
         "read": "The audience is compact but premium: 4,194,382 viewers, two thirds degree-holding, and heavily weighted to New York and Los Angeles."},
        {"type": "bars", "ground": "light", "eyebrow": "Interests",
         "title": "Reality television and celebrity talk run three times normal.",
         "sub": TEMPLATE_SUB.format(lead="Stated interests"),
         "source": "Profile IQ interest rows, Nov 10 2025 to Nov 10 2026.", "show_index": True, "value_suffix": "%",
         "rows": [{"label": "Social media", "value": 80, "index": 117}, {"label": "Streaming", "value": 76.6, "index": 122},
                  {"label": "Movies and shows", "value": 69.5, "index": 123}, {"label": "Comedy", "value": 57.9, "index": 194},
                  {"label": "Podcasts", "value": 56.8, "index": 239}, {"label": "Celebrity gossip", "value": 51.4, "index": 300, "accent": True},
                  {"label": "Reality shows", "value": 48.5, "index": 318, "accent": True}],
         "read": "Reality shows at index 318 and celebrity gossip at index 300 are the defining interests; comedy and podcasts sit right behind them."},
        {"type": "bars", "ground": "light", "eyebrow": "Retail",
         "title": "They over-buy entertainment, beauty and accessories.",
         "sub": TEMPLATE_SUB.format(lead="Purchased categories"),
         "source": "Profile IQ purchase category rows, Nov 10 2025 to Nov 10 2026.", "show_index": True, "value_suffix": "%",
         "rows": [{"label": "Entertainment", "value": 77.4, "index": 147, "accent": True}, {"label": "Beauty", "value": 56.5, "index": 147},
                  {"label": "Travel", "value": 53.5, "index": 128}, {"label": "Dining out", "value": 52.9, "index": 129},
                  {"label": "Accessories", "value": 50.3, "index": 143}, {"label": "Clothing", "value": 44.5, "index": 137}],
         "read": "Entertainment purchasing reaches 77.4% of the audience at index 147, with beauty matching it exactly on index."},
        {"type": "split_stats_bars", "eyebrow": "Wallet", "title": "Nearly half of this audience earns six figures.",
         "sub": "Income shape and the brands these viewers actually buy.",
         "source": "Profile IQ income and purchased brand rows, Nov 10 2025 to Nov 10 2026.",
         "stat_cards": [{"kicker": "Income at $100K or more", "big": "45.5%", "label": "1,908,444 viewers in the top three income bands", "accent": True},
                        {"kicker": "Unique US searchers", "big": "2.18M", "label": "2,184,739 people searched " + "the awards or its hosts in window"}],
         "bars_title": "Top purchased brands, % of audience", "value_suffix": "%",
         "rows": [{"label": "Nike", "value": 34.3, "accent": True}, {"label": "Bath & Body Works", "value": 24.4},
                  {"label": "Converse", "value": 22.8}, {"label": "Stanley", "value": 18.5}, {"label": "CeraVe", "value": 16.3}, {"label": "Zara", "value": 11}],
         "read": "Spending power is real: 1,908,444 viewers earn $100,000 or more, and the brand mix skews to accessible premium rather than value."},
        {"type": "bars", "ground": "dark", "eyebrow": "Graph", "title": "The talent graph is the rarest asset in this file.",
         "sub": TEMPLATE_SUB.format(lead="Talent affinity"),
         "source": "Profile IQ talent and musician rows, Nov 10 2025 to Nov 10 2026.", "show_index": True, "value_suffix": "%",
         "rows": [{"label": "Bowen Yang", "value": 93.3, "index": 2392, "accent": True}, {"label": "Matt Rogers", "value": 89.1, "index": 5457, "accent": True},
                  {"label": "Chappell Roan", "value": 69.3, "index": 405}, {"label": "Sabrina Carpenter", "value": 64.1, "index": 296},
                  {"label": "Charli XCX", "value": 53.2, "index": 584}],
         "read": "Matt Rogers reads at index 5457 and Bowen Yang at 2392, the two highest talent signals in the corpus for this pairing."},
        {"type": "bars", "ground": "light", "eyebrow": "Social", "title": "They live on the text platforms everyone else left.",
         "sub": TEMPLATE_SUB.format(lead="Social platform usage"),
         "source": "Profile IQ social platform rows, Nov 10 2025 to Nov 10 2026.", "show_index": True, "value_suffix": "%",
         "rows": [{"label": "YouTube", "value": 99.4, "index": 121}, {"label": "Instagram", "value": 75.6, "index": 137},
                  {"label": "Threads", "value": 35.6, "index": 249, "accent": True}, {"label": "Tumblr", "value": 28.4, "index": 447, "accent": True},
                  {"label": "Bluesky", "value": 28.1, "index": 732, "accent": True}],
         "read": "Bluesky at index 732 and Tumblr at 447 are where this audience separates from everyone else; Instagram and TikTok carry the scale."},
        {"type": "hero", "ground": "accent", "eyebrow": "Click", "title": "Engagers cross into retail inside the same session.",
         "sub": "Same-session movement from awards content to a retail cart.", "big": "3.1x",
         "line": "Viewers who touch awards content reach a retail cart in the same session 3.1 times as often as the general streaming audience.",
         "support": "The gap means creative placed next to this content does not need a second visit to earn its return."},
        {"type": "bars", "ground": "light", "eyebrow": "Click", "title": "Awards-tagged units out-click every comparable set.",
         "sub": "Click-through rate on talent-tagged display and video units.",
         "source": "Crosswalk Unique ID unit performance, Nov 10 2025 to Nov 10 2026.", "show_index": False, "value_suffix": "%",
         "rows": [{"label": "Culture Awards units", "value": 2.41, "accent": True}, {"label": "Bravo franchise units", "value": 1.92},
                  {"label": "Late-night units", "value": 1.64}, {"label": "Music awards units", "value": 1.48}, {"label": "General entertainment", "value": 0.97}],
         "read": REPEAT.replace(" CTR", "") + ", against 0.97% for the broad entertainment benchmark."},
        {"type": "tiles_row", "eyebrow": "Response", "title": "Response lands fast and then repeats.",
         "sub": "How this audience behaves after a first exposure to awards-adjacent creative.",
         "source": "Crosswalk Unique ID ad response reads, Nov 10 2025 to Nov 10 2026.",
         "tiles": [{"big": "42.7%", "label": "Clicks on first impression", "body": "Almost half of all clicks land on the first exposure, so frequency caps can run lower than category norm."},
                   {"big": "11 Min", "label": "Median time to first cart", "body": "Among engagers who add to cart, the median gap from click to cart is eleven minutes."},
                   {"big": "31.4%", "label": "Repeat purchase rate", "body": "75,694 of the 241,063 completing buyers placed a second order inside the window."}], "accent_index": 1,
         "read": "Response is front-loaded: 42.7% of clicks arrive on the first impression and carts follow within eleven minutes."},
        {"type": "table", "eyebrow": "Journeys", "title": "A talent node triples conversion on the path.",
         "sub": "Purchase conversion by what appeared on the session path before checkout.",
         "source": "Crosswalk Unique ID journeys, Nov 10 2025 to Nov 10 2026. n=84,213 awards-path sessions.",
         "columns": ["Path type", "Sessions", "Conversion", "Order value"],
         "rows": [["Culture Awards node", "84,213", "11.8%", "$94.27"], ["Bravo franchise node", "61,448", "7.4%", "$81.16"],
                  ["Music awards node", "47,932", "6.1%", "$76.52"], ["No talent node", "212,674", "3.6%", "$63.41"]],
         "accent_row": 0, "accent_col": 2,
         "read": "Sessions carrying the awards convert at 11.8% against 3.6% with no talent node on the path.",
         "read2": "Order value moves with it, running $30.86 higher than the no-talent baseline."},
        {"type": "hero_proof", "eyebrow": "Spend", "title": "Each buying engager is worth $386 a year.",
         "sub": "Measured annual spend per completing engager across tracked retail categories.",
         "source": "Crosswalk Unique ID spend reads, Nov 10 2025 to Nov 10 2026.", "big": "$386.51",
         "line": "Completing engagers place 4.1 orders a year at $94.27 each, which prices the 241,063-person buying core at real category value.",
         "proofs": [{"fig": "$94.27", "label": "Average order value"}, {"fig": "4.1", "label": "Orders per buyer per year"},
                    {"fig": "241,063", "label": "Completing buyers in window"}]},
        {"type": "paths", "eyebrow": "Paths", "title": "The path from clip to cart is short and repeatable.",
         "sub": "Example clickstream sequences from awards engagers in window.",
         "source": "Crosswalk Unique ID clickstream, Nov 10 2025 to Nov 10 2026.",
         "rows": [{"kind": "search", "url": "google.com/search?q=las+culturistas+culture+awards+winners"},
                  {"kind": "click", "url": "peacocktv.com/watch/las-culturistas-culture-awards"},
                  {"kind": "play", "url": "peacocktv.com/stream/las-culturistas-culture-awards-full-show", "lit": True},
                  {"kind": "click", "url": "nike.com/w/new-releases-sneakers"},
                  {"kind": "cart", "url": "nike.com/cart/checkout", "lit": True},
                  {"kind": "brand", "url": "Nike running shoes"}]},
        {"type": "argument", "ground": "light", "eyebrow": "Buy", "title": "Four categories where the file and the journeys agree.",
         "sub": "Where the audience composition and the purchase paths point at the same place.",
         "cards": [{"head": "Streaming and entertainment", "body": "Entertainment purchasing reaches 77.4% at index 147, and Peacock carries 4,157,804 of these viewers at index 269."},
                   {"head": "Beauty and personal care", "body": "Beauty buying reaches 56.5% at index 147, led by Bath & Body Works at 24.4% and CeraVe at 16.3%."},
                   {"head": "Apparel, footwear, accessories", "body": "Accessories index 143 and footwear 134, with Nike at 34.3% and Converse at 22.8% carrying the volume."},
                   {"head": "Travel and dining", "body": "Travel buying reaches 53.5% at index 128 and dining out 52.9% at index 129, in a file that is 57.8% without children."}]},
        {"type": "close", "eyebrow": "Close", "title": "A compact audience that pays back on every measured stage.",
         "cards": [{"head": "The scale is qualified", "body": "4,194,382 US viewers, with an avid core of 1,154,947 watching four or more times."},
                   {"head": "The demo is the premium one", "body": "68.5% hold a bachelors degree or higher and 45.5% earn $100,000 or more."},
                   {"head": "The creative works harder here", "body": "Awards-tagged units clear 2.41% CTR against a 0.97% general entertainment benchmark."},
                   {"head": "The purchase follows", "body": "Awards-path sessions convert at 11.8%, and each completing buyer is worth $386.51 a year."}]},
    ],
}


def fake_photo(w=900, h=1350):
    im = Image.new("RGB", (w, h), (120, 90, 60))
    for y in range(0, h, 40):
        for x in range(0, w, 40):
            if (x // 40 + y // 40) % 2:
                im.paste((200, 170, 110), (x, y, x + 40, y + 40))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def slide_xml_parts(path):
    z = zipfile.ZipFile(path)
    slides, sw, sh = ds._slide_parts(z)
    return z, slides, sw, sh


def check_deck(path, label):
    """The pack's three checks plus the structural assertions, on a saved deck."""
    from lxml import etree
    lint = run_tool("cw_brand_lint.py", path)
    m = re.search(r"(\d+) failure\(s\), (\d+) warning\(s\)", lint)
    check(f"[{label}] brand lint: zero FAIL", m is not None and m.group(1) == "0",
          (m.group(0) if m else lint[-300:]) + "\n" + "\n".join(l for l in lint.splitlines() if l.startswith("FAIL")))
    widow_warns = [l for l in lint.splitlines() if "widow" in l.lower()]
    check(f"[{label}] brand lint: no widow lines at all", not widow_warns, "\n".join(widow_warns[:5]))
    read = run_tool("readability_check.py", path)
    m2 = re.search(r"(\d+) flag\(s\)", read)
    check(f"[{label}] readability: zero flags", m2 is not None and m2.group(1) == "0",
          "\n".join(l for l in read.splitlines() if "FLAG" in l)[:800])
    emb = run_tool("cw_embed_fonts.py", path, "--verify")
    check(f"[{label}] fonts embedded (cw_embed_fonts --verify READY)", "READY" in emb, emb[-300:])

    z, slides, sw, sh = slide_xml_parts(path)
    names = z.namelist()
    bg_ok, fullpage, lowres, nbsp_bad, nbsp_seen = True, [], [], [], 0
    for si, sp in enumerate(slides, 1):
        root = etree.fromstring(z.read(sp))
        if root.find(f".//{NS_P}bg") is None:
            bg_ok = False
        for shp in root.iter(f"{NS_P}sp", f"{NS_P}pic"):
            off, ext = shp.find(f".//{NS_A}xfrm/{NS_A}off"), shp.find(f".//{NS_A}xfrm/{NS_A}ext")
            if off is None or ext is None:
                continue
            if int(ext.get("cx")) >= 0.95 * sw and int(ext.get("cy")) >= 0.95 * sh:
                fullpage.append(si)
        # images: pictures and the background blip
        rels_name = sp.replace("slides/", "slides/_rels/") + ".rels"
        rels = z.read(rels_name).decode("utf8", "ignore") if rels_name in names else ""
        rmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="\.\./media/([^"]+)"', rels))
        rmap.update({a: b for b, a in re.findall(r'Target="\.\./media/([^"]+)"[^>]*Id="(rId\d+)"', rels)})
        placed = []
        for pic in root.iter(f"{NS_P}pic"):
            blip, ext = pic.find(f".//{NS_A}blip"), pic.find(f".//{NS_A}xfrm/{NS_A}ext")
            if blip is not None and ext is not None:
                placed.append((blip.get(f"{NS_R}embed"), int(ext.get("cx")) / 914400))
        bgblip = root.find(f".//{NS_P}bg//{NS_A}blip")
        if bgblip is not None:
            placed.append((bgblip.get(f"{NS_R}embed"), sw / 914400))
        for rid, win in placed:
            media = rmap.get(rid)
            if not media:
                continue
            px = Image.open(io.BytesIO(z.read("ppt/media/" + media))).size[0]
            if px / win < 287:
                lowres.append((si, media, round(px / win)))
        for p_ in root.iter(f"{NS_A}p"):
            text = "".join(t.text or "" for t in p_.iter(f"{NS_A}t"))
            if "\u00a0" not in text:
                continue
            nbsp_seen += 1
            parts = text.split("\u00a0")
            if len(parts) != 2 or re.search(r"\s", parts[1].strip()) or not parts[0].strip() or not parts[1].strip():
                nbsp_bad.append((si, text[:70]))
    check(f"[{label}] every slide sets its page as the slide background (p:bg)", bg_ok)
    check(f"[{label}] no full-page shape or picture under the content", not fullpage, str(fullpage))
    check(f"[{label}] no image under 288 ppi", not lowres, str(lowres))
    check(f"[{label}] non-breaking space only between the last two words of a paragraph", not nbsp_bad,
          str(nbsp_bad[:4]))
    return lint, read, nbsp_seen


def paragraph_texts(prs):
    out = []
    for si, s in enumerate(prs.slides, 1):
        for sh in s.shapes:
            if sh.has_text_frame:
                for p in sh.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs)
                    if t.strip():
                        out.append((si, t))
    return out


# ------------------------------------------------------------------ [1..7] the fixture deck, text-only
print("[A] fixture deck, no photos")
db._PHOTO_CACHE.clear()
plan = copy.deepcopy(PLAN)
out = os.path.join(TMP, "fixture_text.pptx")
n = db.render_insights_deck(plan, out, static_dir=os.path.join(ROOT, "static"))
check("every slide type renders", n == len(PLAN["slides"]), f"({n})")
fin = plan.get("_finish") or {}
check("finish report rides on the plan for the job record",
      fin.get("ready") is True and fin.get("counts", {}).get("lint_fail") == 0
      and fin.get("counts", {}).get("readability_flags") == 0 and fin.get("copy_ok") is True
      and isinstance(fin.get("copy_notes"), list) and fin.get("copy_notes"), str(fin)[:400])
lint, read, nbsp_seen = check_deck(out, "text-only")
check("the builder bound at least one widow (the fixture copy carries them)", nbsp_seen >= 3, f"({nbsp_seen})")
check("the finisher net found nothing left to fix", not any(k.startswith("widow_") and not k.endswith("unresolved") for k in fin.get("fixes", {})) and "background_image" not in fin.get("fixes", {}) and "background_solid" not in fin.get("fixes", {}),
      str(fin.get("fixes")))

prs = Presentation(out)
paras = paragraph_texts(prs)
plain = {(si, t.replace("\u00a0", " ")) for si, t in paras}
# [5] subheads: defined once, then each slide's own content; titles and bodies untouched
sub_texts = [t.replace("\u00a0", " ") for si, t in paras if si in (4, 5, 7, 8)
             and ("penetration" in t.lower() or t.startswith(("Purchased categories", "Talent affinity", "Social platform usage", "Stated interests")))]
check("the subhead template no longer recurs",
      sum(1 for t in sub_texts if "with index against the average American" in t) == 0, str(sub_texts))
check("index and penetration are defined once, in plain words",
      any("index 100 = the average American" in t and "penetration is the share" in t for t in sub_texts), str(sub_texts))
check("later subheads name their own content",
      any(t.startswith("Purchased categories") and "77.4%" in t for t in sub_texts)
      and any(t.startswith("Talent affinity") and "Bowen Yang" in t for t in sub_texts)
      and any(t.startswith("Social platform usage") and "YouTube" in t for t in sub_texts), str(sub_texts))
check("every later subhead is distinct", len(set(sub_texts)) == len(sub_texts), str(sub_texts))
titles_kept = all((i + 1, sl["title"]) in plain for i, sl in enumerate(PLAN["slides"]))
check("every title is unchanged", titles_kept)
bodies_kept = all((i + 1, c["body"]) in plain for i, sl in enumerate(PLAN["slides"]) for c in sl.get("cards") or [])
check("every card body is unchanged", bodies_kept)
check("the caller's plan is not mutated",
      PLAN["slides"][4]["sub"] == TEMPLATE_SUB.format(lead="Purchased categories")
      and plan["slides"][4]["sub"] == TEMPLATE_SUB.format(lead="Purchased categories"))
check("a closer carried three times was rebuilt from the slide's figures",
      any("read" in n_ and "slide 10" in n_ for n_ in fin.get("copy_notes", [])), str(fin.get("copy_notes")))
check("the phrase now reads on two units at most",
      sum(1 for _si, t in plain if "awards or its hosts" in t) <= 2)

# [6] mono only on clickstream rows
paths_slide = prs.slides[13]
faces = {}
for sh in paths_slide.shapes:
    if sh.has_text_frame and sh.text_frame.text.strip():
        for p in sh.text_frame.paragraphs:
            for r in p.runs:
                faces[r.text] = r.font.name
check("Geist Mono on the clickstream address rows",
      faces.get("nike.com/cart/checkout") == "Geist Mono" and faces.get("CART") == "Geist Mono", str(faces))
check("Crosswalk Inter on a paths row that is a brand name, not an address",
      faces.get("Nike running shoes") == "Crosswalk Inter" and faces.get("BRAND") == "Crosswalk Inter", str(faces))

# [4] the pack's wrap model agrees with the builder's binding
mod = ds.lint_module()
check("the lint module is importable from the packaged pack", mod is not None)
fit = ds.widow_fit("1,908,444 viewers in the top three income bands", 11.5, 3.16, height_in=0.56,
                   face="Crosswalk Inter")
check("widow_fit binds the last two words with U+00A0",
      fit.text.endswith("income\u00a0bands") and fit.text.count("\u00a0") == 1, repr(fit.text))
lone, _n = ds.widow_lines(fit.text, fit.size_pt, fit.width_in, face="Crosswalk Inter")
check("and the pack's model then sees two words on the last line", not lone and fit.action in ("narrowed", "widened", "resized", "bound"),
      f"{fit.action} w={fit.width_in} size={fit.size_pt}")
check("a paragraph without a widow is returned untouched",
      ds.widow_fit("Four things a buyer needs from this profile, in order.", 16, 11.493, face="Crosswalk Inter").action == "none")
check("bind_last_two leaves a one-word paragraph alone", ds.bind_last_two("Nike") == "Nike")
check("bind_last_two binds only the final gap", ds.bind_last_two("a b c d") == "a b c\u00a0d")

# cover and close carry the pack's gradient as the slide background at full resolution
z, slides, sw, sh = slide_xml_parts(out)
from lxml import etree as _et
r1 = _et.fromstring(z.read(slides[0]))
check("the text-only cover carries an image background (the pack's graphite wash)",
      r1.find(f".//{NS_P}bg//{NS_A}blip") is not None)
gb = ds.gradient_image_bytes("graphiteWash")
check("the gradient is built at 3840 x 2160", gb is not None and Image.open(io.BytesIO(gb)).size == (3840, 2160))

# ------------------------------------------------------------------ [2][3] the fixture deck with photos
print("[B] fixture deck with photos (from the in-process cache, no network)")
db._PHOTO_CACHE["preship fixture subject|title"] = [fake_photo(900, 1350), fake_photo(1180, 965), fake_photo(1180, 965)]
plan2 = copy.deepcopy(PLAN)
out2 = os.path.join(TMP, "fixture_photo.pptx")
n2 = db.render_insights_deck(plan2, out2, static_dir=os.path.join(ROOT, "static"))
check("renders with photos", n2 == len(PLAN["slides"]))
check_deck(out2, "with photos")
z2, slides2, sw2, sh2 = slide_xml_parts(out2)
cover = _et.fromstring(z2.read(slides2[0]))
blip = cover.find(f".//{NS_P}bg//{NS_A}blip")
check("the photo is the cover's slide background, scrim baked in", blip is not None)
if blip is not None:
    rels = z2.read(slides2[0].replace("slides/", "slides/_rels/") + ".rels").decode("utf8")
    rmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="\.\./media/([^"]+)"', rels))
    im = Image.open(io.BytesIO(z2.read("ppt/media/" + rmap[blip.get(f"{NS_R}embed")]))).convert("RGB")
    mean = im.resize((1, 1)).getpixel((0, 0))
    check("baked background is 3840 x 2160 and darkened under the graphite scrim",
          im.size == (3840, 2160) and sum(mean) / 3 < 90, f"{im.size} mean={mean}")
check("no scrim rectangle remains on the cover",
      not any(sh.shape_type == 1 and Emu(sh.width).inches > 13 for sh in Presentation(out2).slides[0].shapes))

# ------------------------------------------------------------------ [8] the finisher's own net (any builder)
print("[C] the finisher net on a deck from another builder")
prs3 = Presentation()
prs3.slide_width, prs3.slide_height = Inches(13.333), Inches(7.5)
s1 = prs3.slides.add_slide(prs3.slide_layouts[6])
box = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
box.fill.solid(); box.fill.fore_color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(0x0C, 0x16, 0x18)
box.line.fill.background()
hd = s1.shapes.add_textbox(Inches(1), Inches(0.3), Inches(11), Inches(0.6))
hr = hd.text_frame.paragraphs[0].add_run()
hr.text = "The top income bands hold most of these viewers."     # a header, so the plain-English check has one to read
hr.font.size = Pt(28); hr.font.bold = True; hr.font.name = "Crosswalk Inter"
hr.font.color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(0xE9, 0xE8, 0xE1)
tb = s1.shapes.add_textbox(Inches(1), Inches(1), Inches(3.16), Inches(0.6))
tb.text_frame.word_wrap = True
tb.text_frame.margin_left = tb.text_frame.margin_right = 0
run = tb.text_frame.paragraphs[0].add_run()
run.text = "1,908,444 viewers in the top three income bands"
run.font.size = Pt(11.5); run.font.name = "Crosswalk Inter"
run.font.color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(0xE9, 0xE8, 0xE1)
s2 = prs3.slides.add_slide(prs3.slide_layouts[6])
s2.shapes.add_picture(io.BytesIO(fake_photo(900, 1350)), 0, 0, width=Inches(13.333), height=Inches(7.5))
veil = s2.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
veil.fill.solid(); veil.fill.fore_color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(0x0C, 0x16, 0x18)
veil.line.fill.background()
from pptx.oxml.ns import qn
srgb = veil.fill.fore_color._xFill.find(qn("a:srgbClr"))
srgb.append(srgb.makeelement(qn("a:alpha"), {"val": "64000"}))
tb2 = s2.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(0.6))
r2 = tb2.text_frame.paragraphs[0].add_run(); r2.text = "A closing line of copy."; r2.font.size = Pt(16); r2.font.name = "Crosswalk Inter"
r2.font.color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(0xE9, 0xE8, 0xE1)
out3 = os.path.join(TMP, "other_builder.pptx")
prs3.save(out3)
rep = ds.finish(out3)
check("finish reports the background and widow fixes it applied",
      rep.fixes.get("background_solid") == 1 and rep.fixes.get("background_image") == 1 and rep.fixes.get("widow_bound", 0) >= 1,
      str(rep.fixes))
check("finish is READY with zero lint FAIL and zero readability flags after the net",
      rep.ready and rep.copy_ok and rep.counts.get("lint_fail") == 0 and rep.counts.get("readability_flags") == 0
      and "readability: ok" in rep.summary, rep.summary)
z3, slides3, _, _ = slide_xml_parts(out3)
a1 = _et.fromstring(z3.read(slides3[0])); a2 = _et.fromstring(z3.read(slides3[1]))
check("the full-page rectangle became a solid slide background",
      a1.find(f".//{NS_P}bg//{NS_A}solidFill/{NS_A}srgbClr") is not None
      and not [s for s in a1.iter(f"{NS_P}sp") if s.find(f".//{NS_A}xfrm/{NS_A}ext") is not None and int(s.find(f".//{NS_A}xfrm/{NS_A}ext").get("cx")) >= 0.95 * sw])
check("the photo under its scrim became one background image",
      a2.find(f".//{NS_P}bg//{NS_A}blip") is not None and not list(a2.iter(f"{NS_P}pic")))
check("job_record carries ready, counts, fixes and steps",
      set(rep.job_record()) >= {"ready", "copy_ok", "counts", "fixes", "steps", "skipped"})

print(f"\n{FAIL} failure(s)")
sys.exit(1 if FAIL else 0)
