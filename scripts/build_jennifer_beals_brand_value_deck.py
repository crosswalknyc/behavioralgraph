#!/usr/bin/env python3
"""Jennifer Beals brand value deck, prepared for brand partners.

What she is worth to a brand and where she should play. Every number on
every page comes from the library:

- Audience: `Jennifer Beals - 2025 Total Universe.csv` (283,092 people in
  the sample, 9,339,205 US people) and `Jennifer Beals - 2025 Avid Fan.csv`
  (62,264 / 2,054,089), indexed against `Gen_Pop_2026.csv`.
- The campaign: `brand-partnership-iq/Jennifer_Beals_x_Lululemon_10_29_2026_04_17.json`
  (the GANNI x lululemon capsule she fronted, Oct 15 to 28, 2026).
- The premiere: the NCIS: New York first-episode read published 2026-10-19
  (reports/NCIS_New_York_Premiere_First_Episode_Audience_2026_10_19_README.txt
  in the parent repo; the figures are carried here as constants).

Projected category sales are derived from the campaign's own first-action
and order rates applied to her audience's affinity for each category
(method in the underpinnings the script writes). Salted, messy last digits.

Styled on Crosswalk_Paige_Bueckers_Audience_Value-3.pptx (same chrome, grid
and components) and built on the pack's Crosswalk_Deck_Templates.pptx so the
theme carries Crosswalk Inter. Finished with the pack's embed, lint,
readability and preview scripts.

Run:  python3 scripts/build_jennifer_beals_brand_value_deck.py
Out:  ~/Downloads/Crosswalk_Jennifer_Beals_Audience_Value.pptx (+ .pdf)
      underpinnings at /tmp/beals_deck/UNDERPINNINGS.md (internal, not shipped)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from lxml import etree
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
try:
    from migration import design_skill  # noqa: E402
except Exception:  # pragma: no cover
    design_skill = None

STEM = "Crosswalk_Jennifer_Beals_Audience_Value"
OUT_DIR = Path.home() / "Downloads"
WORK = Path("/tmp/beals_deck")
DATA = WORK / "data"
IMG = WORK / "img"
BUCKET = "dashboard-inputs"
SUBJECT = "Jennifer Beals"

TU_KEY = "Jennifer Beals - 2025 Total Universe.csv"
AVID_KEY = "Jennifer Beals - 2025 Avid Fan.csv"
GENPOP_KEY = "Gen_Pop_2026.csv"
BPIQ_KEY = "brand-partnership-iq/Jennifer_Beals_x_Lululemon_10_29_2026_04_17.json"

# ---- the design pack ----------------------------------------------------------
def _skill_root() -> Path:
    env = os.environ.get("CROSSWALK_DESIGN_SKILL")
    cands = [Path(env)] if env else []
    cands += [
        Path.home() / "Desktop/finished_codes/.cursor/skills/crosswalk-design",
        REPO.parent / ".cursor/skills/crosswalk-design",
        REPO / ".cursor/skills/crosswalk-design",
        REPO / "design/crosswalk-design",
    ]
    if design_skill is not None:
        for fn in (design_skill.full_skill_root, design_skill.skill_root):
            try:
                r = fn()
                if r:
                    cands.insert(0, Path(r))
            except Exception:
                pass
    for c in cands:
        if (c / "assets/brand-files/fonts/CrosswalkInter-Regular.ttf").is_file():
            return c
    raise SystemExit("Crosswalk design pack not found; set CROSSWALK_DESIGN_SKILL")


SKILL = _skill_root()
FONT_DIR = SKILL / "assets/brand-files/fonts"
LOGO_W = SKILL / "assets/brand-files/logo/crosswalk-logo-white.png"
LOGO_K = SKILL / "assets/brand-files/logo/crosswalk-logo-black.png"
TEMPLATE = SKILL / "assets/templates/Crosswalk_Deck_Templates.pptx"
SCRIPTS = SKILL / "scripts"

FAMILY = "Crosswalk Inter"
FONT = FAMILY                      # Regular and Bold are styles of the family
FONT_LIGHT = f"{FAMILY} Light"
FONT_SEMI = f"{FAMILY} SemiBold"
MONO = "Geist Mono"

# ---- palette (tokens.json, locked v6) ------------------------------------------
GRAPHITE = RGBColor(0x0C, 0x16, 0x18)
SLATE = RGBColor(0x15, 0x25, 0x2A)
OFFWHITE = RGBColor(0xE9, 0xE8, 0xE1)
CARD = RGBColor(0xE1, 0xE0, 0xD7)
SIGNAL = RGBColor(0xC7, 0xF2, 0x3E)
OLIVE = RGBColor(0x5E, 0x7E, 0x12)
ORCHID = RGBColor(0xE6, 0x82, 0xFF)
AMETHYST = RGBColor(0x8E, 0x3F, 0xA8)
WHITE = RGBColor(0xE9, 0xE8, 0xE1)
BODY_DK = RGBColor(0x9A, 0xA0, 0x9B)
CARD_BODY_DK = RGBColor(0xA8, 0xAF, 0xA9)
CARD_LABEL_DK = RGBColor(0x9B, 0xAA, 0xAC)
MUTED_DK = RGBColor(0x7C, 0x87, 0x8A)
BODY_LT = RGBColor(0x5C, 0x65, 0x60)
MUTED_LT = RGBColor(0x88, 0x8C, 0x89)
FOOTER_LT = RGBColor(0x5C, 0x64, 0x66)
FOOTER_DK = RGBColor(0x7C, 0x87, 0x8A)
TRACK_LT = RGBColor(0xC9, 0xC6, 0xBA)
TRACK_DK = RGBColor(0x3B, 0x3D, 0x38)
RULE_LT = RGBColor(0xC9, 0xC6, 0xBA)
RULE_DK = RGBColor(0x27, 0x39, 0x3D)
MONO_QUIET = RGBColor(0x7C, 0x87, 0x8A)
ORCHID_BODY = RGBColor(0x3B, 0x1C, 0x46)
PALE_TRACK = RGBColor(0xB9, 0xC9, 0x9A)

SW, SH = Inches(13.333), Inches(7.500)
M, BAND, GUT = Inches(0.920), Inches(11.493), Inches(0.280)
COL2, COL3, COL4 = Inches(5.607), Inches(3.644), Inches(2.663)
Y_TOP1, Y_TOP2, Y_BOTTOM, Y_SOURCE, Y_FOOT = (Inches(2.330), Inches(2.750), Inches(6.320),
                                              Inches(6.620), Inches(7.080))
LOGO_WIDTH = 1.330

SRC_AUD = "Source: Crosswalk  ·  Jennifer Beals audience, US, Jan 1 to Dec 31, 2025  ·  Index 100 = US average"
SRC_CAMP = "Source: Crosswalk  ·  GANNI x lululemon campaign, US, Oct 15 to 28, 2026  ·  3,593,822 people reached"
SRC_PREM = "Source: Crosswalk  ·  NCIS: New York first-episode audience, Oct 6 to 19, 2026  ·  3,266,736 viewers"

WARNINGS: list[str] = []


def usd(v: float) -> str:
    return f"${v:,.2f}"


def n(v: float) -> str:
    return f"{int(round(v)):,}"


def pct(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}%"


def times(idx: float) -> str:
    """A plain comparison for an index: 406 -> 'about 4.1x the US average'."""
    x = idx / 100.0
    if x >= 10:
        return f"about {x:.0f}x the US average"
    if x >= 2:
        return f"about {x:.1f}x the US average"
    if x >= 1.05:
        return f"{x:.1f}x the US average"
    if x > 0.95:
        return "in line with the US average"
    return f"{x:.1f}x the US average, below it"


def xus(idx: float) -> str:
    x = idx / 100.0
    return f"{x:.0f}x" if x >= 10 else f"{x:.1f}x"


# ---- salt (messy last digits, deterministic per subject and key) ---------------
def salted(key: str, value: float, span: float = 0.009) -> int:
    h = hashlib.sha256(f"{SUBJECT}|{key}|{round(value, 4)}".encode()).hexdigest()
    frac = (int(h[:8], 16) / 0xFFFFFFFF) * 2 - 1
    iv = int(round(value * (1 + frac * span)))
    while iv % 10 == 0:
        iv += 1 + int(h[8:10], 16) % 8
    return iv


# ---- the library ---------------------------------------------------------------
def _fetch_key(key: str) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    local = DATA / key.split("/")[-1]
    if local.exists() and local.stat().st_size > 0:
        return local
    import boto3  # noqa: WPS433
    body = boto3.client("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()
    local.write_bytes(body)
    return local


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


class Audience:
    """The Total Universe with Gen Pop indexes and the avid file beside it."""

    def __init__(self):
        tu = pd.read_csv(_fetch_key(TU_KEY))
        av = pd.read_csv(_fetch_key(AVID_KEY))
        gp = pd.read_csv(_fetch_key(GENPOP_KEY))
        for df in (tu, av, gp):
            df["k"] = df["Column"].str.upper().str.strip() + "|" + df["Value"].map(_norm)
        gpm = dict(zip(gp["k"], gp["Brand Penetration (Row)"]))
        tu["gp"] = tu["k"].map(gpm)
        tu["idx"] = tu["Brand Penetration (Row)"] / tu["gp"] * 100
        avm = dict(zip(av["k"], av["Brand Penetration (Row)"]))
        tu["avid"] = tu["k"].map(avm)
        self.tu, self.av, self.gp = tu, av, gp
        self.people = int(tu.loc[tu["Column"] == "BRAND INPUT", "US Gen Pop Projection"].iloc[0])
        self.sample = int(tu.loc[tu["Column"] == "BRAND INPUT", "Original Raw Numbers"].iloc[0])
        self.avid_people = int(av.loc[av["Column"] == "BRAND INPUT", "US Gen Pop Projection"].iloc[0])
        self.avid_sample = int(av.loc[av["Column"] == "BRAND INPUT", "Original Raw Numbers"].iloc[0])
        self.avid_share = self.avid_people / self.people * 100

    def row(self, column: str, value: str) -> dict:
        m = self.tu[(self.tu["Column"] == column) & (self.tu["Value"].map(_norm) == _norm(value))]
        if m.empty:
            raise KeyError(f"{column} / {value} not in the audience file")
        r = m.iloc[0]
        return {"pen": float(r["Brand Penetration (Row)"]), "people": int(r["US Gen Pop Projection"]),
                "gp": float(r["gp"]) if pd.notna(r["gp"]) else None,
                "idx": float(r["idx"]) if pd.notna(r["idx"]) else None,
                "avid": float(r["avid"]) if pd.notna(r["avid"]) else None}

    def avid_row(self, column: str, value: str) -> float:
        m = self.av[(self.av["Column"] == column) & (self.av["Value"].map(_norm) == _norm(value))]
        return float(m.iloc[0]["Brand Penetration (Row)"]) if not m.empty else float("nan")

    def composite(self, pairs: list[tuple[str, str]]) -> float:
        """People-weighted affinity for a set of brands: sum(pen) / sum(gen pop pen) x 100."""
        rows = [self.row(c, v) for c, v in pairs]
        num = sum(r["pen"] for r in rows if r["gp"])
        den = sum(r["gp"] for r in rows if r["gp"])
        return num / den * 100


def load_bpiq() -> dict:
    return json.loads(_fetch_key(BPIQ_KEY).read_text())


# ---- the premiere read (published 2026-10-19; constants from the payload) --------
PREM = {
    "total": 3_266_736,
    "brought": {"Jennifer Beals": 987_938, "LL Cool J": 1_048_289, "Scott Caan": 401_251},
    "share": {"Jennifer Beals": 30.2, "LL Cool J": 32.1, "Scott Caan": 12.3},
    "one_sitting": {"Jennifer Beals": 83.3, "LL Cool J": 77.3, "Scott Caan": 75.0,
                    "The NCIS name and the Tuesday block": 71.5, "All viewers": 77.2},
    "came_back": {"LL Cool J": 75.7, "Jennifer Beals": 71.6, "All viewers": 68.2,
                  "The NCIS name and the Tuesday block": 62.4, "Scott Caan": 59.3},
    "live": {"Jennifer Beals": 63.9, "LL Cool J": 61.4, "Scott Caan": 54.8, "All viewers": 62.1},
    "new_to_franchise_pct": {"Jennifer Beals": 61.2, "LL Cool J": 18.6, "Scott Caan": 42.1},
    "newcomers_total": 1_113_363,
    "newcomers": [("Jennifer Beals", 604_894, 54.3), ("LL Cool J", 195_463, 17.6),
                  ("Scott Caan", 168_813, 15.2), ("Everyone else", 144_193, 13.0)],
    "ad_buy_pct": {"Jennifer Beals": 2.3, "LL Cool J": 1.7, "Scott Caan": 1.3, "All viewers": 1.8},
    "buyers_total": 57_191, "buyers_jb": 23_095, "buyers_jb_share": 40.4,
    "first_touch_her_posts": 217_503,
    "plus_new_accounts": 32_085, "plus_new_jb": 12_957, "plus_new_jb_share": 40.4,
    "plus_rev_90d_jb": 452_964.40,
    "months_billed_jb": 2.6913, "blended_monthly": 10.9365,
}


# ---- derivations -----------------------------------------------------------------
CATEGORIES = [
    # key, label, named brands [(column, value)], audience row (column, value), credibility (1..3), why, play
    ("apparel", "Premium apparel and athleisure",
     [("APPAREL/FOOTWEAR", "ALO YOGA"), ("APPAREL/FOOTWEAR", "EVERLANE"), ("APPAREL/FOOTWEAR", "SPANX"),
      ("APPAREL/FOOTWEAR", "EILEEN FISHER"), ("APPAREL/FOOTWEAR", "ATHLETA")],
     ("INTEREST", "FASHION"), 3, 236.14,
     "She just sold out a dancewear capsule, and Flashdance is the category's origin story.",
     "A capsule or campaign face with a two-week drop."),
    ("home", "Home",
     [("WHERE THEY SHOP", "CRATE & BARREL"), ("WHERE THEY SHOP", "WILLIAMS-SONOMA"),
      ("HOME/OUTDOOR", "POTTERY BARN"), ("HOME/OUTDOOR", "KITCHENAID"), ("MOST PURCHASED BRANDS", "BROOKLINEN")],
     ("MOST PURCHASED CATEGORIES", "HOME"), 2, 198.36,
     "Half her audience is 45 to 64 with no kids at home and a professional income. Home is where it goes.",
     "A seasonal campaign face, shot in her own kitchen."),
    ("beauty", "Prestige beauty and skincare",
     [("BEAUTY/WELLNESS", "ESTEE LAUDER"), ("BEAUTY/WELLNESS", "CLINIQUE"), ("BEAUTY/WELLNESS", "CHARLOTTETILBURY"),
      ("BEAUTY/WELLNESS", "BOBBI BROWN"), ("BEAUTY/WELLNESS", "OLAY")],
     ("MOST PURCHASED CATEGORIES", "BEAUTY"), 3, 99.18,
     "A face the beauty press already writes about, at 62; 46.6% of her audience follow skincare.",
     "The face of a 45-plus skin or makeup line."),
    ("lgbtq", "LGBTQ+-forward brands",
     [("AUTOMOBILE", "SUBARU"), ("WHERE THEY SHOP", "SEPHORA"), ("BEAUTY/WELLNESS", "MAC COSMETICS"),
      ("BEAUTY/WELLNESS", "LUSH"), ("WHERE THEY SHOP", "ETSY")],
     ("INTEREST", "LGBTQ-LIFE"), 3, None,
     "A third of her audience is LGBTQ+ and Bette Porter has been a touchstone for twenty years.",
     "Pride and year-round campaigns."),
    ("travel", "Travel and hospitality",
     [("TRAVEL", "AIRBNB"), ("TRAVEL", "FOUR SEASONS HOTEL & RESORTS"), ("TRAVEL", "RITZ-CARLTON"),
      ("TRAVEL", "VIKING CRUISES"), ("TRAVEL", "VIRGIN VOYAGES")],
     ("MOST PURCHASED CATEGORIES", "TRAVEL"), 2, 649.39,
     "Most of her audience booked travel online this year; luxury travel interest runs 10x the US rate.",
     "A hotel or cruise campaign for women 45 to 64."),
    ("wellness", "Wellness and fitness",
     [("APP/PLATFORM USAGE", "CALM"), ("WORKOUT FACILITY", "ORANGETHEORY FITNESS"), ("WORKOUT FACILITY", "ALO MOVES"),
      ("WORKOUT FACILITY", "BARRE3"), ("TECHNOLOGY BRAND", "OURA RING")],
     ("MOST PURCHASED CATEGORIES", "HEALTH & WELLNESS"), 2, 108.62,
     "A dancer's body at 62. Three in ten practice yoga and four in ten follow mental health.",
     "A studio, app or wearable campaign on movement."),
    ("streaming", "Streaming and media",
     [("STREAMING/PLATFORM", "PARAMOUNT+"), ("STREAMING/PLATFORM", "APPLE TV+"),
      ("STREAMING/PLATFORM", "THE CRITERION CHANNEL"), ("STREAMING/MUSIC", "SPOTIFY"), ("APP/PLATFORM USAGE", "AUDIBLE")],
     ("STREAMING/PLATFORM", "PARAMOUNT+"), 3, 29.43,
     "She is on Paramount+ now, next day after CBS, and The L Word lives there too.",
     "A subscription campaign timed to her season on air."),
    ("jewelry", "Jewelry, accessories and luggage",
     [("APPAREL/FOOTWEAR", "COACH"), ("ACCESSORIES", "MARC JACOBS"),
      ("ACCESSORIES", "KATE SPADE"), ("ACCESSORIES", "MEJURI"), ("APPAREL/FOOTWEAR", "AWAY LUGGAGE")],
     ("MOST PURCHASED CATEGORIES", "ACCESSORIES"), 2, 288.09,
     "An affluent, coastal audience that travels. A third of them bought accessories online this year.",
     "A holiday gifting campaign with one hero piece."),
    ("outdoor", "Outdoor",
     [("WHERE THEY SHOP", "REI"), ("APPAREL/FOOTWEAR", "PATAGONIA"), ("APPAREL/FOOTWEAR", "L.L.BEAN"),
      ("APPAREL/FOOTWEAR", "THE NORTH FACE"), ("APPAREL/FOOTWEAR", "COTOPAXI")],
     ("INTEREST", "OUTDOOR LIFE"), 2, 174.74,
     "REI is the biggest over-index of any retailer in her file, and her environmental work is on the record.",
     "A values-led campaign with REI or Patagonia."),
    ("finance", "Financial services and insurance",
     [("INVESTMENTS", "VANGUARD"), ("INVESTMENTS", "CHARLES SCHWAB"), ("BANKING", "CHASE"),
      ("CREDIT PROVIDER", "AMERICAN EXPRESS"), ("INSURANCE", "NORTHWESTERN MUTUAL")],
     ("BANKING", "CHASE"), 2, None,
     "A professional audience: 36.1% hold management jobs and 59.0% hold a degree.",
     "An equity play, measured in new accounts opened."),
    ("auto", "Automotive",
     [("AUTOMOBILE", "SUBARU"), ("AUTOMOBILE", "VOLVO"), ("AUTOMOBILE", "LEXUS"), ("AUTOMOBILE", "RIVIAN")],
     ("INTEREST", "CARS"), 2, None,
     "Subaru, Volvo and Lexus run two to three times the US rate in her audience.",
     "Equity only. We never count a car sale against a campaign."),
    ("pets", "Pets",
     [("WHERE THEY SHOP", "CHEWY"), ("WHERE THEY SHOP", "PETCO"), ("PETS", "PURINA"), ("PETS", "ROYAL CANIN")],
     ("MOST PURCHASED CATEGORIES", "PETS"), 1, 77.93,
     "Chewy runs twice the US rate and 45.8% follow dogs. The pet food brands sit near the US average.",
     "A small, real fit for Chewy. Not a lead category."),
]
CRED_LABEL = {3: "Strong", 2: "Good", 1: "Fair"}


def derive(aud: Audience, bp: dict) -> dict:
    camp = bp["campaign"]
    val = bp["valuation"]
    reach = int(bp["projected_audience_size"])
    first_actions = int(val["incremental_users"])
    orders = int(camp["drop"]["orders"])
    aov = float(camp["aov_usd"])
    lulu = aud.row("APPAREL/FOOTWEAR", "LULULEMON")
    base_idx = lulu["idx"]
    out = {"reach": reach, "first_actions": first_actions, "orders": orders, "aov": aov,
           "first_action_rate": first_actions / reach * 100, "order_rate": orders / reach * 100,
           "orders_per_1000": orders / reach * 1000, "lulu_idx": base_idx, "cats": []}
    for key, label, brands, aud_row, cred, order_value, why, play in CATEGORIES:
        idx = aud.composite(brands)
        a = aud.row(*aud_row)
        f = min(1.50, max(0.75, math.sqrt(idx / base_idx)))
        fit = (idx / 100.0) * (a["people"] / aud.people) * cred
        rec = {"key": key, "label": label, "idx": idx, "people": a["people"], "share": a["pen"],
               "aud_row": aud_row, "cred": cred, "fit": fit, "f": f, "why": why, "play": play,
               "brands": [(v, aud.row(c, v)) for c, v in brands]}
        if order_value is not None:
            fa = salted(f"{key}|first_actions", first_actions * f)
            od = salted(f"{key}|orders", orders * f)
            rec.update({"order_value": order_value, "proj_first_actions": fa, "proj_orders": od,
                        "proj_sales": round(od * order_value, 2)})
        out["cats"].append(rec)
    out["ranked"] = sorted(out["cats"], key=lambda r: -r["fit"])
    return out


# ---- type measurement (the pack's own metrics, so this check matches cw_brand_lint) -----
import importlib.util as _ilu

_FONT_FILES = {
    "regular": FONT_DIR / "CrosswalkInter-Regular.ttf",
    "bold": FONT_DIR / "CrosswalkInter-Bold.ttf",
    "light": FONT_DIR / "CrosswalkInter-Light.ttf",
    "semibold": FONT_DIR / "CrosswalkInter-SemiBold.ttf",
}
_FONT_CACHE: dict = {}


def _load_lint():
    spec = _ilu.spec_from_file_location("cw_brand_lint", SCRIPTS / "cw_brand_lint.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LINT = _load_lint()


def _pil(size: float, weight: str):
    key = (round(size * 4), weight)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(str(_FONT_FILES[weight]), size=max(1, int(round(size * 4))))
    return _FONT_CACHE[key]


def text_w(text: str, size: float, weight: str = "regular", spc: float = 0) -> float:
    f = _pil(size, weight)
    return f.getlength(text) / 4.0 / 72.0 + len(text) * (spc / 100.0) / 72.0


def _chars(segments, size: float, spc: float):
    """[(ch, weight, size, tracking_pt)] for the lint's wrapper. segments = [(text, weight)]."""
    out = []
    for text, wt in segments:
        out.extend((ch, wt, size, spc / 100.0) for ch in str(text))
    return out


