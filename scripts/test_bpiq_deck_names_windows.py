#!/usr/bin/env python3
"""The valuation deck names everyone, dates the right window, and reads
the matched group from one field (2026-11-18, Coach x WNBA).

A client noticed that a valuation deck credited a three-player
partnership to the first player on the list. The same deck labeled a
season's counts with the 17 days between the season's end and the day
the valuation was made, and one page showed the matched group's gain as
0 while another showed +2.1 points. No number was wrong; the words, the
dates and that one figure were. These checks hold the builder to:

  [1] names: one name is said as it always was; two or three are all
      named ('A, B and C', and 'or' where the sentence is about engaging
      with any one); four or more, and a property (a league, a team, a
      show), are the partnership by its own name. No page credits a
      total to one name out of several. Possessives never double, and a
      plural subject takes a plural verb.
  [2] windows: every date and day count on every page is the window the
      valuation's after counts cover, whichever way the valuation
      stores it (the campaign's dates with a later tail, an order's
      window from the campaign's first day, the days after an airing,
      the live pull's campaign plus days after, a year slice, bare
      dates, no dates). The cover prints the same window.
  [3] the matched group: its gain reads from the valuation's own field
      on every page. A valuation whose lift count is the rise beyond
      the matched group shows the subtraction; one that priced the whole
      rise shows that count and states the matched group's move in the
      points the engagement page prints. No figure differs from the
      valuation's.
  [4] a deck for one person on an order's window reads as it did before
      this change (the sentences are frozen here from the builder as it
      stood on 2026-11-17).
  [5] the finished file: a three-name deck and a property deck leave
      the builder READY (fonts embedded, no lint FAIL, no plain-English
      flag), every headline is one sentence of twelve words or fewer on
      at most two lines, and the layout self-check finds nothing.

Made-up names and figures. Hermetic: no S3, no network, no model, no
mail, no app import. Run:
    python3 scripts/test_bpiq_deck_names_windows.py
"""
from __future__ import annotations

import contextlib
import copy
import io
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("CORPUS_CATALOG", "0")
os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ.setdefault("PM_MAIL_OFF", "1")

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.modules.setdefault("app", None)          # the dashboard app never boots in a test

from pptx.util import Emu  # noqa: E402

from migration import bpiq_deck_builder as B  # noqa: E402

FAIL = 0


def check(label, cond, detail=""):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}" + (f"  ({str(detail)[:400]})" if detail else ""))


# --------------------------------------------------------------- fixtures
BRAND = "Fernhill"
PROPERTY = "Northline"                       # a league
PROJECT = "Fernhill x Northline"
PEOPLE = ["Mara Vale", "Otis Brandt", "Ines Okafor", "Tess Rivers", "Joss Hale"]
MADE = "2026-08-03"

# name -> (pre, event, post, post_period_days, the days the after counts cover, the window the deck must print)
WINDOWS = {
    # the campaign's dates, then a tail up to the day the valuation was made: the counts are the campaign's
    "season": dict(pre=("2025-02-10", "2026-02-09", 365), event=("2026-02-10", "2026-07-19", 160),
                   post=("2026-07-20", "2026-08-03", 15), stated=15, counted=160,
                   after=("2026-02-10", "2026-07-19", 160)),
    # an order: the window runs from the campaign's first day through the days after it
    "order": dict(pre=("2025-08-19", "2026-02-09", 175), event=("2026-02-10", "2026-07-19", 160),
                  post=("2026-02-10", "2026-08-03", 175), stated=175, counted=160,
                  after=("2026-02-10", "2026-08-03", 175)),
    # the days after an airing: the counts are those later days
    "after": dict(pre=("2026-02-03", "2026-02-09", 7), event=("2026-02-10", "2026-02-14", 5),
                  post=("2026-02-15", "2026-02-21", 7), stated=7, counted=7,
                  after=("2026-02-15", "2026-02-21", 7)),
    # the live pull: the counts cover the campaign and the days after it together
    "pull": dict(pre=("2025-11-12", "2026-02-09", 90), event=("2026-02-10", "2026-04-10", 60),
                 post=("2026-04-11", "2026-05-10", 30), stated=90, counted=90,
                 after=("2026-02-10", "2026-05-10", 90)),
    # a year slice: a second window of no length
    "slice": dict(pre=("2025-02-10", "2026-02-09", 365), event=("2026-02-10", "2026-07-19", 160),
                  post=("2026-07-19", "2026-07-19", 0), stated=160, counted=160,
                  after=("2026-02-10", "2026-07-19", 160)),
}


def _messy(v: float) -> int:
    v = int(round(v))
    return v + 3 if v % 10 == 0 else v