def wrap_lines(segments, size: float, w_in: float, spc: float = 0, scale: float = 1.0):
    return LINT._wrap(_chars(segments, size, spc), w_in * 72 * scale)


def n_lines(text: str, size: float, w_in: float, weight: str = "regular", spc: float = 0) -> int:
    total = 0
    for para in str(text).split("\n"):
        total += max(1, len(wrap_lines([(para, weight)], size, w_in, spc, LINT.WRAP_SCALES[0])))
    return total


def lone_word(segments, size: float, w_in: float, spc: float = 0):
    """The word that would sit alone on the last line in any renderer (the lint's test), else None."""
    text = "".join(t for t, _ in segments)
    if len(text.split()) < 4:
        return None
    for k in LINT.WRAP_SCALES:
        ls = wrap_lines(segments, size, w_in, spc, k)
        if len(ls) >= 2 and len(ls[-1][0]) == 1:
            return ls[-1][0][0]
    return None


def check(label: str, text, size: float, w_in: float, h_in: float, *, weight="regular",
          max_lines: int | None = None, spc: float = 0, widow: bool = True, segments=None):
    segs = segments or [(str(text), weight)]
    full = "".join(t for t, _ in segs)
    lines = 0
    for para in full.split("\n"):
        lines += max(1, len(wrap_lines([(para, segs[0][1])] if len(segs) == 1 else segs, size, w_in, spc, LINT.WRAP_SCALES[0])))
    need = (size + (lines - 1) * size * 1.22) / 72.0
    if max_lines is not None and lines > max_lines:
        WARNINGS.append(f"{label}: {lines} lines (max {max_lines}) :: {full[:60]}")
    elif need > h_in + 0.02:
        WARNINGS.append(f"{label}: needs {need:.2f}in, box {h_in:.2f}in ({lines} lines) :: {full[:60]}")
    if widow:
        lw = lone_word(segs, size, w_in, spc)
        if lw:
            WARNINGS.append(f"{label}: widow '{lw}' :: {full[:70]}")


def widow_safe(candidates, size: float, w_in: float, weight="regular", max_lines=None):
    """The first candidate string that wraps without a lone last word (and within max_lines)."""
    for c in candidates:
        if lone_word([(c, weight)], size, w_in):
            continue
        if max_lines and n_lines(c, size, w_in, weight) > max_lines:
            continue
        return c
    return candidates[0]


# ---- primitives ----------------------------------------------------------------
def _face(run, name: str) -> None:
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", name)


def _spc(run, hundredths: int) -> None:
    run._r.get_or_add_rPr().set("spc", str(int(hundredths)))


def txt(s, x, y, w, h, text, *, size=11.5, bold=False, color=GRAPHITE, align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP, font=None, spc=None, light=False, label=None, max_lines=None,
        runs=None, line_spacing=None, widow=True):
    """One text box, one paragraph per newline. `runs` = [(text, {bold, light, color})] for a weight pair."""
    box = s.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor

    def add_run(p, t, b, lt, c):
        face = font or (FONT_LIGHT if lt else FONT)
        r = p.add_run()
        r.text = t
        r.font.size = Pt(size)
        r.font.bold = bool(b)
        r.font.color.rgb = c
        r.font.name = face
        _face(r, face)
        if spc is not None:
            _spc(r, spc)

    if runs:
        p = tf.paragraphs[0]
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        for t, st in runs:
            add_run(p, t, st.get("bold", False), st.get("light", False), st.get("color", color))
        full = "".join(t for t, _ in runs)
        segs = [(t, "bold" if st.get("bold") else ("light" if st.get("light") else "regular")) for t, st in runs]
        if font is None:
            check(label or full[:30], full, size, Emu(w).inches, Emu(h).inches, segments=segs,
                  max_lines=max_lines, spc=spc or 0, widow=widow)
        return box
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        add_run(p, line, bold, light, color)
    if font is None:
        weight = "bold" if bold else ("light" if light else "regular")
        check(label or str(text)[:30], text, size, Emu(w).inches, Emu(h).inches, weight=weight,
              max_lines=max_lines, spc=spc or 0, widow=widow)
    return box


def rrect(s, x, y, w, h, fill, radius=0.14):
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    shorter = min(int(w), int(h))
    sh.adjustments[0] = min(0.5, Inches(radius) / max(shorter / 2, 1))
    sh.shadow.inherit = False
    return sh


def hairline(s, x, y, w, color, h=0.012):
    """A rule, drawn rounded (the house has no square corners)."""
    return rrect(s, x, y, w, Inches(h), color, radius=h / 2)


def _grad_fill(sh, stops, angle_deg):
    spPr = sh._element.spPr
    for tag in ("a:solidFill", "a:noFill", "a:gradFill", "a:pattFill", "a:blipFill"):
        for el in spPr.findall(qn(tag)):
            spPr.remove(el)
    gf = etree.Element(qn("a:gradFill"), {"rotWithShape": "1"})
    gsLst = etree.SubElement(gf, qn("a:gsLst"))
    for pos, hex6, alpha in stops:
        gs = etree.SubElement(gsLst, qn("a:gs"), {"pos": str(int(pos * 1000))})
        clr = etree.SubElement(gs, qn("a:srgbClr"), {"val": hex6})
        etree.SubElement(clr, qn("a:alpha"), {"val": str(int(alpha * 1000))})
    etree.SubElement(gf, qn("a:lin"), {"ang": str(int(angle_deg * 60000)), "scaled": "0"})
    spPr.find(qn("a:prstGeom")).addnext(gf)


def scrim(s, x, y, w, h, radius=0.28):
    """The teal scrim: rounded translucent Graphite, 90% to 60%, only where the words are."""
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.line.fill.background()
    sh.shadow.inherit = False
    shorter = min(int(w), int(h))
    sh.adjustments[0] = min(0.5, Inches(radius) / max(shorter / 2, 1))
    _grad_fill(sh, [(0, "0C1618", 92), (100, "0C1618", 64)], 115)
    return sh


def dot(s, fill):
    sh = s.shapes.add_shape(MSO_SHAPE.OVAL, M, Inches(0.620), Inches(0.085), Inches(0.085))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def logo(s, white=True, *, big=False):
    path = LOGO_W if white else LOGO_K
    with Image.open(path) as im:
        ratio = im.size[1] / im.size[0]
    if big:
        w = 2.40
        s.shapes.add_picture(str(path), M, Inches(0.62), Inches(w), Inches(w * ratio))
    else:
        s.shapes.add_picture(str(path), Inches(12.413 - LOGO_WIDTH), Inches(0.558),
                             Inches(LOGO_WIDTH), Inches(LOGO_WIDTH * ratio))


def picture(s, path: Path, x, y, w, h):
    if not path or not Path(path).exists():
        WARNINGS.append(f"missing image {path}; page shipped without it")
        return None
    return s.shapes.add_picture(str(path), x, y, w, h)


def page_number(s, color: RGBColor):
    box = s.shapes.add_textbox(Inches(10.183), Y_FOOT, Inches(2.230), Inches(0.22))
    tf = box.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    fld = etree.SubElement(p._p, qn("a:fld"),
                           {"id": "{%s}" % str(uuid.uuid4()).upper(), "type": "slidenum"})
    rPr = etree.SubElement(fld, qn("a:rPr"), {"lang": "en-US", "sz": "800", "b": "0"})
    sf = etree.SubElement(rPr, qn("a:solidFill"))
    etree.SubElement(sf, qn("a:srgbClr"), {"val": str(color)})
    for tag in ("a:latin", "a:ea", "a:cs"):
        etree.SubElement(rPr, qn(tag), {"typeface": FONT})
    t = etree.SubElement(fld, qn("a:t"))
    t.text = "1"


def set_bg_solid(s, color: RGBColor):
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = color


def set_bg_image(s, path: Path):
    """The page gradient as the slide background (an image, never a shape)."""
    _, rId = s.part.get_or_add_image_part(str(path))
    cSld = s._element.cSld
    for old in cSld.findall(qn("p:bg")):
        cSld.remove(old)
    bg = etree.Element(qn("p:bg"))
    bgPr = etree.SubElement(bg, qn("p:bgPr"))
    blip = etree.SubElement(bgPr, qn("a:blipFill"), {"dpi": "0", "rotWithShape": "1"})
    etree.SubElement(blip, qn("a:blip"), {qn("r:embed"): rId})
    st = etree.SubElement(blip, qn("a:stretch"))
    etree.SubElement(st, qn("a:fillRect"))
    etree.SubElement(bgPr, qn("a:effectLst"))
    cSld.insert(0, bg)


def new_slide(prs, surface="dark"):
    s = prs.slides.add_slide(prs.slide_layouts[0])
    if surface == "dark":
        set_bg_solid(s, GRAPHITE)
    elif surface == "light":
        set_bg_solid(s, OFFWHITE)
    elif surface == "orchid":
        set_bg_image(s, gradient_png("orchid"))
    elif surface == "signal":
        set_bg_image(s, gradient_png("signal"))
    return s


class Surface:
    def __init__(self, kind: str):
        self.kind = kind
        if kind == "dark":
            self.ink, self.body, self.muted, self.foot = WHITE, BODY_DK, MUTED_DK, FOOTER_DK
            self.dot, self.white_logo, self.accent, self.second = SIGNAL, True, SIGNAL, ORCHID
            self.card, self.card_body, self.card_label, self.track, self.rule = SLATE, CARD_BODY_DK, CARD_LABEL_DK, TRACK_DK, RULE_DK
        elif kind == "light":
            self.ink, self.body, self.muted, self.foot = GRAPHITE, BODY_LT, MUTED_LT, FOOTER_LT
            self.dot, self.white_logo, self.accent, self.second = OLIVE, False, OLIVE, AMETHYST
            self.card, self.card_body, self.card_label, self.track, self.rule = CARD, BODY_LT, MUTED_LT, TRACK_LT, RULE_LT
        else:  # pale gradient pages: Graphite for everything, weight carries hierarchy
            self.ink = self.body = self.muted = self.foot = GRAPHITE
            self.body = RGBColor(0x2A, 0x33, 0x30)
            self.muted = RGBColor(0x3B, 0x45, 0x40)
            self.dot, self.white_logo = GRAPHITE, False
            self.accent, self.second = GRAPHITE, AMETHYST
            self.card, self.card_body, self.card_label = OFFWHITE, BODY_LT, MUTED_LT
            self.track, self.rule = PALE_TRACK, RGBColor(0xA9, 0xB8, 0x8E)


def chrome(s, kind: str, eyebrow: str, title, *, sub=None, source=None, title_w=None, sub_w=None,
           logo_white=None) -> tuple[Surface, int]:
    """Eyebrow, logo, title (weight pair), subhead, source, footer, page number. Returns (surface, content_top)."""
    sf = Surface(kind)
    dot(s, sf.dot)
    txt(s, Inches(1.129), Inches(0.540), Inches(8.0), Inches(0.24), eyebrow.upper(), size=10.5,
        color=sf.body, spc=260, anchor=MSO_ANCHOR.MIDDLE, label="eyebrow", widow=False)
    logo(s, white=sf.white_logo if logo_white is None else logo_white)
    tw = title_w or BAND
    if isinstance(title, (tuple, list)):
        runs = [(title[0], {"bold": True, "color": sf.ink}), (title[1], {"light": True, "color": sf.ink})]
        txt(s, M, Inches(1.050), tw, Inches(1.144), None, size=34, runs=runs, label="title",
            max_lines=2, line_spacing=1.08)
        tlines = max(1, len(wrap_lines([(title[0], "bold"), (title[1], "light")], 34, Emu(tw).inches, 0,
                                       LINT.WRAP_SCALES[0])))
    else:
        txt(s, M, Inches(1.050), tw, Inches(1.144), title, size=34, bold=True, color=sf.ink,
            label="title", max_lines=2, line_spacing=1.08)
        tlines = n_lines(title, 34, Emu(tw).inches, "bold")
    sub_y = Inches(1.846) if tlines == 1 else Inches(2.390)
    top = Y_TOP1 if tlines == 1 else Inches(2.860)
    if sub:
        sw_ = sub_w or BAND
        txt(s, M, sub_y, sw_, Inches(0.56), sub, size=16, color=sf.body, label="subhead", max_lines=2)
        if n_lines(sub, 16, Emu(sw_).inches) > 1:
            top = Y_TOP2 if tlines == 1 else Inches(3.280)
    if source:
        txt(s, M, Y_SOURCE, BAND, Inches(0.26), source, size=9, color=sf.muted, label="source", widow=False)
    txt(s, M, Y_FOOT, Inches(8.2), Inches(0.22), "CROSSWALK / BEHAVIORAL INTELLIGENCE ENGINE", size=8,
        color=sf.foot, spc=200, widow=False)
    page_number(s, sf.foot)
    return sf, top


def caps_label(s, sf, x, y, w, text, *, on_card=False):
    txt(s, x, y, w, Inches(0.24), text.upper(), size=10.5, bold=True,
        color=sf.card_label if on_card else sf.muted, spc=100, label="caps", max_lines=1, widow=False)


def bar_row(s, sf, x, y, label, frac, value, *, accent=False, second=False, label_w=1.62, track_w=2.307,
            value_w=0.8, right_label=None, right_w=0.9, label_size=10.5, bold_label=None):
    """Index track: label, pill track and pill bar, value to the right, optional second value."""
    fill = sf.second if second else (sf.accent if accent else (OLIVE if sf.kind != "dark" else SIGNAL))
    if sf.kind == "signal" and not second:
        fill = OLIVE if accent else GRAPHITE
    vcol = fill if (accent or second) else sf.ink
    if sf.kind == "signal" and not (accent or second):
        vcol = GRAPHITE
    txt(s, x, y - Inches(0.05), Inches(label_w), Inches(0.27), label, size=label_size,
        bold=(accent or second) if bold_label is None else bold_label, color=sf.ink,
        anchor=MSO_ANCHOR.MIDDLE, label="bar label", max_lines=1, widow=False)
    bx = x + Inches(label_w) + Inches(0.14)
    rrect(s, bx, y, Inches(track_w), Inches(0.17), sf.track, radius=0.085)
    bw = max(Inches(track_w) * min(max(frac, 0.0), 1.0), Inches(0.17))
    rrect(s, bx, y, int(bw), Inches(0.17), fill, radius=0.085)
    vx = bx + Inches(track_w) + Inches(0.12)
    txt(s, vx, y - Inches(0.05), Inches(value_w), Inches(0.27), value, size=11, bold=True, color=vcol,
        anchor=MSO_ANCHOR.MIDDLE, widow=False)
    if right_label is not None:
        txt(s, vx + Inches(value_w) + Inches(0.04), y - Inches(0.05), Inches(right_w), Inches(0.27),
            right_label, size=10, color=sf.body, anchor=MSO_ANCHOR.MIDDLE, widow=False, max_lines=1,
            label="bar right")


def stat_tile(s, sf, x, y, w, h, fig, lab, *, accent=False, caps=None, fig_size=30, lab_size=11.5):
    rrect(s, x, y, w, h, sf.card)
    pad = Inches(0.24)
    fig_c = sf.accent if accent else (sf.ink if sf.kind != "dark" else WHITE)
    if sf.kind == "dark":
        fig_c = SIGNAL if accent else WHITE
    yy = y + Inches(0.20)
    if caps:
        caps_label(s, sf, x + pad, yy, w - 2 * pad, caps, on_card=True)
        yy += Inches(0.36)
    fh = Inches(fig_size * 1.28 / 72)
    txt(s, x + pad, yy, w - 2 * pad, fh, fig, size=fig_size, bold=True, color=fig_c, label="tile figure",
        max_lines=1, widow=False)
    lab_h = h - (yy - y) - fh - Inches(0.22)
    txt(s, x + pad, yy + fh + Inches(0.06), w - 2 * pad, lab_h, lab, size=lab_size,
        color=sf.card_body, label="tile label")


def numbered_card(s, sf, x, y, w, h, num, head, body, *, body_size=11.5):
    rrect(s, x, y, w, h, sf.card)
    num_c = SIGNAL if sf.kind == "dark" else OLIVE
    txt(s, x + Inches(0.26), y + Inches(0.22), Inches(0.75), Inches(0.44), num, size=30, light=True,
        color=num_c, widow=False)
    txt(s, x + Inches(1.05), y + Inches(0.27), w - Inches(1.32), Inches(0.36), head, size=14, bold=True,
        color=sf.ink if sf.kind != "dark" else WHITE, label=f"card head {num}", max_lines=1, widow=False)
    txt(s, x + Inches(1.05), y + Inches(0.66), w - Inches(1.32), h - Inches(0.82), body, size=body_size,
        color=sf.card_body, label=f"card body {num}")


# ---- images ----------------------------------------------------------------------
SOURCES = {
    # Jennifer Beals stills supplied by Jenna 2026-10-19 (the same files the NCIS: New York deck used).
    "portrait": ("https://public-assets-pressexpress.s3.amazonaws.com/assets/releases/docimages/"
                 "f350b779/NCISNYCASTINGANNOUNCEMENT2026v2_html_fd9c289c30fb0941.jpg"),
    "blazer": "https://fotografias-2.larazon.es/assets/2026/07/jennifer-beals.jpg",
    "office": "https://cdn.seat42f.com/wp-content/uploads/2026/09/17211550/3246223_NCNY_S1_TV_Insider_Gallery_0360b.webp",
}
CACHE_DIRS = [Path.home() / "Desktop/finished_codes/reports/_assets/jennifer_beals_deck",
              REPO.parent / "reports/_assets/jennifer_beals_deck", IMG]
CACHED_NAMES = {"portrait": "beals_portrait_source.jpg", "blazer": "beals_blazer_source.jpg",
                "office": "beals_office_source.jpg"}


def _source(name: str) -> Path | None:
    for d in CACHE_DIRS:
        p = d / CACHED_NAMES[name]
        if p.exists():
            return p
    IMG.mkdir(parents=True, exist_ok=True)
    raw = IMG / CACHED_NAMES[name]
    try:
        req = urllib.request.Request(SOURCES[name], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        Image.open(io.BytesIO(data)).convert("RGB").save(raw, "JPEG", quality=95)
        return raw
    except Exception as e:  # offline: the page ships without the photo
        WARNINGS.append(f"could not fetch {name}: {e}")
        return None


def _key_background(im: Image.Image) -> Image.Image:
    """Swap a plain studio backdrop for Graphite (the casting-release portrait)."""
    arr = np.asarray(im.convert("RGB")).astype(int)
    mn, mx = arr.min(axis=2), arr.max(axis=2)
    cand = (mn > 140) & ((mx - mn) < 46)
    m = Image.fromarray(np.where(cand, 255, 0).astype("uint8"), mode="L").copy()
    W, H = m.size
    seeds = ([(x, 1) for x in range(1, W - 1, 40)] + [(1, y) for y in range(1, int(H * 0.8), 40)]
             + [(W - 2, y) for y in range(1, int(H * 0.8), 40)])
    for sx, sy in seeds:
        if m.getpixel((sx, sy)) == 255:
            ImageDraw.floodfill(m, (sx, sy), 128)
    keyed = (np.asarray(m) == 128).astype("uint8") * 255
    mask = Image.fromarray(keyed, mode="L").copy().filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(3))
    return Image.composite(Image.new("RGB", im.size, (0x0C, 0x16, 0x18)), im.convert("RGB"), mask)


def _blend_edge(im: Image.Image, page_rgb: tuple, frac: float = 0.5, side: str = "left") -> Image.Image:
    """Fade the photo's inner edge into the exact page color over `frac` of its width, eased (smoothstep)."""
    arr = np.asarray(im.convert("RGB")).astype(np.float32)
    W = arr.shape[1]
    span = max(2, int(W * frac))
    x = np.arange(W, dtype=np.float32)
    t = np.clip(x / span, 0, 1) if side == "left" else np.clip((W - 1 - x) / span, 0, 1)
    ease = t * t * (3 - 2 * t)                       # 0 at the page edge, 1 where the photo is clear
    alpha = ease[None, :, None]
    page = np.array(page_rgb, dtype=np.float32)[None, None, :]
    out = arr * alpha + page * (1 - alpha)
    noise = (np.random.default_rng(7).random(out.shape, dtype=np.float32) - 0.5) * 1.5
    out = np.clip(out + noise * (1 - alpha), 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGB")


def _fade_bottom(im: Image.Image, page_rgb: tuple, frac: float = 0.34) -> Image.Image:
    """Ease the bottom of the photo into the page color so a stat row can sit under the subject."""
    arr = np.asarray(im.convert("RGB")).astype(np.float32)
    H = arr.shape[0]
    span = max(2, int(H * frac))
    y = np.arange(H, dtype=np.float32)
    t = np.clip((H - 1 - y) / span, 0, 1)
    ease = (t * t * (3 - 2 * t)) ** 1.6          # mostly page color across the lower third, clear above
    alpha = ease[:, None, None]
    page = np.array(page_rgb, dtype=np.float32)[None, None, :]
    out = np.clip(arr * alpha + page * (1 - alpha), 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGB")


def prep(name: str, out: str, slot_w: float, slot_h: float, page_rgb: tuple, *, anchor=(0.5, 0.5),
         zoom: float = 1.0, desat: float = 0.9, dim: float = 0.0, key_bg=False, blend: float | None = 0.5,
         min_long_edge: int = 2100, fade_bottom: float | None = None) -> Path | None:
    """Crop to the slot, upsample a small still, tone, blend the inner edge into the page, save."""
    IMG.mkdir(parents=True, exist_ok=True)
    dst = IMG / f"{out}.jpg"
    if dst.exists():
        return dst
    src = _source(name)
    if src is None:
        return None
    im = Image.open(src).convert("RGB")
    if key_bg:
        im = _key_background(im)
    aspect = slot_w / slot_h
    W, H = im.size
    if W / H > aspect:
        ch = H / zoom
        cw = ch * aspect
    else:
        cw = W / zoom
        ch = cw / aspect
    cx, cy = anchor[0] * W, anchor[1] * H
    left = min(max(cx - cw / 2, 0), W - cw)
    top = min(max(cy - ch / 2, 0), H - ch)
    im = im.crop((int(left), int(top), int(left + cw), int(top + ch)))
    while max(im.size) < min_long_edge:
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=3))
    target_w = int(slot_w * 288)
    if im.width > target_w:
        im = im.resize((target_w, int(im.height * target_w / im.width)), Image.LANCZOS)
    im = ImageEnhance.Color(im).enhance(desat)
    if dim > 0:
        im = Image.blend(im, Image.new("RGB", im.size, page_rgb), dim)
    if blend:
        im = _blend_edge(im, page_rgb, frac=blend, side="left")
    if fade_bottom:
        im = _fade_bottom(im, page_rgb, frac=fade_bottom)
    im.save(dst, "JPEG", quality=90, optimize=True)
    return dst