def valuation(names, *, qtype="talent", project=PROJECT, brand=BRAND, shape="season", control=True,
              lift="net", held_audience=0) -> dict:
    """A coherent valuation document, by hand: every lens multiplies out
    and the total is the sum of its parts."""
    w = WINDOWS[shape]
    n, projected = 9237, 2814563
    weight = projected / n
    pre_users, post_users = 1974, 3126
    pre_proj, post_proj = _messy(pre_users * weight), _messy(post_users * weight)
    pre_hits, post_hits = 5381, 8273
    c_pre, c_post = 21.11, 23.07
    c_delta = round(c_post - c_pre, 2)
    pre_pen, post_pen = round(pre_users / n * 100, 2), round(post_users / n * 100, 2)
    gain = post_proj - pre_proj
    held = int(round(projected * c_delta / 100.0))
    incr = gain if (lift == "whole" or not control) else gain - held
    rates = {"bev_per_user": 2.5, "blv_per_incr_user": 5.0, "conv_value_per_user": 180.0,
             "emv_per_user": {"Instagram": 11.0, "TikTok": 7.0, "Direct (Brand Site)": 5.0}}
    emv_rows = [("Instagram", 211433, 130056, 11.0), ("TikTok", 96381, 56162, 7.0),
                ("Direct (Brand Site)", 71917, 49276, 5.0)]
    emv = [{"platform": p, "post_users_projected": a, "pre_users_projected": b,
            "incremental_users_projected": a - b, "emv_per_user_rate": r, "emv_value": round((a - b) * r, 2)}
           for p, a, b, r in emv_rows]
    orders = 41267
    bev, blv, cv = round(post_proj * 2.5, 2), round(incr * 5.0, 2), round(orders * 180.0, 2)
    emv_total = round(sum(r["emv_value"] for r in emv), 2)
    doc = {
        "project_name": project,
        "qualifier_type": qtype,
        "qualifier_value": list(names),
        "brand_partner": brand,
        "start_date": w["event"][0], "end_date": w["event"][1],
        "attribution_window_days": w["post"][2],
        "pre_period": dict(zip(("start", "end", "days"), w["pre"])),
        "event_period": dict(zip(("start", "end", "days"), w["event"])),
        "post_period": dict(zip(("start", "end", "days"), w["post"])),
        "audience_size": n, "projected_audience_size": projected,
        "pre_period_days": w["pre"][2], "post_period_days": w["stated"],
        "totals": {"pre_hits": pre_hits, "post_hits": post_hits, "pre_users": pre_users, "post_users": post_users,
                   "pre_users_projected": pre_proj, "post_users_projected": post_proj,
                   "pre_hits_per_day": round(pre_hits / w["pre"][2], 2),
                   "post_hits_per_day": round(post_hits / w["counted"], 2),
                   "audience_pen_pre_pct": pre_pen, "audience_pen_post_pct": post_pen},
        "conversions": {"pre_users_projected": 30118, "post_users_projected": orders, "low_signal": False,
                        "enabled": True},
        "control_group": ({"enabled": True, "control_size": n, "projected_control_size": projected,
                           "treat_pre_pen_pct": pre_pen, "treat_post_pen_pct": post_pen,
                           "control_pre_pen_pct": c_pre, "control_post_pen_pct": c_post,
                           "treat_delta_pp": round(post_pen - pre_pen, 2), "control_delta_pp": c_delta,
                           "incremental_lift_pp": round(post_pen - pre_pen - c_delta, 2)}
                          if control else {"enabled": False}),
        "demographics": {"post": {
            "gender": [{"value": "Female", "percentage": 58.31}, {"value": "Male", "percentage": 39.44},
                       {"value": "Prefer Not to Say", "percentage": 2.25}],
            "age": [{"value": "18-24", "percentage": 23.7}, {"value": "25-34", "percentage": 31.2},
                    {"value": "35-44", "percentage": 24.6}, {"value": "45-54", "percentage": 20.5}]}},
        "top_brand_properties": [{"common_name": "fernhill.com home", "hits_projected": 913277},
                                 {"common_name": "Fernhill on Instagram", "hits_projected": 644159},
                                 {"common_name": "fernhill.com/browse/women", "hits_projected": 301846}],
        "top_brand_properties_pre": [{"common_name": "fernhill.com home", "hits_projected": 702341},
                                     {"common_name": "Fernhill on Instagram", "hits_projected": 498213},
                                     {"common_name": "fernhill.com/browse/women", "hits_projected": 244917}],
        "created_at": MADE + "T15:02:11+00:00",
        "valuation": {"total_brand_value": round(bev + blv + cv + emv_total, 2), "brand_engagement_value": bev,
                      "earned_media_value": emv_total, "brand_lift_value": blv, "conversion_value": cv,
                      "incremental_users": incr, "rates": rates, "emv_breakdown": emv},
    }
    if held_audience:
        doc["campaign"] = {"talent_audience_held": held_audience}
    return doc


def build(doc, category="FASHION"):
    with contextlib.redirect_stdout(io.StringIO()):
        return B.build_presentation(copy.deepcopy(doc), category=category)