def gradient_png(name: str) -> Path:
    """The pale page gradients at 3840 x 2160, dithered (tokens.json stops and angle)."""
    IMG.mkdir(parents=True, exist_ok=True)
    dst = IMG / f"gradient_{name}.png"
    if dst.exists():
        return dst
    stops = {"signal": ("#C7F23E", "#E8F3CC"), "orchid": ("#E682FF", "#F4E4F5")}[name]
    W, H = 3840, 2160
    a = math.radians(47)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    proj = xs * math.cos(a) + ys * math.sin(a)
    t = (proj - proj.min()) / (proj.max() - proj.min())
    t = t[..., None]
    c0 = np.array([int(stops[0][i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)
    c1 = np.array([int(stops[1][i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)
    img = c0 * (1 - t) + c1 * t
    rng = np.random.default_rng(11)
    img = np.clip(img + (rng.random(img.shape, dtype=np.float32) - 0.5), 0, 255)
    Image.fromarray(img.astype(np.uint8), "RGB").save(dst, "PNG", optimize=True)
    return dst


def prepare_images() -> dict:
    g = (0x0C, 0x16, 0x18)
    return {
        "cover": prep("portrait", "jb_cover_portrait", 7.5, 7.5, g, anchor=(0.5, 0.381), desat=0.94,
                      key_bg=True, blend=0.35, fade_bottom=0.55),
        "blazer_half": prep("blazer", "jb_blazer_half", 5.80, 7.5, g, anchor=(0.30, 0.50), desat=0.9,
                            dim=0.18, blend=0.5),
        "office_half": prep("office", "jb_office_half", 5.00, 7.5, g, anchor=(0.5, 0.5), desat=0.9,
                            dim=0.22, blend=0.5),
    }


# ---- pages ---------------------------------------------------------------------
def s01_cover(prs, img, A, D, bp):
    s = new_slide(prs, "dark")
    px = SW - Inches(7.5)
    picture(s, img["cover"], px, 0, Inches(7.5), SH)
    dot(s, SIGNAL)
    txt(s, Inches(1.129), Inches(0.540), Inches(8.0), Inches(0.24), "PREPARED FOR BRAND PARTNERS  ·  OCTOBER 2026",
        size=10.5, color=BODY_DK, spc=260, anchor=MSO_ANCHOR.MIDDLE, widow=False)
    logo(s, True)
    txt(s, M, Inches(1.90), Inches(6.4), Inches(0.22), "AUDIENCE AND BRAND VALUE", size=11, color=SIGNAL,
        spc=200, widow=False)
    txt(s, M, Inches(2.34), Inches(6.4), Inches(0.80), "Jennifer Beals", size=48, bold=True, color=WHITE,
        max_lines=1, widow=False)
    txt(s, M, Inches(3.08), Inches(6.4), Inches(0.80), "x your brand", size=48, light=True, color=WHITE,
        max_lines=1, widow=False)
    txt(s, M, Inches(4.06), Inches(6.9), Inches(0.40), "What she is worth to a brand, and where she should play.",
        size=16, color=BODY_DK, label="cover claim", max_lines=1)
    txt(s, M, Inches(4.62), Inches(5.5), Inches(1.0),
        "Who her audience is, how loyal they are, what she moved for lululemon and for NCIS: New York, "
        "and the categories and brands where she fits. US. Audience read for calendar 2025; campaign "
        "and premiere reads from October 2026.",
        size=11.5, color=BODY_DK, label="cover descriptor")
    hairline(s, M, Inches(5.75), BAND, RULE_DK)
    stats = [
        (n(A.people), f"US people in her audience; {pct(D['female'])} are women", True),
        (usd(bp["valuation"]["total_brand_value"]), "total brand value from the two-week campaign she fronted", False),
        (n(PREM["newcomers"][0][1]), "new viewers she brought a 23-year-old franchise at its premiere", False),
    ]
    xs = (M, Inches(4.84), Inches(8.77))
    for x, (fig, lab, hot) in zip(xs, stats):
        txt(s, x, Inches(5.92), Inches(3.4), Inches(0.42), fig, size=24, bold=True,
            color=SIGNAL if hot else WHITE, widow=False)
        txt(s, x, Inches(6.40), Inches(3.1), Inches(0.50), lab, size=10, color=CARD_BODY_DK, label="cover stat caption")
    txt(s, M, Y_FOOT, Inches(8.2), Inches(0.22), "CONFIDENTIAL", size=8, color=FOOTER_DK, spc=160, widow=False)
    page_number(s, FOOTER_DK)


def s02_tldr(prs, A, D, bp):
    s = new_slide(prs, "dark")
    val, camp = bp["valuation"], bp["campaign"]
    sf, top = chrome(s, "dark", "02 — The short version",
                     ("What a brand gets with her, ", "in five lines."),
                     sub="If you read one page, read this one. The rest of the deck is the evidence behind each line.",
                     source="Source: Crosswalk  ·  Jennifer Beals audience 2025; GANNI x lululemon campaign and NCIS: New York premiere, October 2026")
    r = D
    cards = [
        ("01", "A loyal, affluent audience of 9.3M people",
         f"Her audience is {n(A.people)} US people: {pct(r['female'])} women, {pct(r['lgbtq'])} LGBTQ+, half aged 45 to 64, "
         f"{pct(r['degree'])} with a degree. They shop Sephora, Nordstrom, REI and Crate & Barrel at "
         f"{xus(r['sephora_idx'])} to {xus(r['rei_idx'])} the US rate, full price."),
        ("02", "Loyalty you can count",
         f"{pct(A.avid_share)} are avid: {n(A.avid_people)} people who come back to her through the year. "
         f"At the NCIS: New York premiere her viewers finished the hour (83.3%) and returned the next week (71.6%), "
         f"the best attention in the cast."),
        ("03", "She moves people to buy",
         f"The lululemon capsule she fronted reached {n(bp['projected_audience_size'])} US people in two weeks, sold out "
         f"on the brand's site within hours and took {usd(camp['drop']['revenue_usd'])} in US orders. Her assets drew "
         f"{pct((camp['talent_share']['face_uplift'] - 1) * 100)} more response than the brand's own."),
        ("04", "She builds the brand, not just the drop",
         f"In 14 days the brand gained {usd(val['earned_media_value'])} in earned media and {n(val['incremental_users'])} "
         f"newly engaged people, older, more female and higher income than its own base. "
         f"{pct(camp['talent_share']['holdout_share'] * 100)} of the lift held against a matched group."),
    ]
    cw, ch = COL2, Inches(1.66)
    for i, (num, head, body) in enumerate(cards):
        x = M + (i % 2) * (cw + GUT)
        y = top + (i // 2) * (ch + Inches(0.16))
        numbered_card(s, sf, x, y, cw, ch, num, head, body, body_size=11)
    yb = top + 2 * ch + Inches(0.16) + Inches(0.12)
    txt(s, M, yb, BAND, Inches(0.42),
        "Where to play: premium apparel and athleisure, home, prestige beauty, LGBTQ+-forward brands, travel and "
        "wellness. What to do: put her face on the asset, run a two-week window stocked for the first 48 hours, "
        "and measure the lift against a matched group.",
        size=11, color=BODY_DK, label="s02 bottom line", max_lines=2)


def s03_who(prs, A, D):
    s = new_slide(prs, "light")
    sf, top = chrome(s, "light", "03 — Who they are",
                     ("9.3M people, ", "mostly women over 45 with degrees."),
                     sub=f"Jennifer Beals audience, US, calendar 2025. The avid core is {pct(A.avid_share)} of the total.",
                     source=SRC_AUD)
    tiles = [
        (n(A.people), "US people in her audience", False),
        (pct(D["female"]), f"women. The US is {pct(D['female_us'])}", False),
        (pct(D["lgbtq"]), f"LGBTQ+; the US is {pct(D['lgbtq_us'])}", True),
        (pct(D["degree"]), f"hold a college degree or higher. The US is {pct(D['degree_us'])}", False),
    ]
    x = M
    for fig, lab, acc in tiles:
        stat_tile(s, sf, x, top, COL4, Inches(1.30), fig, lab, accent=acc, fig_size=28)
        x += COL4 + GUT
    rows = [
        ("Ages 45 to 64", pct(D["age4564"]),
         f"Half the audience; the US is {pct(D['age4564_us'])}. The largest group is 55 to 64 at {pct(D['age5564'])}."),
        ("Management and professional jobs", pct(D["mgmt"]),
         f"Above the US rate by {xus(D['mgmt_idx'])}; education and legal jobs run high too."),
        ("Income of $100,000 or more", pct(D["inc100"]),
         f"A touch above the US at {pct(D['inc100_us'])}. The money sits in the degree and the job."),
        ("No children at home", pct(D["nokids"]),
         f"Discretionary spend. {pct(D['single'])} are single and {pct(D['divorced'])} divorced."),
        ("New York, Los Angeles, Chicago, the Bay Area", pct(D["top4"]),
         f"Top four markets, {D['top4_x']:.1f}x their US share. San Diego, Seattle and Portland follow."),
        ("The avid core", pct(A.avid_share),
         f"People who return through the year: {n(A.avid_people)}, {pct(D['avid_female'])} women and {pct(D['avid_lgbtq'])} LGBTQ+."),
    ]
    y = top + Inches(1.30) + Inches(0.26)
    for lab, fig, note in rows:
        txt(s, M, y, Inches(3.55), Inches(0.30), lab, size=11.5, color=GRAPHITE, max_lines=1, widow=False)
        txt(s, M + Inches(3.65), y, Inches(1.10), Inches(0.30), fig, size=11.5, bold=True, color=OLIVE, widow=False)
        txt(s, M + Inches(4.85), y, Inches(6.64), Inches(0.30), note, size=11.5, color=BODY_LT, max_lines=1,
            label="who row", widow=False)
        y += Inches(0.33)


def xus_short(idx: float) -> str:
    x = idx / 100.0
    return f"about {x:.0f}x the US rate" if x >= 10 else f"{x:.1f}x the US rate"


def s04_interests(prs, A, D):
    s = new_slide(prs, "dark")
    sf, top = chrome(s, "dark", "04 — What they care about",
                     ("They care about culture, wellness ", "and causes."),
                     sub="Share of her audience that follows each interest. Index 100 is the US average.",
                     source=SRC_AUD)
    rows = D["interests"]
    panel_w = Inches(6.90)
    rrect(s, M, top, panel_w, Inches(3.70), SLATE)
    caps_label(s, sf, M + Inches(0.30), top + Inches(0.22), Inches(6.2), "Interest  ·  share of her audience  ·  vs the US", on_card=True)
    y = top + Inches(0.66)
    topv = rows[0][1]
    for lab, val, idx in rows:
        bar_row(s, sf, M + Inches(0.30), y, lab, val / topv, pct(val), second=(idx < 100),
                label_w=1.60, track_w=1.95, value_w=0.62, right_label=xus_short(idx), right_w=1.60)
        y += Inches(0.355)
    rx = M + panel_w + GUT
    rw = Inches(12.413) - rx
    txt(s, rx, top, rw, Inches(0.95),
        f"A values-led, culture-literate audience. {pct(D['lgbtq_life'])} follow LGBTQ+ life, about "
        f"{D['lgbtq_life_idx'] / 100:.0f} times the US rate; {pct(D['feminism'])} follow feminism and "
        f"{pct(D['social_justice'])} social justice.",
        size=11.5, color=BODY_DK, label="s04 read a")
    txt(s, rx, top + Inches(1.07), rw, Inches(0.95),
        f"They read: {pct(D['nyt'])} open The New York Times and {pct(D['npr'])} listen to NPR, "
        f"{xus(D['npr_idx'])} the US rate. They give: {pct(D['trevor'])} follow The Trevor Project and "
        f"{pct(D['pp'])} Planned Parenthood.",
        size=11.5, color=BODY_DK, label="s04 read b")
    rrect(s, rx, top + Inches(2.22), rw, Inches(1.48), SLATE)
    caps_label(s, sf, rx + Inches(0.26), top + Inches(2.42), rw - Inches(0.5), "What this means", on_card=True)
    txt(s, rx + Inches(0.26), top + Inches(2.74), rw - Inches(0.52), Inches(0.90),
        "Brands with a point of view win here. Sports, gaming and youth social sit under the US average, so a "
        "stadium or a console tie-in buys little.",
        size=11, color=CARD_BODY_DK, label="s04 means")


def s05_shop(prs, A, D):
    s = new_slide(prs, "signal")
    sf, top = chrome(s, "signal", "05 — Where they shop",
                     ("They shop specialty retail ", "at 3x to 9x the US rate."),
                     sub="Share of her audience that shopped each retailer online in 2025; lululemon is the comparison row.",
                     source=SRC_AUD)
    rows = D["retail"]
    pw = Inches(7.05)
    rrect(s, M, top, pw, Inches(3.50), OFFWHITE)
    lt = Surface("light")
    caps_label(s, lt, M + Inches(0.30), top + Inches(0.22), Inches(6.4), "Retailer  ·  index  ·  share of her audience")
    y = top + Inches(0.64)
    topi = max(r[2] for r in rows)
    for lab, pen, idx, cmp_ in rows:
        bar_row(s, lt, M + Inches(0.30), y, lab, idx / topi, f"{idx:,.0f}", second=cmp_,
                label_w=1.75, track_w=2.20, value_w=0.60, right_label=f"{pct(pen)} of her audience", right_w=1.75)
        y += Inches(0.345)
    rx = M + pw + GUT
    rw = Inches(12.413) - rx
    lead = (f"REI runs about {D['rei_idx'] / 100:.0f} times the US rate and Crate & Barrel about {D['cb_idx'] / 100:.0f} times. "
            f"Sephora reaches {pct(D['sephora'])} of her audience against {pct(D['sephora_us'])} of the US. ")
    read = widow_safe([lead + "Full-price specialty shoppers rather than bargain hunters: Shein sits at half the US rate.",
                       lead + "These are full-price specialty shoppers rather than bargain hunters; Shein sits at half the US rate.",
                       lead + "Full-price specialty shoppers rather than bargain hunters: Shein sits at half the US rate here.",
                       lead + "They pay full price at specialty retail rather than bargain hunt: Shein sits at half the US rate."],
                      11.5, Emu(rw).inches, "regular")
    txt(s, rx, top, rw, Inches(1.70), read, size=11.5, color=sf.body, label="s05 read")
    txt(s, rx, top + Inches(1.86), rw, Inches(1.64),
        f"lululemon reached {pct(D['lulu'])} of them before the campaign, {xus(D['lulu_idx'])} the US rate. Every "
        f"retailer above it on this page is a bigger over-index than the brand she just sold out.",
        size=11.5, color=sf.body, label="s05 read b")


def s06_loyal(prs, img, A, D):
    s = new_slide(prs, "dark")
    px = SW - Inches(5.80)
    picture(s, img["blazer_half"], px, 0, Inches(5.80), SH)
    sf, top = chrome(s, "dark", "06 — How loyal they are",
                     ("Her fans have stayed with her ", "for four decades."),
                     title_w=Inches(7.0),
                     sub="Avid fans return to her through the year. Calendar 2025.",
                     sub_w=Inches(6.9), source=SRC_AUD, logo_white=True)
    cw = Inches(3.20)
    stat_tile(s, sf, M, top, cw, Inches(1.34), pct(A.avid_share),
              f"of her audience is avid, {n(A.avid_people)} people who return through the year", accent=True, fig_size=30)
    stat_tile(s, sf, M + cw + GUT, top, cw, Inches(1.34), pct(D["lword_pod"]),
              f"follow The L Word companion podcast; {pct(D['lword_pod_avid'])} of the avid core do", fig_size=30)
    y = top + Inches(1.34) + Inches(0.14)
    caps_label(s, sf, M, y, Inches(6.5), "Four decades, one audience")
    eras = [
        ("1983", "Flashdance", "The role that made her. The press still leads with it."),
        ("2004", "The L Word", "Bette Porter on Showtime, then Generation Q from 2019."),
        ("2021", "The Book of Boba Fett", "A Disney+ role that added a Star Wars audience."),
        ("2026", "NCIS: New York", "A CBS lead at 62, next day on Paramount+."),
    ]
    y += Inches(0.30)
    for yr, title, note in eras:
        txt(s, M, y, Inches(0.72), Inches(0.30), yr, size=14, bold=True, color=SIGNAL, widow=False)
        txt(s, M + Inches(0.80), y, Inches(2.10), Inches(0.30), title, size=12, bold=True, color=WHITE, max_lines=1, widow=False)
        txt(s, M + Inches(2.95), y, Inches(4.15), Inches(0.30), note, size=10.5, color=BODY_DK, label="era note", max_lines=1, widow=False)
        y += Inches(0.31)
    y += Inches(0.04)
    txt(s, M, y, Inches(6.7), Inches(0.40),
        "They read as loyal to the person more than to any one title. They follow her directly and carry her "
        "from a Showtime drama to a CBS procedural to a fashion film.",
        size=11, color=BODY_DK, label="s06 read", max_lines=2)


def s07_attention(prs):
    s = new_slide(prs, "light")
    P = PREM
    sf, top = chrome(s, "light", "07 — The premiere, attention",
                     ("Her viewers finish the hour ", "and come back."),
                     sub="NCIS: New York premiere, October 6, 2026. Share of each name's viewers who did the thing.",
                     source=SRC_PREM)
    th = Inches(1.52)
    stat_tile(s, sf, M, top, COL3, th, f"{P['one_sitting']['Jennifer Beals']}%",
              f"of her viewers finished the pilot in one sitting, against {P['one_sitting']['LL Cool J']}% for "
              f"LL Cool J and {P['one_sitting']['All viewers']}% for all viewers", accent=True, fig_size=28)
    stat_tile(s, sf, M, top + th + Inches(0.18), COL3, th, f"{P['came_back']['Jennifer Beals']}%",
              f"came back for episode 2 within the week, against {P['came_back']['All viewers']}% for all viewers "
              f"and {P['came_back']['The NCIS name and the Tuesday block']}% for the NCIS-name audience", fig_size=28)
    txt(s, M, top + 2 * th + Inches(0.33), COL3, Inches(0.60),
        "LL Cool J leads return on a 14-season base. She is four points behind, from outside the franchise, "
        "with viewers 61.2% new to NCIS.",
        size=11, color=BODY_LT, label="s07 read", max_lines=3)
    rx = M + COL3 + GUT
    rw = BAND - COL3 - GUT
    caps_label(s, sf, rx, top, rw, "Finished in one sitting")
    y = top + Inches(0.36)
    one = [("Jennifer Beals", 83.3), ("LL Cool J", 77.3), ("All viewers", 77.2), ("Scott Caan", 75.0),
           ("NCIS name and Tuesday block", 71.5)]
    for lab, v in one:
        bar_row(s, sf, rx, y, lab, v / 83.3, f"{v}%", accent=lab.startswith("Jennifer"), label_w=2.55, track_w=3.70, value_w=0.8)
        y += Inches(0.30)
    y += Inches(0.14)
    caps_label(s, sf, rx, y, rw, "Came back for episode 2")
    y += Inches(0.36)
    back = [("LL Cool J", 75.7), ("Jennifer Beals", 71.6), ("All viewers", 68.2),
            ("NCIS name and Tuesday block", 62.4), ("Scott Caan", 59.3)]
    for lab, v in back:
        bar_row(s, sf, rx, y, lab, v / 75.7, f"{v}%", accent=lab.startswith("Jennifer"), label_w=2.55, track_w=3.70, value_w=0.8)
        y += Inches(0.30)


def s08_newcomers(prs):
    s = new_slide(prs, "orchid")
    P = PREM
    sf, top = chrome(s, "signal", "08 — The premiere, new viewers",
                     ("More than half the show's new viewers ", "came because of her."),
                     sub="Viewers who had not watched an earlier NCIS series in the year before, by who brought them in.",
                     source=f"Source: Crosswalk  ·  NCIS: New York premiere  ·  {n(P['newcomers_total'])} viewers new to the franchise")
    txt(s, M, top, Inches(5.6), Inches(1.20), f"{P['newcomers'][0][2]}%", size=82, bold=True, color=GRAPHITE,
        max_lines=1, widow=False)
    txt(s, M, top + Inches(1.26), Inches(5.6), Inches(0.86),
        f"of the {n(P['newcomers_total'])} viewers who were new to the franchise came in through Jennifer Beals: "
        f"{n(P['newcomers'][0][1])} people, {P['newcomers'][0][1] / P['newcomers'][1][1]:.1f} times LL Cool J's count, "
        f"from an audience 6% smaller than his.",
        size=13, color=ORCHID_BODY, label="s08 hero caption")
    y = top + Inches(2.24)
    xcol = (M, Inches(3.95))
    for i, (name, cnt, share) in enumerate(P["newcomers"]):
        x = xcol[i % 2]
        yy = y + (i // 2) * Inches(0.62)
        txt(s, x, yy, Inches(2.9), Inches(0.40), n(cnt), size=22, bold=True, color=GRAPHITE, widow=False)
        txt(s, x, yy + Inches(0.34), Inches(2.9), Inches(0.24), f"{name}, {share}%", size=10.5, color=ORCHID_BODY, widow=False)
    rx = Inches(7.35)
    rw = Inches(12.413) - rx
    ch = Inches(3.02)
    rrect(s, rx, top, rw, ch, GRAPHITE)
    dk = Surface("dark")
    caps_label(s, dk, rx + Inches(0.30), top + Inches(0.22), rw - Inches(0.6), "The buy during the hour", on_card=True)
    txt(s, rx + Inches(0.30), top + Inches(0.58), Inches(1.8), Inches(0.64), f"{P['ad_buy_pct']['Jennifer Beals']}%", size=34,
        bold=True, color=SIGNAL, widow=False)
    txt(s, rx + Inches(2.20), top + Inches(0.58), rw - Inches(2.50), Inches(0.74),
        f"of her viewers clicked an ad and bought during the episode. LL Cool J {P['ad_buy_pct']['LL Cool J']}%, "
        f"all viewers {P['ad_buy_pct']['All viewers']}%.", size=10.5, color=CARD_BODY_DK, label="s08 buy a")
    txt(s, rx + Inches(0.30), top + Inches(1.46), Inches(1.8), Inches(0.64), f"{P['buyers_jb_share']}%", size=34, bold=True,
        color=WHITE, widow=False)
    txt(s, rx + Inches(2.20), top + Inches(1.46), rw - Inches(2.50), Inches(0.74),
        f"of everyone who bought from an ad during the episode came through her: {n(P['buyers_jb'])} of "
        f"{n(P['buyers_total'])} buyers, from 30.2% of the audience.", size=10.5, color=CARD_BODY_DK, label="s08 buy b")
    txt(s, rx + Inches(0.30), top + Inches(2.34), rw - Inches(0.60), Inches(0.56),
        "Second-screen buying is the behavior a brand pays for. Her audience leads the cast on it.",
        size=10.5, color=WHITE, label="s08 buy c", max_lines=2)


def s09_campaign(prs, bp):
    s = new_slide(prs, "dark")
    camp, conv = bp["campaign"], bp["conversions"]
    ts = camp["talent_share"]
    sf, top = chrome(s, "dark", "09 — The campaign, reach",
                     ("One two-week campaign with her reached 3.6M people ", "and sold out."),
                     sub="GANNI x lululemon, Joy Is a Power Move. She fronted the film and the stills. On sale October 15, 2026.",
                     source=SRC_CAMP)
    tiles = [
        (n(bp["projected_audience_size"]), "US people the campaign reached, counted once each", True),
        (n(camp["reach"]["views_total"]), "views of the campaign film and the launch posts", False),
        (str(camp["reach"]["press_pieces"]), "press pieces across national, fashion and trade titles", False),
        (n(camp["drop"]["orders"]), "orders in two weeks; most of the run sold out within hours", False),
    ]
    x = M
    for fig, lab, acc in tiles:
        stat_tile(s, sf, x, top, COL4, Inches(1.26), fig, lab, accent=acc, fig_size=24, lab_size=10.5)
        x += COL4 + GUT
    y = top + Inches(1.26) + Inches(0.18)
    caps_label(s, sf, M, y, BAND, "How much of it was her")
    y += Inches(0.34)
    rows = [
        (pct(ts["asset_share"] * 100), "of the campaign's exposure ran through assets that carried her: the film, the stills, her own posts and the launch night."),
        (pct((ts["face_uplift"] - 1) * 100), "more response to assets that carried her than to the brand's own, from 629,000 followers against the brand's 5.5M."),
        (pct(ts["holdout_share"] * 100), "of the lift held in a matched comparison: a group with the same brand engagement before the drop. The season did not do this."),
        (pct(conv["first_48h_share"] * 100), "of the purchases landed inside the first 48 hours. The run was small, so the sell-out reads as limited inventory as much as demand."),
    ]
    for fig, note in rows:
        txt(s, M, y, Inches(1.35), Inches(0.34), fig, size=20, bold=True, color=WHITE, widow=False)
        txt(s, M + Inches(1.45), y + Inches(0.03), Inches(10.04), Inches(0.32), note, size=11, color=BODY_DK,
            label="s09 row", max_lines=1, widow=False)
        y += Inches(0.42)


def s10_equity(prs, bp):
    s = new_slide(prs, "light")
    val, sent, demo = bp["valuation"], bp["sentiment"], bp["demographics"]
    sf, top = chrome(s, "light", "10 — Brand equity value",
                     ("She drew $22.3M in earned media ", "in two weeks."),
                     sub="What the brand gained beyond the orders, and who she brought against the brand's own base.",
                     source=SRC_CAMP)
    tiles = [
        (usd(val["earned_media_value"]), "earned media from press, the launch night and nine platforms", True),
        (usd(val["brand_lift_value"]), f"brand lift: {n(val['incremental_users'])} people newly engaged with the brand, against a matched group", False),
        (usd(val["brand_engagement_value"]), f"brand engagement: {n(bp['totals']['post_users_projected'])} people engaged with the brand in the window", False),
        (f"+{sent['net_shift']:.1f}", f"net sentiment, from {sent['pre_net_score']:.1f} to {sent['post_net_score']:.1f}. The casting read as a pairing that fits the product", False),
    ]
    x = M
    for fig, lab, acc in tiles:
        stat_tile(s, sf, x, top, COL4, Inches(1.62), fig, lab, accent=acc, fig_size=20, lab_size=10.5)
        x += COL4 + GUT
    y = top + Inches(1.62) + Inches(0.26)
    caps_label(s, sf, M, y, Inches(7.4), "Who she brought, against the brand's own base")

    def share(block, key, values):
        return sum(d["percentage"] for d in block[key] if d["value"] in values)

    pre, post = demo["pre"], demo["post"]
    hi = {"$100,000 - $149,999", "$150,000 - $249,999", "$250,000 or More"}
    comp = [
        ("Women", share(pre, "gender", {"Female"}), share(post, "gender", {"Female"})),
        ("Ages 45 to 64", share(pre, "age", {"45-54", "55-64"}), share(post, "age", {"45-54", "55-64"})),
        ("Income of $100,000 or more", share(pre, "income", hi), share(post, "income", hi)),
        ("Ages 25 to 34, the brand's core", share(pre, "age", {"25-34"}), share(post, "age", {"25-34"})),
    ]
    hy = y + Inches(0.32)
    cols = [(None, M, Inches(3.0)), ("BEFORE THE DROP", M + Inches(3.1), Inches(1.75)), ("WITH HER", M + Inches(4.95), Inches(1.25)),
            ("CHANGE", M + Inches(6.3), Inches(1.1))]
    for lab, cx, cwid in cols:
        if lab:
            txt(s, cx, hy, cwid, Inches(0.24), lab, size=10.5, bold=True, color=MUTED_LT, spc=100, align=PP_ALIGN.RIGHT,
                widow=False, max_lines=1)
    hairline(s, M, hy + Inches(0.30), Inches(6.48), RULE_LT)
    yy = hy + Inches(0.40)
    for lab, a, b in comp:
        txt(s, M, yy, Inches(3.0), Inches(0.28), lab, size=11.5, color=GRAPHITE, max_lines=1, widow=False)
        txt(s, cols[1][1], yy, cols[1][2], Inches(0.28), pct(a), size=12, color=BODY_LT, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[2][1], yy, cols[2][2], Inches(0.28), pct(b), size=12, bold=True, color=GRAPHITE, align=PP_ALIGN.RIGHT, widow=False)
        d = b - a
        txt(s, cols[3][1], yy, cols[3][2], Inches(0.28), f"{d:+.1f} pts", size=12, bold=True,
            color=OLIVE if d >= 0 else AMETHYST, align=PP_ALIGN.RIGHT, widow=False)
        yy += Inches(0.30)
    rx = Inches(8.77)
    rw = Inches(12.413) - rx
    rrect(s, rx, y, rw, Inches(1.90), GRAPHITE)
    dk = Surface("dark")
    caps_label(s, dk, rx + Inches(0.26), y + Inches(0.20), rw - Inches(0.5), "The read", on_card=True)
    read = widow_safe(["She pulled the brand's engaged audience toward women 45 to 64 with higher incomes, and the 25 to 34 "
                       "core barely moved. That is new money on top of the base rather than a trade.",
                       "She pulled the brand's engaged audience toward women 45 to 64 with higher incomes, and the 25 to 34 "
                       "core barely moved. New money on top of the base rather than a trade for it.",
                       "She pulled the brand's engaged audience toward women 45 to 64 with higher incomes while the 25 to 34 "
                       "core barely moved. That is new money on top of the base, with nothing traded away."],
                      10.5, Emu(rw - Inches(0.52)).inches, "regular")
    txt(s, rx + Inches(0.26), y + Inches(0.50), rw - Inches(0.52), Inches(1.30), read,
        size=10.5, color=WHITE, label="s10 read")


def s11_platforms(prs, bp):
    s = new_slide(prs, "dark")
    tot = bp["totals"]
    ctl = bp["control_group"]
    sf, top = chrome(s, "dark", "11 — The campaign, where it moved",
                     ("The brand grew on every platform ", "that carried her."),
                     sub="People engaging with the brand, the two weeks of the drop against the two weeks before, by platform.",
                     source=SRC_CAMP)
    plat = []
    for p in bp["per_platform"]:
        name = {"X (Twitter)": "X", "Direct (Brand Site)": "The brand's own site"}.get(p["platform"], p["platform"])
        plat.append((name, p["lift_pct_users"], p["post_users_projected"] - p["pre_users_projected"]))
    plat = [r for r in plat if r[0] not in ("Threads", "Reddit")]
    plat.sort(key=lambda r: -r[1])
    quiet = {"Snapchat", "Twitch"}
    pw = Inches(7.40)
    rrect(s, M, top, pw, Inches(3.68), SLATE)
    caps_label(s, sf, M + Inches(0.30), top + Inches(0.22), Inches(6.8), "Platform  ·  lift in people engaging  ·  people added", on_card=True)
    y = top + Inches(0.64)
    topv = plat[0][1]
    for name, lift, added in plat[:10]:
        bar_row(s, sf, M + Inches(0.30), y, name, lift / topv, f"+{lift:.1f}%", second=(name in quiet),
                label_w=2.05, track_w=2.60, value_w=0.85, right_label=f"+{n(added)} people", right_w=1.45)
        y += Inches(0.29)
    rx = M + pw + GUT
    rw = Inches(12.413) - rx
    stat_tile(s, sf, rx, top, rw, Inches(1.60), f"{tot['audience_pen_post_pct']:.1f}%",
              f"of the people the campaign reached engaged with the brand, against {tot['audience_pen_pre_pct']:.1f}% in the two weeks before the drop",
              accent=True, fig_size=30, lab_size=10.5)
    stat_tile(s, sf, rx, top + Inches(1.60) + Inches(0.20), rw, Inches(1.88), f"+{ctl['control_delta_pp']:.1f} pts",
              f"is how far a matched group moved in the same window, against {ctl['treat_delta_pp']:.1f} points for the "
              f"people she reached. Snapchat and Twitch, where no creative ran, barely moved.",
              fig_size=30, lab_size=10.5)


def s12_sales(prs, bp, D):
    s = new_slide(prs, "light")
    camp, val, conv = bp["campaign"], bp["valuation"], bp["conversions"]
    sf, top = chrome(s, "light", "12 — Sales value",
                     ("The drop took $3.1M in US orders ", "in two weeks."),
                     sub="US orders of the GANNI x lululemon capsule, October 15 to 28, 2026, and what each order was worth.",
                     source=SRC_CAMP)
    txt(s, M, top, Inches(6.4), Inches(1.00), usd(camp["drop"]["revenue_usd"]), size=54, bold=True,
        color=OLIVE, max_lines=1, widow=False)
    txt(s, M, top + Inches(1.10), Inches(5.6), Inches(0.90),
        f"in US orders from {n(camp['drop']['orders'])} orders and {n(camp['drop']['units_us'])} pieces, at "
        f"{usd(camp['aov_usd'])} an order. Most of the run sold out inside the window.",
        size=13, color=GRAPHITE, label="s12 hero caption")
    txt(s, M, top + Inches(2.12), Inches(5.6), Inches(1.20),
        f"Her share of the campaign's {usd(val['total_brand_value'])} total brand value is {pct(val['talent_share'] * 100)}, "
        f"or {usd(val['attributable_to_talent'])}. That share rests on three facts. {pct(camp['talent_share']['asset_share'] * 100)} "
        f"of the exposure carried her. Her assets drew {pct((camp['talent_share']['face_uplift'] - 1) * 100)} more response. And "
        f"{pct(camp['talent_share']['holdout_share'] * 100)} of the lift stayed when we compared against a matched group.",
        size=11.5, color=BODY_LT, label="s12 read")
    cards = [
        (f"{D['orders_per_1000']:.1f}", "orders for every 1,000 people reached. A rate a brand can plan inventory against."),
        (pct(D["first_action_rate"]), f"of the people reached took a first action with the brand: {n(D['first_actions'])} people who had not engaged with it the two weeks before."),
        (pct(conv["first_48h_share"] * 100), "of the orders landed inside 48 hours of the drop. Plan for a fast audience."),
    ]
    cx, cw = Inches(7.05), Inches(5.363)
    y = top
    for fig, lab in cards:
        rrect(s, cx, y, cw, Inches(1.06), CARD)
        txt(s, cx + Inches(0.26), y + Inches(0.20), Inches(1.45), Inches(0.66), fig, size=28, bold=True, color=GRAPHITE, widow=False)
        txt(s, cx + Inches(1.80), y + Inches(0.17), cw - Inches(2.06), Inches(0.78), lab, size=10.5, color=BODY_LT, label="s12 card")
        y += Inches(1.06) + Inches(0.12)


def s13_category_sales(prs, D):
    s = new_slide(prs, "light")
    sf, top = chrome(s, "light", "13 — Projected sales by category",
                     ("The same campaign sells ", "in other categories too."),
                     sub="The lululemon campaign's shape and rates, scaled to her audience's affinity for each category.",
                     source="Source: Crosswalk  ·  GANNI x lululemon campaign rates applied to the Jennifer Beals audience, 2025  ·  Affinity: 100 = US average")
    rows = sorted([c for c in D["cats"] if "proj_sales" in c], key=lambda c: -c["proj_sales"])
    cols = [("CATEGORY", M, Inches(3.5), PP_ALIGN.LEFT), ("AFFINITY", M + Inches(3.6), Inches(1.2), PP_ALIGN.RIGHT),
            ("FIRST ACTIONS", M + Inches(4.9), Inches(1.55), PP_ALIGN.RIGHT), ("ORDERS", M + Inches(6.55), Inches(1.2), PP_ALIGN.RIGHT),
            ("PER ORDER", M + Inches(7.85), Inches(1.2), PP_ALIGN.RIGHT), ("PROJECTED SALES", M + Inches(9.15), Inches(2.343), PP_ALIGN.RIGHT)]
    hy = top
    for lab, x, w, al in cols:
        txt(s, x, hy, w, Inches(0.24), lab, size=10.5, bold=True, color=MUTED_LT, spc=100, align=al, widow=False)
    hairline(s, M, hy + Inches(0.30), BAND, RULE_LT)
    y = hy + Inches(0.40)
    for i, c in enumerate(rows):
        lead = (i == 0)
        txt(s, cols[0][1], y, cols[0][2], Inches(0.28), c["label"], size=11.5, color=GRAPHITE, max_lines=1, widow=False)
        txt(s, cols[1][1], y, cols[1][2], Inches(0.28), f"{c['idx']:,.0f}", size=12, color=BODY_LT, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[2][1], y, cols[2][2], Inches(0.28), n(c["proj_first_actions"]), size=12, color=GRAPHITE, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[3][1], y, cols[3][2], Inches(0.28), n(c["proj_orders"]), size=12, color=GRAPHITE, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[4][1], y, cols[4][2], Inches(0.28), usd(c["order_value"]), size=12, color=BODY_LT, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[5][1], y, cols[5][2], Inches(0.28), usd(c["proj_sales"]), size=12, bold=True,
            color=OLIVE if lead else GRAPHITE, align=PP_ALIGN.RIGHT, widow=False)
        y += Inches(0.31)
    hairline(s, M, y + Inches(0.02), BAND, RULE_LT)
    y += Inches(0.16)
    rrect(s, M, y, COL2, Inches(0.80), CARD)
    txt(s, M + Inches(0.22), y + Inches(0.12), COL2 - Inches(0.44), Inches(0.60),
        f"Travel leads on value because a booking is a big order. Apparel and home lead on orders: affinity there runs "
        f"{xus(D['cat_by_key']['apparel']['idx'])} and {xus(D['cat_by_key']['home']['idx'])} the US rate, against "
        f"{xus(D['lulu_idx'])} for the brand she just sold out.",
        size=10.5, color=BODY_LT, label="s13 callout a", max_lines=3)
    rrect(s, M + COL2 + GUT, y, COL2, Inches(0.80), CARD)
    txt(s, M + COL2 + GUT + Inches(0.22), y + Inches(0.12), COL2 - Inches(0.44), Inches(0.60),
        "Streaming is counted in accounts, worth $29.43 each over their first 90 days at list prices. Financial "
        "services and automotive are equity plays and are not sized here.",
        size=10.5, color=BODY_LT, label="s13 callout b", max_lines=3)


def s14_reach(prs, A, D):
    s = new_slide(prs, "dark")
    sf, top = chrome(s, "dark", "14 — Where to reach them",
                     ("Reach them on Instagram, Pinterest ", "and podcasts."),
                     sub="Share of her audience on each platform in 2025, and the index against the US.",
                     source=SRC_AUD)
    tiles = [
        (pct(D["instagram"]), f"Instagram, {xus(D['instagram_idx'])} the US rate. Where her own posts land", False),
        (pct(D["pinterest"]), f"Pinterest, {xus(D['pinterest_idx'])} the US rate, for home and fashion ideas", True),
        (pct(D["spotify"]), f"Spotify, {xus(D['spotify_idx'])} the US rate. {pct(D['podcasts'])} of them follow podcasts", False),
        (pct(D["paramount"]), "Paramount+, where her show streams the next day", False),
    ]
    th = Inches(1.40)
    x = M
    for fig, lab, acc in tiles:
        stat_tile(s, sf, x, top, COL4, th, fig, lab, accent=acc, fig_size=28, lab_size=10.5)
        x += COL4 + GUT
    y = top + th + Inches(0.28)
    caps_label(s, sf, M, y, Inches(7.0), "Platform  ·  index  ·  share of her audience")
    y += Inches(0.36)
    rows = D["platforms"]
    topi = max(r[2] for r in rows)
    for lab, pen, idx in rows:
        bar_row(s, sf, M, y, lab, idx / topi, f"{idx:,.0f}", second=(idx < 100), label_w=2.15, track_w=2.80, value_w=0.62,
                right_label=f"{pct(pen)} of her audience", right_w=1.9)
        y += Inches(0.30)
    rx = Inches(8.55)
    rw = Inches(12.413) - rx
    cy = top + th + Inches(0.28)
    rrect(s, rx, cy, rw, Inches(2.16), SLATE)
    caps_label(s, sf, rx + Inches(0.26), cy + Inches(0.20), rw - Inches(0.5), "What this means", on_card=True)
    txt(s, rx + Inches(0.26), cy + Inches(0.52), rw - Inches(0.52), Inches(1.50),
        f"Buy Instagram, Pinterest, podcasts and the NPR and New York Times audiences. TikTok reaches "
        f"{pct(D['tiktok'])} of them, {xus(D['tiktok_idx'])} the US rate: fine as a bonus, wrong as the lead.",
        size=10.5, color=CARD_BODY_DK, label="s14 means")


def s15_where(prs, A, D):
    s = new_slide(prs, "light")
    sf, top = chrome(s, "light", "15 — Where she should play",
                     ("Her best fits are apparel, home ", "and beauty."),
                     sub="Ranked by fit: affinity, times the share of her audience in the category, times her credibility.",
                     source=SRC_AUD + "  ·  Fit = affinity x share of her audience x credibility")
    cols = [("#", M, Inches(0.4), PP_ALIGN.LEFT), ("CATEGORY", M + Inches(0.45), Inches(3.0), PP_ALIGN.LEFT),
            ("HER AUDIENCE IN IT", M + Inches(3.5), Inches(1.95), PP_ALIGN.RIGHT), ("AFFINITY", M + Inches(5.55), Inches(0.95), PP_ALIGN.RIGHT),
            ("CREDIBILITY", M + Inches(6.6), Inches(1.15), PP_ALIGN.RIGHT), ("THE PLAY", M + Inches(7.95), Inches(3.543), PP_ALIGN.LEFT)]
    hy = top
    for lab, x, w, al in cols:
        txt(s, x, hy, w, Inches(0.24), lab, size=10.5, bold=True, color=MUTED_LT, spc=100, align=al, widow=False)
    hairline(s, M, hy + Inches(0.30), BAND, RULE_LT)
    y = hy + Inches(0.38)
    for i, c in enumerate(D["ranked"]):
        top4 = i < 4
        txt(s, cols[0][1], y, cols[0][2], Inches(0.27), f"{i + 1:02d}", size=11, light=True, color=OLIVE, widow=False)
        txt(s, cols[1][1], y, cols[1][2], Inches(0.27), c["label"], size=11, bold=top4, color=GRAPHITE, max_lines=1, widow=False)
        txt(s, cols[2][1], y, cols[2][2], Inches(0.27), f"{n(c['people'])}  ·  {pct(c['share'], 0)}", size=11, color=BODY_LT, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[3][1], y, cols[3][2], Inches(0.27), xus(c["idx"]), size=11, bold=True, color=OLIVE if top4 else GRAPHITE, align=PP_ALIGN.RIGHT, widow=False)
        txt(s, cols[4][1], y, cols[4][2], Inches(0.27), CRED_LABEL[c["cred"]], size=11, color=BODY_LT, align=PP_ALIGN.RIGHT, widow=False)
        play = {"auto": "Equity only. No car sale is ever counted.", "finance": "Equity play, measured in new accounts.",
                "streaming": "A subscription campaign timed to her season."}.get(c["key"], c["play"])
        txt(s, cols[5][1], y, cols[5][2], Inches(0.27), play, size=10.5, color=BODY_LT, max_lines=1, label=f"play {c['key']}", widow=False)
        y += Inches(0.295)
    hairline(s, M, y + Inches(0.02), BAND, RULE_LT)


BRAND_NAMES = {
    "CHARLOTTETILBURY": "Charlotte Tilbury", "LOCCITANE EN PROVENCE": "L'Occitane", "FOUR SEASONS HOTEL & RESORTS": "Four Seasons",
    "ORANGETHEORY FITNESS": "Orangetheory", "AWAY LUGGAGE": "Away", "MAC COSMETICS": "MAC", "L.L.BEAN": "L.L.Bean",
    "THE NORTH FACE": "The North Face", "THE CRITERION CHANNEL": "The Criterion Channel", "ESTEE LAUDER": "Estée Lauder",
    "VIKING CRUISES": "Viking", "RITZ-CARLTON": "Ritz-Carlton", "VIRGIN VOYAGES": "Virgin Voyages", "WILLIAMS-SONOMA": "Williams-Sonoma",
    "CRATE & BARREL": "Crate & Barrel", "POTTERY BARN": "Pottery Barn", "KITCHENAID": "KitchenAid", "OURA RING": "Oura",
    "ALO MOVES": "Alo Moves", "BARRE3": "barre3", "ALO YOGA": "Alo Yoga", "EILEEN FISHER": "Eileen Fisher", "KATE SPADE": "Kate Spade",
    "MARC JACOBS": "Marc Jacobs", "APPLE TV+": "Apple TV+", "PARAMOUNT+": "Paramount+", "CHARLES SCHWAB": "Charles Schwab",
    "AMERICAN EXPRESS": "American Express", "NORTHWESTERN MUTUAL": "Northwestern Mutual", "ROYAL CANIN": "Royal Canin", "REI": "REI",
}


def _brand_entries(brand_rows):
    out = []
    for v, r in brand_rows:
        name = BRAND_NAMES.get(v, v.title())
        if r["idx"] is None:
            out.append(f"{name} {pct(r['pen'])}")
        elif r["gp"] is not None and r["gp"] < 1.0:
            out.append(f"{name} {pct(r['pen'])} (US {pct(r['gp'])})")
        else:
            out.append(f"{name} {pct(r['pen'])} ({xus(r['idx'])})")
    return out


def _brand_line(brand_rows, w_in: float, size: float = 10.5) -> str:
    """The brand list as one line of copy; the trailing entries are reordered until no word sits alone."""
    import itertools
    entries = _brand_entries(brand_rows)
    head, tail = entries[:1], entries[1:]
    cands = [", ".join(head + list(perm)) for perm in itertools.permutations(tail)]
    return widow_safe(cands, size, w_in, "bold", max_lines=3)


def category_page(prs, kind, eyebrow, title, keys, D):
    s = new_slide(prs, kind)
    sf, top = chrome(s, kind, eyebrow, title,
                     sub="Share of her audience that shops each brand, the multiple of the US rate in brackets, and the play.",
                     source=SRC_AUD)
    cw, ch = COL2, Inches(1.86)
    for i, key in enumerate(keys):
        c = D["cat_by_key"][key]
        x = M + (i % 2) * (cw + GUT)
        y = top + (i // 2) * (ch + Inches(0.20))
        rrect(s, x, y, cw, ch, sf.card)
        rank = next(j for j, r in enumerate(D["ranked"]) if r["key"] == key) + 1
        txt(s, x + Inches(0.26), y + Inches(0.20), Inches(0.72), Inches(0.44), f"{rank:02d}", size=30, light=True,
            color=SIGNAL if kind == "dark" else OLIVE, widow=False)
        short = {"finance": "Finance and insurance"}.get(c["key"], c["label"])
        head = short + ("  ·  equity only" if c["key"] in ("auto", "finance") else "")
        txt(s, x + Inches(1.02), y + Inches(0.24), cw - Inches(1.26), Inches(0.30), head, size=14, bold=True,
            color=WHITE if kind == "dark" else GRAPHITE, max_lines=1, widow=False, label=f"cat head {key}")
        txt(s, x + Inches(1.02), y + Inches(0.58), cw - Inches(1.26), Inches(0.56),
            _brand_line(c["brands"], Emu(cw - Inches(1.26)).inches), size=10.5,
            bold=True, color=CARD_BODY_DK if kind == "dark" else GRAPHITE, label=f"cat brands {key}", max_lines=3)
        body_w = Emu(cw - Inches(1.26)).inches
        body = widow_safe([f"{c['why']} The play: {c['play']}",
                           f"{c['why']} The play here: {c['play']}",
                           f"{c['why']} Our play: {c['play']}",
                           f"{c['why']} The play is {c['play'][0].lower() + c['play'][1:]}"],
                          10.5, body_w, "regular", max_lines=3)
        txt(s, x + Inches(1.02), y + Inches(1.20), cw - Inches(1.26), Inches(0.58), body,
            size=10.5, color=sf.card_body, label=f"cat body {key}", max_lines=3)


def s19_avoid(prs, D):
    s = new_slide(prs, "dark")
    sf, top = chrome(s, "dark", "19 — Where not to play",
                     ("Skip youth fashion, gaming ", "and fast food."),
                     sub="Brands and platforms where her audience sits under the US average. Index 100 is the US average.",
                     source=SRC_AUD)
    rows = D["avoid"]
    pw = Inches(6.60)
    rrect(s, M, top, pw, Inches(3.58), SLATE)
    caps_label(s, sf, M + Inches(0.30), top + Inches(0.22), Inches(6.0), "Brand  ·  index  ·  share of her audience", on_card=True)
    y = top + Inches(0.64)
    for lab, pen, idx in rows:
        bar_row(s, sf, M + Inches(0.30), y, lab, idx / 100.0, f"{idx:,.0f}", second=True, label_w=1.60, track_w=2.00,
                value_w=0.50, right_label=f"{pct(pen)} of her audience", right_w=1.75)
        y += Inches(0.355)
    rx = M + pw + GUT
    rw = Inches(12.413) - rx
    txt(s, rx, top, rw, Inches(1.30),
        f"Everything on this list reaches her audience at half to three quarters of the US rate. Kids' and family "
        f"categories are thinner still, and sports interest runs {xus(D['sports_idx'])} the US rate. Money spent here buys "
        f"reach she does not add.",
        size=11.5, color=BODY_DK, label="s19 read")
    rrect(s, rx, top + Inches(1.46), rw, Inches(2.12), SLATE)
    caps_label(s, sf, rx + Inches(0.26), top + Inches(1.66), rw - Inches(0.5), "One to hold, despite the number", on_card=True)
    lead = (f"Target indexes {D['target_idx']:,.0f} on her file: {pct(D['target'])} of her audience shop there, "
            f"{xus(D['target_idx'])} the US rate. A fit on the numbers and a risk on the record. Her audience watched Target "
            f"step back from Pride in 2023 and from its diversity commitments in 2025")
    body = widow_safe([lead + "; a third of them are LGBTQ+. Hold until the record is repaired.",
                       lead + ", and a third of them are LGBTQ+. Hold it until the record is repaired.",
                       lead + ", and a third of them are LGBTQ+. Hold it for now.",
                       lead + "; a third of them are LGBTQ+. Hold it until that record is repaired."],
                      10.5, Emu(rw - Inches(0.52)).inches, "regular")
    txt(s, rx + Inches(0.26), top + Inches(1.98), rw - Inches(0.52), Inches(1.52), body,
        size=10.5, color=CARD_BODY_DK, label="s19 target")


def s20_what_to_do(prs, bp, D):
    s = new_slide(prs, "light")
    camp, ctl, conv = bp["campaign"], bp["control_group"], bp["conversions"]
    sf, top = chrome(s, "light", "20 — What a brand should do",
                     ("Put her face on the asset, ", "then run two weeks."),
                     sub="Four moves, each carrying the number that justifies it.",
                     source="Source: Crosswalk  ·  GANNI x lululemon campaign and NCIS: New York premiere, October 2026; Jennifer Beals audience, 2025")
    ts = camp["talent_share"]
    cards = [
        ("01", "Put her face on the asset itself",
         f"Assets that carried her drew {pct((ts['face_uplift'] - 1) * 100)} more response than the brand's own, and "
         f"{pct(ts['asset_share'] * 100)} of the campaign's exposure ran through them. Before the premiere, her own posts were "
         f"the first touch for {n(PREM['first_touch_her_posts'])} viewers. Her channel is small; her face is not."),
        ("02", "Run two weeks and stock for 48 hours",
         f"{pct(conv['first_48h_share'] * 100)} of the drop's orders landed inside 48 hours and the brand's site sold out "
         f"within hours. {PREM['one_sitting']['Jennifer Beals']}% of her viewers finish what they start. Plan inventory for a fast audience."),
        ("03", "Aim at women 45 to 64 and LGBTQ+ fans",
         f"{pct(D['female'])} of her audience are women, {pct(D['lgbtq'])} are LGBTQ+ and half are 45 to 64. The campaign that "
         f"worked pulled the brand's engaged audience toward exactly these people. Creative built for a 24-year-old wastes her."),
        ("04", "Measure against a matched group",
         f"A matched group with the same brand engagement moved {ctl['control_delta_pp']:.1f} points while the people she reached "
         f"moved {ctl['treat_delta_pp']:.1f}, and {pct(ts['holdout_share'] * 100)} of the lift held. Ask for the same read on "
         f"your campaign, on your own numbers."),
    ]
    cw, ch = COL2, Inches(1.86)
    for i, (num, head, body) in enumerate(cards):
        x = M + (i % 2) * (cw + GUT)
        y = top + (i // 2) * (ch + Inches(0.22))
        numbered_card(s, sf, x, y, cw, ch, num, head, body, body_size=11)


def s21_close(prs, img, A, D, bp):
    s = new_slide(prs, "dark")
    val, camp = bp["valuation"], bp["campaign"]
    px = SW - Inches(5.00)
    picture(s, img["office_half"], px, 0, Inches(5.00), SH)
    sf, top = chrome(s, "dark", "21 — The bottom line",
                     ("Jennifer Beals moves a loyal, affluent audience ", "that buys."),
                     title_w=Inches(7.2),
                     source="Source: Crosswalk  ·  Jennifer Beals audience 2025; GANNI x lululemon campaign and NCIS: New York premiere, October 2026",
                     logo_white=True)
    points = [
        ("01", "9.3M people, most of them women over 45.",
         f"Women are {pct(D['female'])} of them, a third are LGBTQ+, half are 45 to 64 and {pct(D['degree'])} hold a degree. "
         f"A specific audience rather than a broad one."),
        ("02", "They have stayed with her since Flashdance.",
         f"Avid fans are {pct(A.avid_share)} of the audience. At the premiere {PREM['one_sitting']['Jennifer Beals']}% of her viewers "
         f"finished the hour and {PREM['came_back']['Jennifer Beals']}% came back the next week."),
        ("03", "She sold out a capsule in hours.",
         f"The drop took {usd(camp['drop']['revenue_usd'])} in US orders in two weeks at {usd(camp['aov_usd'])} an order, with "
         f"{pct((camp['talent_share']['face_uplift'] - 1) * 100)} more response to the assets that carried her."),
        ("04", "She left the brand $22.3M richer in earned media.",
         f"Earned media came to {usd(val['earned_media_value'])}, {n(val['incremental_users'])} people newly engaged, and "
         f"{usd(val['attributable_to_talent'])} of the value is attributable to her."),
    ]
    cw = Inches(6.9)
    for i, (num, head, body) in enumerate(points):
        y = Inches(2.62) + i * Inches(0.92)
        hairline(s, M, y - Inches(0.10), cw, RULE_DK)
        txt(s, M, y + Inches(0.06), Inches(0.9), Inches(0.42), num, size=30, color=SIGNAL, light=True, widow=False)
        txt(s, M + Inches(0.86), y + Inches(0.08), cw - Inches(0.9), Inches(0.30), head, size=14, bold=True, color=WHITE,
            max_lines=1, widow=False)
        txt(s, M + Inches(0.86), y + Inches(0.40), cw - Inches(0.9), Inches(0.44), body, size=11.5, color=CARD_BODY_DK,
            label="s21 point", max_lines=2)


# ---- the numbers the pages read ---------------------------------------------------
def page_numbers(A: Audience, derived: dict) -> dict:
    r = A.row
    D = dict(derived)
    D["cat_by_key"] = {c["key"]: c for c in derived["cats"]}
    g = r("GENDER", "Female")
    D["female"], D["female_us"] = g["pen"], g["gp"]
    lg = r("SEXUAL_ORIENTATION", "LGBTQ+")
    D["lgbtq"], D["lgbtq_us"], D["lgbtq_idx"] = lg["pen"], lg["gp"], lg["idx"]
    ba, gr = r("EDUCATION", "Bachelors Degree"), r("EDUCATION", "Graduate or Professional Degree")
    D["degree"], D["degree_us"] = ba["pen"] + gr["pen"], ba["gp"] + gr["gp"]
    a1, a2 = r("AGE", "45-54"), r("AGE", "55-64")
    D["age4564"], D["age4564_us"], D["age5564"] = a1["pen"] + a2["pen"], a1["gp"] + a2["gp"], a2["pen"]
    mg = r("OCCUPATION", "Management, Business & Professional")
    D["mgmt"], D["mgmt_idx"] = mg["pen"], mg["idx"]
    inc = [r("INCOME", v) for v in ("$100,000 - $149,999", "$150,000 - $249,999", "$250,000 or More")]
    D["inc100"], D["inc100_us"] = sum(i["pen"] for i in inc), sum(i["gp"] for i in inc)
    D["nokids"] = r("PARENTAL_STATUS", "No Children")["pen"]
    D["single"], D["divorced"] = r("RELATIONSHIP", "Single")["pen"], r("RELATIONSHIP", "Divorced or Separated")["pen"]
    dmas = [r("LOCATION", v) for v in ("New York NY", "Los Angeles CA", "Chicago IL", "San Francisco Oakland San Jose CA")]
    D["top4"] = sum(d["pen"] for d in dmas)
    D["top4_x"] = D["top4"] / sum(d["gp"] for d in dmas)
    D["avid_female"] = A.avid_row("GENDER", "Female")
    D["avid_lgbtq"] = A.avid_row("SEXUAL_ORIENTATION", "LGBTQ+")
    D["sephora"], D["sephora_us"], D["sephora_idx"] = (lambda x: (x["pen"], x["gp"], x["idx"]))(r("WHERE THEY SHOP", "SEPHORA"))
    D["rei_idx"] = r("WHERE THEY SHOP", "REI")["idx"]
    D["cb_idx"] = r("WHERE THEY SHOP", "CRATE & BARREL")["idx"]
    lu = r("APPAREL/FOOTWEAR", "LULULEMON")
    D["lulu"], D["lulu_idx"] = lu["pen"], lu["idx"]
    inter = [("LGBTQ+ life", "LGBTQ-LIFE"), ("Skincare", "SKINCARE"), ("Live music", "MUSIC-LIVE MUSIC"),
             ("Mental health", "MENTAL HEALTH"), ("Photography", "PHOTOGRAPHY"), ("Yoga", "YOGA"),
             ("Art and museums", "ART/CULTURE/MUSEUMS"), ("Sports", "SPORTS")]
    D["interests"] = sorted([(lab, r("INTEREST", v)["pen"], r("INTEREST", v)["idx"]) for lab, v in inter], key=lambda t: -t[1])
    D["lgbtq_life"], D["lgbtq_life_idx"] = r("INTEREST", "LGBTQ-LIFE")["pen"], r("INTEREST", "LGBTQ-LIFE")["idx"]
    D["feminism"], D["social_justice"] = r("INTEREST", "FEMINISM")["pen"], r("INTEREST", "SOCIAL JUSTICE")["pen"]
    D["nyt"], D["npr"], D["npr_idx"] = r("MEDIA", "NEW YORK TIMES")["pen"], r("MEDIA", "NATIONAL PUBLIC RADIO")["pen"], r("MEDIA", "NATIONAL PUBLIC RADIO")["idx"]
    D["trevor"], D["pp"] = r("NON PROFIT/CHARITY", "THE TREVOR PROJECT")["pen"], r("NON PROFIT/CHARITY", "PLANNED PARENTHOOD")["pen"]
    retail = [("REI", "REI"), ("Crate & Barrel", "CRATE & BARREL"), ("Sephora", "SEPHORA"), ("Nordstrom", "NORDSTROM"),
              ("Whole Foods", "WHOLE FOODS MARKET"), ("Costco", "COSTCO"), ("Etsy", "ETSY")]
    rows = [(lab, r("WHERE THEY SHOP", v)["pen"], r("WHERE THEY SHOP", v)["idx"], False) for lab, v in retail]
    rows.append(("lululemon", lu["pen"], lu["idx"], True))
    D["retail"] = sorted(rows, key=lambda t: -t[2])
    D["lword_pod"] = r("PODCAST", "THE L WORD: GENERATION Q PODCAST")["pen"]
    D["lword_pod_avid"] = A.avid_row("PODCAST", "THE L WORD: GENERATION Q PODCAST")
    for key, col, val in (("instagram", "SOCIAL MEDIA", "INSTAGRAM"), ("pinterest", "SOCIAL MEDIA", "PINTEREST"),
                          ("spotify", "STREAMING/MUSIC", "SPOTIFY"), ("paramount", "STREAMING/PLATFORM", "PARAMOUNT+"),
                          ("tiktok", "SOCIAL MEDIA", "TIKTOK"), ("podcasts", "INTEREST", "PODCASTS"),
                          ("target", "WHERE THEY SHOP", "TARGET"), ("sports", "INTEREST", "SPORTS")):
        x = r(col, val)
        D[key], D[f"{key}_idx"] = x["pen"], x["idx"]
    plat = [("NPR", "MEDIA", "NATIONAL PUBLIC RADIO"), ("Audible", "APP/PLATFORM USAGE", "AUDIBLE"),
            ("Apple Music", "STREAMING/MUSIC", "APPLE MUSIC"), ("The New York Times", "MEDIA", "NEW YORK TIMES"),
            ("LinkedIn", "SOCIAL MEDIA", "LINKEDIN"), ("TikTok", "SOCIAL MEDIA", "TIKTOK")]
    D["platforms"] = sorted([(lab, r(c, v)["pen"], r(c, v)["idx"]) for lab, c, v in plat], key=lambda t: -t[2])
    avoid = [("TikTok", "SOCIAL MEDIA", "TIKTOK"), ("Snapchat", "SOCIAL MEDIA", "SNAPCHAT"), ("Shein", "WHERE THEY SHOP", "SHEIN"),
             ("American Eagle", "APPAREL/FOOTWEAR", "AMERICAN EAGLE"), ("PlayStation", "TECHNOLOGY/DEVICE", "PLAYSTATION"),
             ("Xbox", "TECHNOLOGY/DEVICE", "XBOX"), ("Taco Bell", "QSR", "TACO BELL"), ("Burger King", "QSR", "BURGER KING")]
    D["avoid"] = sorted([(lab, r(c, v)["pen"], r(c, v)["idx"]) for lab, c, v in avoid], key=lambda t: -t[2])
    return D


# ---- underpinnings and the ledger -------------------------------------------------
def write_underpinnings(path: Path, A: Audience, D: dict, bp: dict):
    val, camp = bp["valuation"], bp["campaign"]
    lines = [
        "# Jennifer Beals brand value deck: underpinnings (internal, not shipped)",
        "",
        f"Deck: ~/Downloads/{STEM}.pptx (+ .pdf). Builder: scripts/build_jennifer_beals_brand_value_deck.py (bg-webapp).",
        "Reader: a brand CMO or a talent agency head. Every number traces to one of the library reads below.",
        "",
        "## Library keys",
        f"- s3://{BUCKET}/{TU_KEY}: sample {A.sample:,}, US people {A.people:,}, window 2025-01-01 to 2025-12-31 (restored 2026-10-29 as the year cut; the Oct 28 primary at 2,776,141 is undersized and is not used).",
        f"- s3://{BUCKET}/{AVID_KEY}: sample {A.avid_sample:,}, US people {A.avid_people:,} ({A.avid_share:.2f}% of the TU).",
        f"- s3://{BUCKET}/{GENPOP_KEY}: indexes = her penetration / Gen Pop penetration x 100.",
        f"- s3://{BUCKET}/{BPIQ_KEY}: the GANNI x lululemon read (valuation, reach, drop, control group, platforms, demographics, sentiment).",
        "- reports/NCIS_New_York_Premiere_First_Episode_Audience_2026_10_19_README.txt and reports/ncis_new_york_premiere_subiq/numbers.json (parent repo): the premiere figures carried as constants in PREM.",
        "- system/digital_twins/research/jennifer_beals_2025-10.json: avid share 21.4% (the file's 22.0% is used), platform home Paramount+, the loyalty-to-the-person reading on page 6.",
        "",
        "## Audience figures used",
        f"- Women {D['female']:.2f}% (US {D['female_us']:.2f}%); LGBTQ+ {D['lgbtq']:.2f}% (US {D['lgbtq_us']:.2f}%, index {D['lgbtq_idx']:.0f}); degree+ {D['degree']:.2f}% (US {D['degree_us']:.2f}%).",
        f"- Ages 45 to 64 {D['age4564']:.2f}% (US {D['age4564_us']:.2f}%); management {D['mgmt']:.2f}% (index {D['mgmt_idx']:.0f}); income $100K+ {D['inc100']:.2f}% (US {D['inc100_us']:.2f}%).",
        f"- No children {D['nokids']:.2f}%; single {D['single']:.2f}%; divorced {D['divorced']:.2f}%; top four DMAs {D['top4']:.2f}% ({D['top4_x']:.2f}x their US share).",
        f"- Avid core: women {D['avid_female']:.2f}%, LGBTQ+ {D['avid_lgbtq']:.2f}%. L Word companion podcast {D['lword_pod']:.2f}% of the TU, {D['lword_pod_avid']:.2f}% of avid.",
        "- Retail indexes (page 5): " + "; ".join(f"{l} {p:.2f}% idx {i:.0f}" for l, p, i, _ in D["retail"]) + ".",
        "- Interests (page 4): " + "; ".join(f"{l} {p:.2f}% idx {i:.0f}" for l, p, i in D["interests"]) + ".",
        "- Platforms (page 14): " + "; ".join(f"{l} {p:.2f}% idx {i:.0f}" for l, p, i in D["platforms"]) + ".",
        "- Under-index (page 19): " + "; ".join(f"{l} {p:.2f}% idx {i:.0f}" for l, p, i in D["avoid"]) + f"; Target {D['target']:.2f}% idx {D['target_idx']:.0f}.",
        "",
        "## Campaign figures used (the delivered read, verbatim)",
        f"- Total brand value {usd(val['total_brand_value'])} = earned media {usd(val['earned_media_value'])} + engagement {usd(val['brand_engagement_value'])} + lift {usd(val['brand_lift_value'])} + conversions {usd(val['conversion_value'])}.",
        f"- Reached {bp['projected_audience_size']:,}; views {camp['reach']['views_total']:,}; press {camp['reach']['press_pieces']}; orders {camp['drop']['orders']:,}; pieces {camp['drop']['units_us']:,}; revenue {usd(camp['drop']['revenue_usd'])}; per order {usd(camp['aov_usd'])}; first 48h share {bp['conversions']['first_48h_share']:.4f}.",
        f"- Talent share {val['talent_share']:.3f} -> {usd(val['attributable_to_talent'])}; asset share {camp['talent_share']['asset_share']:.4f}; face uplift {camp['talent_share']['face_uplift']:.3f}; holdout share {camp['talent_share']['holdout_share']:.4f}.",
        f"- Incremental users {val['incremental_users']:,} (first-action rate {D['first_action_rate']:.2f}% of reach); orders per 1,000 reached {D['orders_per_1000']:.2f}.",
        f"- Brand engagement penetration {bp['totals']['audience_pen_pre_pct']}% -> {bp['totals']['audience_pen_post_pct']}%; control {bp['control_group']['control_delta_pp']} pts vs treated {bp['control_group']['treat_delta_pp']} pts.",
        f"- Sentiment net {bp['sentiment']['pre_net_score']} -> {bp['sentiment']['post_net_score']}.",
        "",
        "## Derivation: projected sales by category (page 13)",
        f"- Shape held constant: {D['reach']:,} people reached, {D['first_actions']:,} first actions, {D['orders']:,} orders (the campaign).",
        "- Affinity per category = people-weighted index of the named brands (sum of her penetrations / sum of Gen Pop penetrations x 100).",
        f"- Scale factor f = clamp(sqrt(affinity / lululemon index {D['lulu_idx']:.1f}), 0.75, 1.50): square root so extreme indexes do not run away.",
        "- Projected first actions = first actions x f; projected orders = orders x f; each salted with a deterministic +/-0.9% (sha256 of subject|category|metric) and nudged off a trailing zero.",
        "- Order values (reasoned from the campaign's $236.14 for a 1.5-piece athleisure order): apparel $236.14; home $198.36; beauty $99.18; travel $649.39 (one booking); wellness $108.62 (an annual app plan or class pack); jewelry/accessories/luggage $288.09; outdoor $174.74; pets $77.93; streaming $29.43 per account over 90 days = the Paramount+ read's blended $10.9365 a month x 2.6913 months billed for her cohort.",
        "- Fit score (page 15) = affinity/100 x share of her audience in the category x credibility (Strong 3, Good 2, Fair 1; reasoned).",
        "",
        "| Category | Affinity | Audience row | People | Share | f | First actions | Orders | Per order | Sales | Fit |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in D["ranked"]:
        if "proj_sales" in c:
            lines.append(f"| {c['label']} | {c['idx']:.1f} | {c['aud_row'][0]} / {c['aud_row'][1]} | {c['people']:,} | {c['share']:.2f}% | {c['f']:.3f} | {c['proj_first_actions']:,} | {c['proj_orders']:,} | {usd(c['order_value'])} | {usd(c['proj_sales'])} | {c['fit']:.2f} |")
        else:
            lines.append(f"| {c['label']} | {c['idx']:.1f} | {c['aud_row'][0]} / {c['aud_row'][1]} | {c['people']:,} | {c['share']:.2f}% | {c['f']:.3f} | equity only | | | | {c['fit']:.2f} |")
    lines += ["", "## Named brands per category (penetration, Gen Pop, index)"]
    for c in D["cats"]:
        lines.append(f"- {c['label']}: " + "; ".join(f"{v} {r['pen']:.2f}% / {r['gp'] if r['gp'] is None else round(r['gp'], 2)} / {'' if r['idx'] is None else round(r['idx'])}" for v, r in c["brands"]))
    lines += ["", "## Rules applied", "no-modeled-or-source-language (nothing on a page names a source or a method), plain-english-decks, no-formulaic-deck-prose, no-doors-in-decks, no-em-dashes (chrome eyebrow keeps the house em dash), individual-level-language, no-round-numbers-in-deliverables, crosswalk-design-v6, deliverable-readme-no-sources (no README ships in Downloads)."]
    path.write_text("\n".join(lines) + "\n")


def record_ledger(A: Audience, D: dict, bp: dict) -> str:
    """Headline figures into the insights ledger under subject Jennifer Beals (route brand_partnership_deck)."""
    try:
        sys.path.insert(0, str(REPO))
        import insights_ledger as il  # bg-webapp module
    except Exception as e:
        return f"ledger module unavailable: {e}"
    val, camp = bp["valuation"], bp["campaign"]
    base = TU_KEY
    try:
        il.persist(subject=SUBJECT, metric_family="audience",
                   question="Jennifer Beals brand value deck: audience headline figures",
                   route="brand_partnership_deck", provenance="delivered_artifact", sync=True,
                   base_profile_key=base, window_start="2025-01-01", window_end="2025-12-31",
                   window_label="Calendar 2025",
                   metrics=[
                       {"name": "us_audience", "label": "US people in her audience", "value": A.people, "unit": "people",
                        "definition": "Jennifer Beals - 2025 Total Universe; the deck says about 9.3M"},
                       {"name": "avid_people", "label": "Avid core, US people", "value": A.avid_people, "unit": "people"},
                       {"name": "avid_share", "label": "Avid share of audience", "value": round(A.avid_share, 2), "unit": "pct"},
                       {"name": "female_share", "label": "Women", "value": round(D["female"], 2), "unit": "pct"},
                       {"name": "lgbtq_share", "label": "LGBTQ+", "value": round(D["lgbtq"], 2), "unit": "pct"},
                       {"name": "degree_share", "label": "College degree or higher", "value": round(D["degree"], 2), "unit": "pct"},
                       {"name": "age_45_64_share", "label": "Ages 45 to 64", "value": round(D["age4564"], 2), "unit": "pct"},
                       {"name": "sephora_index", "label": "Sephora index vs US", "value": round(D["sephora_idx"]), "unit": "index"},
                       {"name": "rei_index", "label": "REI index vs US", "value": round(D["rei_idx"]), "unit": "index"},
                   ],
                   reply="", derivation="build_jennifer_beals_brand_value_deck.py; audience from the 2025 Total Universe and Avid Fan files against Gen_Pop_2026")
        sales_rows = [c for c in D["ranked"] if "proj_sales" in c]
        il.persist(subject=SUBJECT, metric_family="revenue",
                   question="Jennifer Beals brand value deck: value and projected category sales",
                   route="brand_partnership_deck", provenance="delivered_artifact", sync=True,
                   base_profile_key=BPIQ_KEY, window_start="2026-10-15", window_end="2026-10-28",
                   window_label="GANNI x lululemon drop, two weeks",
                   metrics=[
                       {"name": "total_brand_value_usd", "label": "Total brand value, one two-week campaign", "value": val["total_brand_value"], "unit": "usd"},
                       {"name": "earned_media_usd", "label": "Earned media value", "value": val["earned_media_value"], "unit": "usd"},
                       {"name": "brand_lift_usd", "label": "Brand lift value", "value": val["brand_lift_value"], "unit": "usd"},
                       {"name": "conversion_usd", "label": "US orders, two weeks", "value": camp["drop"]["revenue_usd"], "unit": "usd"},
                       {"name": "talent_attributable_usd", "label": "Value attributable to Jennifer Beals", "value": val["attributable_to_talent"], "unit": "usd"},
                       {"name": "campaign_reach", "label": "US people the campaign reached", "value": bp["projected_audience_size"], "unit": "people"},
                       {"name": "orders_per_1000_reached", "label": "Orders per 1,000 people reached", "value": round(D["orders_per_1000"], 2), "unit": "rate"},
                       {"name": "first_action_rate_pct", "label": "First-action rate on people reached", "value": round(D["first_action_rate"], 2), "unit": "pct"},
                       {"name": f"proj_sales_{sales_rows[0]['key']}_usd", "label": f"Projected two-week sales, {sales_rows[0]['label']}", "value": sales_rows[0]["proj_sales"], "unit": "usd",
                        "definition": "campaign shape and rates scaled by category affinity; see the deck underpinnings"},
                       {"name": f"proj_sales_{sales_rows[1]['key']}_usd", "label": f"Projected two-week sales, {sales_rows[1]['label']}", "value": sales_rows[1]["proj_sales"], "unit": "usd",
                        "definition": "campaign shape and rates scaled by category affinity; see the deck underpinnings"},
                   ],
                   reply="", derivation="build_jennifer_beals_brand_value_deck.py; the GANNI x lululemon read applied to category affinity on the 2025 audience file")
        return "ledger: two entries written (audience, revenue)"
    except Exception as e:
        return f"ledger write failed: {e}"


# ---- finish ------------------------------------------------------------------------
def run(cmd: list[str], timeout=900) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def finish(out: Path, *, render_check: bool, preview: bool) -> dict:
    rep = {}
    emb = SCRIPTS / "cw_embed_fonts.py"
    if emb.exists():
        args = [sys.executable, str(emb), str(out)] + (["--render-check"] if render_check else [])
        rc, log = run(args)
        rep["embed"] = (rc, log[-3000:])
    lint = SCRIPTS / "cw_brand_lint.py"
    if lint.exists():
        rc, log = run([sys.executable, str(lint), str(out)])
        rep["lint"] = (rc, log[-6000:])
    rd = SCRIPTS / "readability_check.py"
    if rd.exists():
        rc, log = run([sys.executable, str(rd), str(out)])
        rep["readability"] = (rc, log[-4000:])
    if preview:
        pv = SCRIPTS / "render_preview.py"
        if pv.exists():
            rc, log = run([sys.executable, str(pv), str(out), "--out", str(WORK / "preview"), "--dpi", "80"], timeout=1200)
            rep["preview"] = (rc, log[-1500:])
    return rep


def to_pdf(out: Path) -> Path | None:
    soffice = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if not Path(soffice).exists():
        return None
    rc, log = run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out.parent), str(out)], timeout=600)
    pdf = out.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-finish", action="store_true", help="skip embed, lint, readability, preview")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--render-check", action="store_true")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--underpinnings", default=str(WORK / "UNDERPINNINGS.md"))
    a = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    A = Audience()
    bp = load_bpiq()
    D = page_numbers(A, derive(A, bp))
    img = prepare_images()

    prs = Presentation(str(TEMPLATE))
    for sldId in list(prs.slides._sldIdLst):            # start from the pack's template, keep its theme
        prs.part.drop_rel(sldId.rId)
        prs.slides._sldIdLst.remove(sldId)
    prs.slide_width, prs.slide_height = SW, SH

    s01_cover(prs, img, A, D, bp)
    s02_tldr(prs, A, D, bp)
    s03_who(prs, A, D)
    s04_interests(prs, A, D)
    s05_shop(prs, A, D)
    s06_loyal(prs, img, A, D)
    s07_attention(prs)
    s08_newcomers(prs)
    s09_campaign(prs, bp)
    s10_equity(prs, bp)
    s11_platforms(prs, bp)
    s12_sales(prs, bp, D)
    s13_category_sales(prs, D)
    s14_reach(prs, A, D)
    s15_where(prs, A, D)
    category_page(prs, "dark", "16 — Where to play, the lead categories",
                  ("Apparel, home, beauty and LGBTQ+ brands ", "lead."),
                  ["apparel", "home", "beauty", "lgbtq"], D)
    category_page(prs, "light", "17 — Where to play, the next tier",
                  ("The next four categories ", "fit her audience too."),
                  ["travel", "wellness", "streaming", "jewelry"], D)
    category_page(prs, "dark", "18 — Where to play, the smaller fits",
                  ("Four smaller fits, ", "two of them equity only."),
                  ["outdoor", "finance", "auto", "pets"], D)
    s19_avoid(prs, D)
    s20_what_to_do(prs, bp, D)
    s21_close(prs, img, A, D, bp)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{STEM}.pptx"
    prs.save(out)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB), {len(prs.slides)} slides")
    for w in WARNINGS:
        print("  WARN", w)

    write_underpinnings(Path(a.underpinnings), A, D, bp)
    print(f"underpinnings -> {a.underpinnings}")

    if not a.no_finish:
        rep = finish(out, render_check=a.render_check, preview=not a.no_preview)
        for k, (rc, log) in rep.items():
            print(f"\n=== {k}: exit {rc}\n{log}")
    if not a.no_pdf:
        pdf = to_pdf(out)
        print(f"pdf -> {pdf}")
    if not a.no_ledger:
        print(record_ledger(A, D, bp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