def pages(prs) -> list:
    return [[" ".join(sh.text_frame.text.split()) for sh in s.shapes
             if sh.has_text_frame and sh.text_frame.text.strip()] for s in prs.slides]


def page_named(pg: list, eyebrow: str) -> list:
    for texts in pg:
        if texts and re.fullmatch(r"\d\d\s+" + re.escape(eyebrow.upper()), texts[0]):
            return texts
    return []


def after(texts: list, label: str) -> str:
    return texts[texts.index(label) + 1]


def blob(pg) -> str:
    return "\n".join(t for texts in pg for t in texts)


def headlines(prs) -> list:
    out = []
    for i, s in enumerate(prs.slides, start=1):
        for sh in s.shapes:
            if not sh.has_text_frame:
                continue
            top = Emu(sh.top).inches
            for p in sh.text_frame.paragraphs:
                runs = [r for r in p.runs if r.text.strip()]
                if runs and (runs[0].font.size.pt if runs[0].font.size else 0) >= 28 and 0.8 < top < 1.6:
                    out.append((i, "".join(r.text for r in p.runs)))
    return out


def close_page(pg: list) -> list:
    """The closing page's own text: the statement, its reading, the next step."""
    return [t for t in page_named(pg, "What to do next")[1:] if t != B.FOOTER_LOCKUP and not t.isdigit()]


def statement(pg: list) -> str:
    c = close_page(pg)
    return c[0] if c else ""


def short_title(pg: list) -> str:
    texts = [t for t in page_named(pg, "The short version")[1:] if t != B.FOOTER_LOCKUP and not t.isdigit()]
    return texts[0] if texts else ""


def money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def solo_credit(pg, names) -> list:
    """Text that names some of the people and not all of them."""
    bad = []
    for texts in pg:
        for t in texts:
            said = [n for n in names if n in t]
            if said and len(said) != len(names):
                bad.append(t[:120])
    return bad


# ------------------------------------------------------------------ [1]
def test_names():
    print("[1] names: one, two, three, five, and a property")
    days = WINDOWS["season"]["after"][2]

    # one name: said alone, as before
    one = pages(build(valuation(PEOPLE[:1])))
    b1 = blob(one)
    check("one name: the cover, the short version, the method page and the close all name them",
          f"A look at the value Mara Vale brought to {BRAND} in {days} days." in one[0]
          and "who engaged with Mara Vale." in b1 and "people who engaged with Mara Vale through two windows" in b1
          and statement(one) == f"Mara Vale moved {BRAND} engagement past a matched group.", (one[0], statement(one)))
    check("one name: the short version's headline is theirs",
          short_title(one).startswith("Mara Vale's partnership delivered $"), short_title(one))

    # two names
    two_names = PEOPLE[:2]
    two = pages(build(valuation(two_names)))
    b2 = blob(two)
    check("two names: the cover claim names both",
          f"A look at the value Mara Vale and Otis Brandt brought to {BRAND} in {days} days." in two[0], two[0])
    check("two names: engaging with either one is 'or'",
          "who engaged with Mara Vale or Otis Brandt." in b2
          and "people who engaged with Mara Vale or Otis Brandt through two windows" in b2)
    check("two names: the headline and the close are both of them or the partnership by name, never one",
          short_title(two).startswith((f"The {PROJECT} partnership delivered $",
                                       "Mara Vale and Otis Brandt's partnership delivered $"))
          and statement(two).startswith((f"The {PROJECT} partnership moved", "Mara Vale and Otis Brandt moved")),
          (short_title(two), statement(two)))
    check("two names: no page names one without the other", not solo_credit(two, two_names), solo_credit(two, two_names))

    # three names
    three_names = PEOPLE[:3]
    three = pages(build(valuation(three_names)))
    b3 = blob(three)
    check("three names: the cover claim names all three in order",
          f"A look at the value Mara Vale, Otis Brandt and Ines Okafor brought to {BRAND} in {days} days." in three[0],
          three[0])
    check("three names: engaging with any of them is 'or'",
          "who engaged with Mara Vale, Otis Brandt or Ines Okafor." in b3
          and "people who engaged with Mara Vale, Otis Brandt or Ines Okafor through two windows" in b3)
    check("three names: the headline is the partnership by its own name (three names do not fit twelve words)",
          short_title(three) == f"The {PROJECT} partnership delivered "
          f"${valuation(three_names)['valuation']['total_brand_value']:,.2f} in brand value.", short_title(three))
    check("three names: the close is the partnership by its own name",
          statement(three) == f"The {PROJECT} partnership moved brand engagement past a matched group.", statement(three))
    check("three names: no page names one or two of them without the rest",
          not solo_credit(three, three_names), solo_credit(three, three_names))
    check("three names: nothing reads 'Mara Vale's partnership' or 'Mara Vale moved'",
          "Mara Vale's partnership" not in b3 and "Mara Vale moved" not in b3 and "Mara Vale brought" not in b3)

    # five names: the partnership, no list
    five = pages(build(valuation(PEOPLE)))
    b5 = blob(five)
    check("five names: no name is printed; the copy speaks of the partnership",
          not any(n in b5 for n in PEOPLE)
          and f"A look at what the partnership was worth to {BRAND} in {days} days." in five[0]
          and "who engaged with the partnership." in b5
          and "people who engaged with the partnership through two windows" in b5, five[0])
    check("five names: the headline and the close are the partnership by its own name",
          short_title(five).startswith(f"The {PROJECT} partnership delivered $")
          and statement(five) == f"The {PROJECT} partnership moved brand engagement past a matched group.",
          (short_title(five), statement(five)))

    # a property: a league, by the partnership's own name
    prop = pages(build(valuation([PROPERTY], qtype="league"), category="SPORTS"))
    bp = blob(prop)
    check("a property: the headline and the close are the partnership by its own name",
          short_title(prop).startswith(f"The {PROJECT} partnership delivered $")
          and statement(prop) == f"The {PROJECT} partnership moved brand engagement past a matched group.",
          (short_title(prop), statement(prop)))
    check("a property: never 'Northline's partnership', never 'the value Northline brought'",
          f"{PROPERTY}'s partnership" not in bp and f"the value {PROPERTY} brought" not in bp
          and f"A look at what the partnership was worth to {BRAND} in {days} days." in prop[0], prop[0])
    check("a property: people still engage with it by name", f"who engaged with {PROPERTY}." in bp)
    # a property found by several search terms is still one property
    terms = pages(build(valuation([PROPERTY, "Northline tickets", "northlineleague", "northline.com/schedule"],
                                  qtype="league")))
    check("a property's search terms are never printed as a list of partners",
          "Northline tickets" not in blob(terms) and "northlineleague" not in blob(terms)
          and "northline.com" not in blob(terms) and f"who engaged with {PROPERTY}." in blob(terms))
    # a pairing too long for a twelve-word headline: the plain partnership, still never one name
    long_title = "Fernhill x Northline Summer Showcase"
    wide = pages(build(valuation(PEOPLE[:3], project=long_title)))
    check("a longer pairing that does not fit the headline falls back to 'The partnership', never to one name",
          statement(wide) == "The partnership moved brand engagement past a matched group."
          and short_title(wide).startswith(f"The {long_title} partnership delivered $")
          and not solo_credit(wide, PEOPLE[:3]), (short_title(wide), statement(wide)))
    noted = pages(build(valuation(PEOPLE[:3], project="Fernhill x Northline (Spring Cut)")))
    check("a title with a note in brackets is not said as the partnership's name",
          short_title(noted).startswith("The partnership delivered $") and "(Spring Cut) partnership" not in blob(noted),
          short_title(noted))
    # several names the valuation does not mark as people: no one of them is said alone
    unmarked = pages(build(valuation(PEOPLE[:2], qtype="other")))
    check("two names of an unmarked kind: neither is credited alone",
          not any(n in blob(unmarked) for n in PEOPLE[:2])
          and statement(unmarked) == f"The {PROJECT} partnership moved brand engagement past a matched group.",
          statement(unmarked))
    # one person found by a name and a handle is one person
    handle = pages(build(valuation(["Mara Vale", "maravale", "themaravale"])))
    check("a name and its handles are one person",
          statement(handle) == f"Mara Vale moved {BRAND} engagement past a matched group."
          and "maravale" not in blob(handle), statement(handle))

    # possessives
    s_name = pages(build(valuation(["Tess Rivers"])))
    check("a name ending in s takes an apostrophe and an s, once",
          short_title(s_name).startswith("Tess Rivers's partnership delivered $"), short_title(s_name))
    apos = pages(build(valuation(PEOPLE[:1], brand="Harlow's", project="Harlow's x Northline")))
    ba = blob(apos)
    check("a brand that ends in an apostrophe and an s never takes a second one",
          "Harlow's'" not in ba and "Harlow's's" not in ba and "of Harlow's engagers are women." in ba,
          [t for t in ba.splitlines() if "engagers are" in t][:2])
    joint = B._joint_poss(PEOPLE[:3])
    check("several names share one possessive, on the last name",
          joint == "Mara Vale, Otis Brandt and Ines Okafor's" and B._joint_poss(["Tess Rivers", "Joss Hale"])
          == "Tess Rivers and Joss Hale's" and B._poss("Lowe's") == "Lowe's" and B._poss("Tess Rivers") == "Tess Rivers's")

    # agreement, and fans in sports work
    held = 4107329
    who = {
        1: (f"Mara Vale's projected US audience is {held:,}.", f"Mara Vale has {held:,} projected US fans."),
        2: (f"Mara Vale and Otis Brandt have a combined projected US audience of {held:,}.",
            f"Mara Vale and Otis Brandt have {held:,} projected US fans between them."),
        3: (f"Mara Vale, Otis Brandt and Ines Okafor have a combined projected US audience of {held:,}.",
            f"Mara Vale, Otis Brandt and Ines Okafor have {held:,} projected US fans between them."),
        5: (f"The people in the partnership have a combined projected US audience of {held:,}.",
            f"The people in the partnership have {held:,} projected US fans between them."),
    }
    ok, seen = True, []
    for k, (plain, sport) in who.items():
        doc = valuation(PEOPLE[:k], held_audience=held)
        p_plain, p_sport = blob(pages(build(doc, "FASHION"))), blob(pages(build(doc, "SPORTS")))
        ok = ok and plain in p_plain and sport in p_sport and "audience" not in p_sport.lower()
        seen.append((k, plain in p_plain, sport in p_sport, "audience" not in p_sport.lower()))
    check("the partner's own following: one name takes 'has', several take 'have'; sports work says fans", ok, seen)

    every = [one, two, three, five, prop, unmarked]
    words = re.compile(r"\b(his|her|hers|she|he|him|the creator|households?|the talent)\b", re.I)
    hits = sorted({m.group(0).lower() for pg in every for m in words.finditer(blob(pg))})
    check("no pronoun, no 'the creator', no 'the talent', no households on any page", not hits, hits)
    blocked = [w for pg in every for w in B.reader_blocked(blob(pg))]
    check("no outside name, method word or test statistic on any page", not blocked, blocked[:5])


# ------------------------------------------------------------------ [2]
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
RANGE = re.compile(rf"{_MON} \d{{1,2}}(?:, \d{{4}})? to {_MON} \d{{1,2}}, \d{{4}}", re.I)
DAYS = re.compile(r"\b(\d[\d,]*) days?\b", re.I)


def window_report(doc, pg):
    """(day counts printed, date ranges printed, the ones allowed)."""
    text = blob(pg)
    pre = doc["pre_period"]
    days = {int(m.group(1).replace(",", "")) for m in DAYS.finditer(text)}
    ranges = {m.group(0).lower() for m in RANGE.finditer(text)}
    return days, ranges, pre


def test_windows():
    print("[2] windows: every date and day count is the window the after counts cover")
    for shape, w in WINDOWS.items():
        doc = valuation(PEOPLE[:1], shape=shape)
        a_start, a_end, a_days = w["after"]
        got = B._after_window(doc)
        check(f"{shape}: the after window is {a_start} to {a_end}, {a_days} days",
              (got["start"], got["end"], got["days"]) == (a_start, a_end, a_days), got)
        pg = pages(build(doc))
        days, ranges, pre = window_report(doc, pg)
        ok_days = {pre["days"], a_days}
        ok_ranges = {B._fmt_range(pre["start"], pre["end"]).lower(), B._fmt_range(a_start, a_end).lower(),
                     B._fmt_range(a_start, a_end, long=True).lower()}
        check(f"{shape}: every day count on every page is one of the two windows' ({sorted(ok_days)})",
              days and days <= ok_days, sorted(days))
        check(f"{shape}: every date range on every page is one of the two windows'",
              ranges and ranges <= ok_ranges, sorted(ranges - ok_ranges))
        check(f"{shape}: the cover prints the same window, in full",
              B._fmt_range(a_start, a_end, long=True) + "." in pg[0], pg[0])
        check(f"{shape}: the cover claim, the short version and the close carry its length",
              f"in {a_days} days." in " ".join(pg[0]) and f"The short version of {a_days} days, "
              f"{B._fmt_range(a_start, a_end)}." in page_named(pg, "The short version")
              and any(t.startswith(f"In {a_days} days the partnership") for t in close_page(pg)),
              (pg[0][2], close_page(pg)[:2]))
    # the season shape is the one that printed wrong: the tail never shows
    season = valuation(PEOPLE[:3], shape="season")
    text = blob(pages(build(season)))
    tail = WINDOWS["season"]["post"]
    check("the tail between the campaign's end and the day the valuation was made is never printed",
          "15 days" not in text and B._fmt_range(tail[0], tail[1]) not in text
          and "Jul 20" not in text and "Aug 3" not in text)
    check("counts read during the campaign are 'after launch', never 'after the campaign'",
          "AFTER LAUNCH" in text and "after the campaign" not in text.lower())
    check("a campaign that closed before the valuation was made reads as delivered, not 'so far'",
          "so far" not in text and "delivered $" in text)
    # the days after an airing are 'after the campaign', as they were
    aired = blob(pages(build(valuation(PEOPLE[:1], shape="after"))))
    check("counts read after the campaign's last day are still 'after the campaign'",
          "AFTER THE CAMPAIGN" in aired and "after launch" not in aired.lower())
    pull = blob(pages(build(valuation(PEOPLE[:1], shape="pull"))))
    check("the live pull's two windows are the same length, and the method page says so",
          "Both windows run 90 days, so before and after compare like for like." in pull)

    # no second window at all, and no dates at all
    bare = valuation(PEOPLE[:1], shape="season")
    for k in ("post_period", "event_period", "post_period_days", "attribution_window_days"):
        bare.pop(k, None)
    got = B._after_window(bare)
    check("bare campaign dates: the window is the campaign's, its length counted from the dates",
          (got["start"], got["end"], got["days"]) == ("2026-02-10", "2026-07-19", 160), got)
    undated = valuation(PEOPLE[:1], shape="season")
    for k in ("post_period", "event_period", "pre_period", "start_date", "end_date", "post_period_days",
              "pre_period_days", "attribution_window_days"):
        undated.pop(k, None)
    pg = pages(build(undated))
    check("no dates at all: the deck still builds, with no date and no day count invented",
          len(pg) >= 12 and not RANGE.search(blob(pg)) and not DAYS.search(blob(pg))
          and B.PAGE_FAILED_LINE not in blob(pg), [t for t in pg[1][:3]])
    # two windows of one length: the valuation the engine wrote counts the campaign's dates
    same = valuation(PEOPLE[:1], shape="after")
    same["event_period"] = {"start": "2026-02-08", "end": "2026-02-14", "days": 7}
    same["start_date"] = "2026-02-08"
    check("two windows of one length, no engine mark: the later window, as before",
          B._after_window(same)["start"] == "2026-02-15")
    same["diagnostics"] = {"run_key": "fernhill|mara-vale|2026-02-08"}
    check("two windows of one length on a valuation the engine wrote: the campaign's dates",
          B._after_window(same)["start"] == "2026-02-08")
    for junk in (None, {}, {"post_period": "x", "event_period": 7, "totals": []}, {"start_date": "soon"}):
        try:
            B._after_window(junk)
            ok = True
        except Exception as e:  # pragma: no cover
            ok = repr(e)
        check(f"a malformed valuation never raises ({str(junk)[:30]})", ok is True, ok)


# ------------------------------------------------------------------ [3]
def test_matched_group():
    print("[3] the matched group's gain reads from one field on every page")
    for lift in ("net", "whole"):
        doc = valuation(PEOPLE[:3], lift=lift)
        v, cg = doc["valuation"], doc["control_group"]
        pg = pages(build(doc))
        text = blob(pg)
        eng = page_named(pg, "Engagement before and after")
        blv = page_named(pg, "Brand lift")
        points = after(eng, "MATCHED GROUP") if "MATCHED GROUP" in eng else ""
        strip = [t for t in eng if re.fullmatch(r"[+-]\d+\.\d points", t)]
        check(f"{lift}: the engagement page prints the matched group's gain from the valuation ({cg['control_delta_pp']:+.1f} points)",
              f"{cg['control_delta_pp']:+.1f} points" in strip, strip)
        rate = money(after(blv, "x our 2026 value per new engaged person"))
        gain = doc["totals"]["post_users_projected"] - doc["totals"]["pre_users_projected"]
        if lift == "net":
            shown_gain = int(after(blv, f"More {BRAND} engagers among those reached").replace(",", ""))
            matched = int(after(blv, "Less the same gain in the matched group").replace(",", ""))
            new = int(after(blv, "New people beyond the matched group").replace(",", ""))
            check("net: the gain less the matched group's gain is the new people",
                  shown_gain == gain and shown_gain - matched == new == v["incremental_users"], (shown_gain, matched, new))
            check("net: the matched group's gain in people is its gain in points, the field the engagement page prints",
                  abs(matched / cg["projected_control_size"] * 100 - cg["control_delta_pp"]) < 0.05,
                  (matched, cg["control_delta_pp"]))
            check("net: the page is headed as the lift over the matched group",
                  any(t.startswith("The lift over the matched group is worth $") for t in blv))
            check("net: the short version says the matched group did not make this gain",
                  "It is the gain the matched group did not make." in text)
        else:
            new = int(after(blv, f"New {BRAND} engagers since the campaign began").replace(",", ""))
            check("whole: the page shows the count the valuation priced, the whole rise",
                  new == gain == v["incremental_users"], (new, gain))
            check("whole: no subtraction row, and no matched gain of 0",
                  "Less the same gain in the matched group" not in blv and "0" not in blv, blv)
            said = re.search(r"A matched group gained (\d+\.\d) points over the same days, against (\d+\.\d) points "
                             r"for the people reached\.", " ".join(blv))
            check("whole: the lift page states the matched group's gain in the points the engagement page prints",
                  bool(said) and f"+{said.group(1)} points" in strip
                  and said.group(2) == f"{cg['treat_delta_pp']:.1f}", (said.group(0) if said else blv, strip))
            check("whole: the page is headed by what it prices, not as a lift over the matched group",
                  not any("The lift over the matched group is worth" in t for t in blv)
                  and any(t.startswith(f"New {BRAND} engagers since the campaign began are worth $") for t in blv))
            check("whole: the short version does not call it the gain the matched group did not make",
                  "the gain the matched group did not make" not in text
                  and f"New engagement is worth ${v['brand_lift_value']:,.2f}." in text)
            check("whole: the method page says what brand lift value counts and what the matched group is for",
                  f"Brand lift value counts everyone who began engaging with {BRAND} after launch." in text
                  and "The lift over the matched group is the gain among the people reached minus that group's gain" in text)
        check(f"{lift}: brand lift is new people times rate, and is its tile",
              abs(new * rate - money(after(blv, "BRAND LIFT VALUE"))) < 0.005
              and abs(new * rate - v["brand_lift_value"]) < 0.005, (new, rate))
        moved = set(re.findall(r"matched group(?: of the same size)? moved from (\d+\.\d)% to (\d+\.\d)%", text))
        check(f"{lift}: the matched group's before and after rates are the same wherever they print",
              moved == {(f"{cg['control_pre_pen_pct']:.1f}", f"{cg['control_post_pen_pct']:.1f}")}, moved)
        lifted = re.search(r"gained (\d+\.\d) points more than a group like them", text)
        check(f"{lift}: the lift over the matched group is the valuation's, on the strip and in the reading",
              f"{cg['incremental_lift_pp']:+.1f} points" in strip and lifted
              and lifted.group(1) == f"{cg['incremental_lift_pp']:.1f}", strip)
        # no figure differs from the valuation's
        total = page_named(pg, "Total value")
        tiles = {lab: money(after(total, lab)) for lab in ("EARNED MEDIA", "BRAND ENGAGEMENT", "BRAND LIFT", "CONVERSION")}
        check(f"{lift}: every lens and the total print the valuation's own figures",
              abs(tiles["EARNED MEDIA"] - v["earned_media_value"]) < 0.005
              and abs(tiles["BRAND ENGAGEMENT"] - v["brand_engagement_value"]) < 0.005
              and abs(tiles["BRAND LIFT"] - v["brand_lift_value"]) < 0.005
              and abs(tiles["CONVERSION"] - v["conversion_value"]) < 0.005
              and f"${v['total_brand_value']:,.2f}" in pg[0] and abs(sum(tiles.values()) - v["total_brand_value"]) < 0.005,
              tiles)
        check(f"{lift}: the people reached, the engagers and the orders are the valuation's",
              f"{doc['projected_audience_size']:,}" in pg[0]
              and f"{doc['totals']['post_users_projected']:,} people" in eng
              and f"{doc['totals']['pre_users_projected']:,} people" in eng
              and f"{doc['conversions']['post_users_projected']:,}" in page_named(pg, "Sales"))
    # the count behind the lift is read once
    doc = valuation(PEOPLE[:1], lift="whole")
    with contextlib.redirect_stdout(io.StringIO()):
        ctx = B._derive_context(doc, None, "FASHION")
    c = B._lift_counts(ctx)
    check("the lift's counts come from one reading: whole rise, the valuation's matched gain in people",
          c["whole"] is True and c["base"] == c["gain"]
          and c["held"] == int(round(doc["projected_audience_size"] * doc["control_group"]["control_delta_pp"] / 100)), c)
    with contextlib.redirect_stdout(io.StringIO()):
        plain = B._derive_context(valuation(PEOPLE[:1], control=False), None, "FASHION")
    check("no matched group bought: never read as a whole-rise valuation with one", B._lift_counts(plain)["whole"] is False)
    no_ctl = blob(pages(build(valuation(PEOPLE[:3], control=False))))
    check("no matched group bought: no page speaks of one", "matched" not in no_ctl.lower())
    # a matched group of another size is never called the same size
    other = valuation(PEOPLE[:1])
    other["control_group"]["projected_control_size"] = 2903117
    who_when = page_named(pages(build(other)), "Who and when")
    check("a matched group of a different size is not captioned 'Same size'",
          "Same size, never reached" not in who_when and "Never reached by the campaign" in who_when, who_when)


# ------------------------------------------------------------------ [4]
# One person, an order's window, the lift net of the matched group: the
# sentences the builder wrote for this valuation on 2026-11-17, before
# the change. They must not move.
BEFORE = {
    "cover": ["A look at the value Mara Vale brought to Fernhill in 175 days."],
    "The short version": [
        "Mara Vale's partnership is worth $12,578,497.50 to Fernhill so far.",
        "The short version of 175 days, Feb 10 to Aug 3, 2026.",
        "The read follows 2,814,563 people in the US who engaged with Mara Vale. The share of them engaging with "
        "Fernhill went from 21.4% to 33.8%. A matched group moved from 21.1% to 23.1% over the same days.",
        "Brand lift is worth $1,479,280.00. It is the gain the matched group did not make.",
    ],
    "Who and when": [
        "We compared the people reached with a matched group.",
        "2,814,563 people in the US, 175 days before and 175 days after.",
        "Crosswalk sees the full digital life of 30M+ opted-in US consumers. For this read we followed people who "
        "engaged with Mara Vale through two windows. We counted what they did: the sites they visited, the apps "
        "they used, and the Fernhill pages and posts in their sessions.",
        "BEFORE LAUNCH", "AFTER LAUNCH", "Aug 19, 2025 to Feb 9, 2026", "Feb 10 to Aug 3, 2026",
        "Same size, never reached",
    ],
    "Engagement before and after": [
        "175 DAYS BEFORE LAUNCH", "175 DAYS AFTER LAUNCH", "+12.5 points", "+2.0 points", "+10.5 points",
    ],
    "Brand lift": [
        "The lift over the matched group is worth $1,479,280.00.",
        "Brand lift value, Feb 10 to Aug 3, 2026.",
        "More Fernhill engagers among those reached", "351,021",
        "Less the same gain in the matched group", "55,165",
        "New people beyond the matched group", "295,856",
        "Brand lift counts the gain in Fernhill engagers beyond what a matched group gained over the same days. "
        "Both groups hold 2,814,563 people. Before launch, 21.4% of the people reached engaged with Fernhill, "
        "against 21.1% of the matched group.",
    ],
    "What to do next": [
        "Mara Vale moved Fernhill engagement past a matched group.",
        "In 175 days the partnership added 295,856 Fernhill engagers and $12,578,497.50 in brand value.",
    ],
    "How it is counted": [
        "Before: Aug 19, 2025 to Feb 9, 2026. After: Feb 10 to Aug 3, 2026. Both windows run 175 days, so before "
        "and after compare like for like.",
        "Brand lift is the gain among the people reached minus the gain in a matched group over the same days. "
        "That group is the same size, started at about the same Fernhill engagement, and had no exposure to the "
        "campaign.",
    ],
}


def test_single_talent_as_before():
    print("[4] one person on an order's window reads as it did before")
    doc = valuation(PEOPLE[:1], shape="order", lift="net")
    pg = pages(build(doc))
    for eyebrow, lines in BEFORE.items():
        texts = pg[0] if eyebrow == "cover" else page_named(pg, eyebrow)
        missing = [ln for ln in lines if ln not in texts]
        check(f"{eyebrow}: {len(lines)} line(s) unchanged", not missing, missing[:2])


# ------------------------------------------------------------------ [5]
def test_finished_file():
    print("[5] the finished file: READY, headlines that fit, a clean layout")
    decks = {
        "one name": valuation(PEOPLE[:1]),
        "two names": valuation(PEOPLE[:2]),
        "three names": valuation(PEOPLE[:3], lift="whole"),
        "five names": valuation(PEOPLE),
        "a property": valuation([PROPERTY], qtype="league"),
    }
    for label, doc in decks.items():
        prs = build(doc, "SPORTS")
        heads = headlines(prs)
        long = [(i, t) for i, t in heads if len(t.split()) > 12 or not t.endswith(".") or ":" in t
                or len(re.findall(r"[.!?](?:\s|$)", re.sub(r"\d\.\d", "0", t))) != 1]
        tall = [(i, t) for i, t in heads if B._line_count(t, 28, B.BAND, bold=True) > 2]
        check(f"{label}: every headline is one sentence of twelve words or fewer", len(heads) >= 12 and not long, long[:3])
        check(f"{label}: every headline holds two lines at a title size", not tall, tall[:3])
        issues = B.check_layout(prs)
        check(f"{label}: the layout self-check finds nothing", not issues, issues[:3])
        check(f"{label}: no page failed to draw", B.PAGE_FAILED_LINE not in blob(pages(prs)))
    if B._design() is None:
        print("  skip  the design pack is not on this machine; the finisher did not run")
        return
    for label in ("three names", "a property"):
        with contextlib.redirect_stdout(io.StringIO()):
            body, report = B.build_deck_report(copy.deepcopy(decks[label]), category="SPORTS")
        steps = {name: ok for name, ok, _detail in getattr(report, "steps", [])}
        counts = getattr(report, "counts", {}) or {}
        check(f"{label}: the deck leaves the builder READY (fonts embedded, lint and plain-English clean)",
              bool(body) and getattr(report, "ready", False) is True and steps.get("embed") is True
              and counts.get("lint_fail", 1) == 0 and counts.get("readability_flags", 1) == 0,
              (getattr(report, "ready", None), steps, counts))


def main() -> int:
    test_names()
    test_windows()
    test_matched_group()
    test_single_talent_as_before()
    test_finished_file()
    print()
    if FAIL:
        print(f"FAIL: {FAIL} check(s)")
        return 1
    print("PASS: the valuation deck names everyone, dates the right window, and reads the matched group once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
