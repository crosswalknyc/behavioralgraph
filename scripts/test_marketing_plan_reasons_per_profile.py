#!/usr/bin/env python3
"""Profile IQ Marketing tab reasons per profile (2026-10-30, Jenna: "it seems
like it's the same on all. like reels is always 35%. it has to reason for
each profile").

Hermetic: five fixture profiles built in memory, the model call mocked, S3
faked. Checks:

  1. no two profiles share a channel mix, a paid-social platform split or a
     Meta ad-format vector;
  2. every vector sums to 100.0 and no share ends in a zero tenth;
  3. every channel rationale carries directional (Tier 2) language;
  4. no banned vocabulary (synth / modeled / panel / estimated / em dash /
     door / model names) anywhere the page shows;
  5. the plan is cached by profile ETag (second read never re-bills) and
     the pending flow lands the reasoned plan in the cache;
  6. a failing model call falls back to the deterministic plan;
  7. a model answer is adopted (reasoned=True) and stays within the clamp;
  8. templates/index.html renders from /api/marketing-plan and no longer
     carries the fixed Reels 35% table.

Run: python3 scripts/test_marketing_plan_reasons_per_profile.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from migration import marketing_plan as mp  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AGE_LABELS = ["17 AND UNDER", "18-24", "25-34", "35-44", "45-54", "55-64", "65 OR OLDER"]
INCOME_LABELS = ["LESS THAN $25,000", "$25,000 - $49,999", "$50,000 - $74,999", "$75,000 - $99,999",
                 "$100,000 - $149,999", "$150,000 - $199,999", "$200,000 OR MORE"]


def _rows(cat, items):
    """items: list of (name, bp, index)."""
    return [{"Column": cat, "Value": n, "Brand Penetration (Row)": bp, "Index vs Gen Pop": idx} for n, bp, idx in items]


def make_profile(subject, category, age, gender, income, parents, social, app, search, streaming, music,
                 podcasts, mpb, shops, creators, media, dmas, games, theaters, gyms, dine):
    recs = [
        {"Column": "BRAND INPUT", "Value": subject, "Brand Penetration (Row)": 100.0, "Original Raw Numbers": 384217, "US Gen Pop Projection": 12675321},
        {"Column": "SAMPLE SIZE", "Value": "SAMPLE SIZE (2025-10-30 TO 2026-10-30)", "Brand Penetration (Row)": 100.0},
        {"Column": "BRAND CATEGORY", "Value": category},
        {"Column": "SUBJECT", "Value": subject, "Brand Penetration (Row)": 100.0},
    ]
    recs += _rows("AGE", [(l, v, 100.0) for l, v in zip(AGE_LABELS, age)])
    recs += _rows("GENDER", [("MALE", gender[0], 100.0), ("FEMALE", gender[1], 100.0), ("NON-BINARY", 100 - gender[0] - gender[1], 100.0)])
    recs += _rows("INCOME", [(l, v, 100.0) for l, v in zip(INCOME_LABELS, income)])
    recs += _rows("PARENTAL_STATUS", [("HAS KIDS", parents, 100.0), ("DOESNT HAVE KIDS", 100 - parents, 100.0)])
    recs += _rows("SOCIAL MEDIA", social)
    recs += _rows("APP/PLATFORM USAGE", app)
    recs += _rows("SEARCH ENGINE/AI", search)
    recs += _rows("STREAMING/PLATFORM", streaming)
    recs += _rows("STREAMING/MUSIC", music)
    recs += _rows("PODCAST", podcasts)
    recs += _rows("MOST PURCHASED BRANDS", mpb)
    recs += _rows("WHERE THEY SHOP", shops)
    recs += _rows("CREATOR/INFLUENCER", creators)
    recs += _rows("MEDIA", media)
    recs += _rows("LOCATION", dmas)
    recs += _rows("GAMES", games)
    recs += _rows("MOVIE THEATER", theaters)
    recs += _rows("WORKOUT FACILITY", gyms)
    recs += _rows("WHERE THEY DINE", dine)
    return pd.DataFrame(recs).fillna("")


FIXTURES = {
    "Fizz Energy": make_profile(
        "Fizz Energy", "BEVERAGE",
        age=[4.1, 29.7, 31.2, 17.9, 9.8, 5.1, 2.2], gender=(57.9, 38.6), income=[14.2, 24.1, 21.3, 14.6, 14.1, 6.9, 4.8], parents=31.7,
        social=[("YOUTUBE", 92.1, 113), ("TIKTOK", 69.8, 158), ("INSTAGRAM", 66.2, 147), ("SNAPCHAT", 47.3, 161), ("X", 38.7, 129), ("FACEBOOK", 38.4, 79), ("PINTEREST", 28.1, 107), ("LINKEDIN", 27.6, 113), ("DISCORD", 27.9, 166), ("TWITCH", 24.3, 201)],
        app=[("REDDIT", 61.3, 141)],
        search=[("GOOGLE", 87.9, 99), ("CHAT GPT", 36.4, 135), ("BING", 12.1, 34)],
        streaming=[("NETFLIX", 56.7, 145), ("Disney+/Hulu", 55.6, 108), ("AMAZON PRIME VIDEO", 47.1, 127), ("HBO MAX", 21.2, 91), ("PARAMOUNT+", 19.7, 102), ("PEACOCK", 14.1, 87), ("TUBI", 9.3, 118)],
        music=[("SPOTIFY", 49.8, 159), ("APPLE MUSIC", 36.1, 299), ("PANDORA MUSIC", 13.3, 123), ("SIRIUSXM", 5.4, 61), ("IHEART", 4.3, 380)],
        podcasts=[("THE JOE ROGAN EXPERIENCE", 4.1, 66), ("THIS PAST WEEKEND", 3.4, 150)],
        mpb=[("COCA COLA", 39.7, 633), ("NIKE", 35.8, 156), ("ADIDAS", 30.1, 168), ("ALANI NU", 29.3, 857), ("CERAVE", 27.2, 221), ("GYMSHARK", 24.6, 651), ("CROCS", 22.1, 131), ("OLD NAVY", 21.4, 98), ("CONVERSE", 19.2, 139), ("UNDER ARMOUR", 17.9, 183), ("STANLEY", 16.1, 254), ("SKECHERS", 16.2, 99)],
        shops=[("AMAZON", 80.5, 102), ("WALMART", 72.4, 116), ("TARGET", 40.9, 138), ("TEMU", 32.7, 164), ("KROGER", 24.3, 141)],
        creators=[("MRBEAST", 91.2, 118), ("KAI CENAT", 62.3, 231), ("LARRY WHEELS", 41.7, 388), ("JOEY SWOLL", 38.2, 402)],
        media=[("BARSTOOL SPORTS", 41.2, 218), ("COMPLEX", 33.7, 240), ("BLEACHER REPORT", 44.1, 190), ("BUZZFEED", 30.2, 112)],
        dmas=[("New York NY", 6.9, 102), ("Los Angeles CA", 6.1, 108), ("Chicago IL", 3.4, 112), ("Dallas Ft Worth TX", 2.9, 118), ("Phoenix AZ", 2.3, 151), ("Houston TX", 2.2, 101)],
        games=[("CALL OF DUTY", 31.2, 241), ("FORTNITE", 28.4, 266), ("NBA 2K", 24.1, 301)],
        theaters=[("AMC THEATRES", 24.1, 128), ("REGAL", 15.2, 121)],
        gyms=[("PLANET FITNESS", 22.3, 171), ("LA FITNESS", 9.1, 162)],
        dine=[("CHIPOTLE", 44.1, 161), ("TACO BELL", 41.9, 139), ("WINGSTOP", 25.2, 228)],
    ),
    "June Carter Hall": make_profile(
        "June Carter Hall", "MUSICIAN/BAND",
        age=[5.2, 8.4, 12.3, 19.6, 21.7, 21.8, 11.0], gender=(39.7, 58.3), income=[16.1, 25.8, 20.2, 12.9, 13.6, 6.1, 5.3], parents=64.5,
        social=[("YOUTUBE", 80.4, 98), ("FACEBOOK", 65.9, 135), ("INSTAGRAM", 52.1, 115), ("PINTEREST", 35.8, 135), ("TIKTOK", 34.6, 78), ("LINKEDIN", 31.3, 128), ("SNAPCHAT", 24.1, 81), ("X", 20.2, 67), ("DISCORD", 16.3, 97), ("TWITCH", 6.5, 54)],
        app=[("REDDIT", 14.9, 27)],
        search=[("GOOGLE", 90.7, 102), ("CHAT GPT", 28.1, 104), ("BING", 24.1, 67)],
        streaming=[("NETFLIX", 76.6, 196), ("AMAZON PRIME VIDEO", 65.1, 175), ("Disney+/Hulu", 47.3, 92), ("HBO MAX", 26.2, 114), ("PEACOCK", 15.3, 95), ("PARAMOUNT+", 15.2, 79), ("TUBI", 12.4, 158)],
        music=[("SPOTIFY", 69.3, 222), ("APPLE MUSIC", 35.8, 295), ("IHEART", 17.5, 410), ("SIRIUSXM", 17.4, 195), ("PANDORA MUSIC", 11.9, 110)],
        podcasts=[("COUNTRY HEAT WEEKLY", 27.5, 480), ("BOBBYCAST", 23.9, 410), ("WHISKEY RIFF RAFF", 19.4, 390), ("THE JOE ROGAN EXPERIENCE", 13.0, 115)],
        mpb=[("NIKE", 27.2, 118), ("OLD NAVY", 26.4, 121), ("SKECHERS", 26.1, 161), ("BATH & BODY WORKS", 26.0, 132), ("CROCS", 21.9, 130), ("ADIDAS", 17.7, 99), ("GILLETTE", 16.1, 97), ("OLAY", 15.4, 150), ("NEUTROGENA", 15.0, 112), ("CARHARTT", 15.0, 140), ("NEW BALANCE", 14.1, 142), ("COLUMBIA", 13.5, 119)],
        shops=[("AMAZON", 80.1, 101), ("WALMART", 67.7, 109), ("TARGET", 60.5, 204), ("LOWES", 52.3, 322), ("COSTCO", 33.1, 173)],
        creators=[("AL ROKER", 58.1, 212), ("RYAN SEACREST", 55.2, 170), ("GORDON RAMSAY", 51.3, 150), ("MRBEAST", 61.0, 79)],
        media=[("PEOPLE", 38.2, 171), ("TASTE OF HOME", 29.1, 240), ("FOX NEWS", 36.4, 160), ("USA TODAY", 27.3, 121)],
        dmas=[("Dallas Ft Worth TX", 5.8, 231), ("Houston TX", 4.1, 190), ("Nashville TN", 3.9, 420), ("Atlanta GA", 3.7, 160), ("Oklahoma City OK", 2.8, 390), ("New York NY", 2.6, 39)],
        games=[("CANDY CRUSH", 21.2, 141), ("WORDLE", 15.1, 118)],
        theaters=[("AMC THEATRES", 16.4, 87), ("CINEMARK", 14.2, 131)],
        gyms=[("PLANET FITNESS", 11.3, 87)],
        dine=[("CRACKER BARREL", 38.2, 301), ("CHICK FIL A", 44.0, 150), ("OLIVE GARDEN", 31.1, 180)],
    ),
    "Maple Street": make_profile(
        "Maple Street", "SERIES - NBC",
        age=[4.6, 4.1, 7.8, 10.7, 16.2, 25.4, 31.2], gender=(34.3, 64.2), income=[18.2, 27.1, 19.3, 12.1, 12.4, 6.3, 4.6], parents=56.0,
        social=[("YOUTUBE", 83.1, 102), ("FACEBOOK", 74.3, 153), ("PINTEREST", 44.8, 169), ("INSTAGRAM", 40.3, 89), ("TIKTOK", 25.0, 56), ("LINKEDIN", 23.9, 97), ("SNAPCHAT", 13.9, 47), ("X", 11.8, 39), ("DISCORD", 9.5, 57), ("TWITCH", 6.2, 52)],
        app=[("REDDIT", 9.4, 17)],
        search=[("GOOGLE", 83.7, 94), ("BING", 28.6, 80), ("CHAT GPT", 19.4, 72)],
        streaming=[("PEACOCK", 100.0, 621), ("NETFLIX", 76.4, 195), ("AMAZON PRIME VIDEO", 64.3, 173), ("Disney+/Hulu", 36.5, 71), ("PARAMOUNT+", 14.9, 78), ("HBO MAX", 14.5, 63), ("TUBI", 13.8, 176)],
        music=[("APPLE MUSIC", 36.9, 305), ("SPOTIFY", 36.6, 117), ("SIRIUSXM", 17.5, 195), ("IHEART", 17.3, 400), ("PANDORA MUSIC", 11.3, 104)],
        podcasts=[],
        mpb=[("SKECHERS", 25.9, 160), ("OLD NAVY", 24.4, 112), ("BATH & BODY WORKS", 23.8, 121), ("NIKE", 22.9, 100), ("CROCS", 19.5, 116), ("TIDE", 18.8, 112), ("ADIDAS", 16.1, 90), ("CHARMIN", 15.8, 112), ("HOKA", 15.0, 115), ("NEUTROGENA", 15.0, 111), ("COCA COLA", 14.4, 229), ("CERAVE", 14.0, 113)],
        shops=[("AMAZON", 77.1, 98), ("WALMART", 68.0, 109), ("TARGET", 54.4, 184), ("LOWES", 48.4, 298), ("COSTCO", 36.3, 190)],
        creators=[("RYAN SEACREST", 46.2, 143), ("OPRAH", 44.1, 161), ("KELLY CLARKSON", 40.3, 210)],
        media=[("PEOPLE", 42.1, 188), ("AARP", 33.8, 260), ("USA TODAY", 30.2, 134), ("TODAY SHOW", 28.9, 171)],
        dmas=[("Dallas Ft Worth TX", 4.1, 163), ("Atlanta GA", 3.9, 169), ("Houston TX", 3.6, 167), ("Indianapolis IN", 2.9, 310), ("Tulsa OK", 2.1, 480), ("Springfield MO", 1.9, 510)],
        games=[("WORDLE", 19.3, 151), ("CANDY CRUSH", 17.2, 114)],
        theaters=[("CINEMARK", 10.2, 94), ("REGAL", 9.8, 78)],
        gyms=[("PLANET FITNESS", 8.1, 62)],
        dine=[("CRACKER BARREL", 41.2, 325), ("OLIVE GARDEN", 35.6, 206), ("CHICK FIL A", 39.1, 133)],
    ),
    "Bay City Hoops": make_profile(
        "Bay City Hoops", "NBA",
        age=[6.1, 17.3, 23.1, 21.6, 15.4, 10.7, 5.8], gender=(63.1, 35.5), income=[9.8, 17.1, 18.2, 14.9, 21.3, 10.4, 8.3], parents=42.8,
        social=[("YOUTUBE", 88.4, 108), ("INSTAGRAM", 50.4, 111), ("FACEBOOK", 53.1, 109), ("TIKTOK", 60.2, 135), ("SNAPCHAT", 37.1, 125), ("X", 38.8, 129), ("PINTEREST", 30.4, 115), ("LINKEDIN", 40.9, 166), ("DISCORD", 22.1, 132), ("TWITCH", 19.4, 161)],
        app=[("REDDIT", 64.9, 118)],
        search=[("GOOGLE", 91.1, 103), ("CHAT GPT", 41.2, 153), ("BING", 14.2, 40)],
        streaming=[("NETFLIX", 71.2, 182), ("AMAZON PRIME VIDEO", 60.1, 162), ("Disney+/Hulu", 52.3, 102), ("ESPN", 31.2, 379), ("HBO MAX", 29.1, 126), ("PEACOCK", 18.7, 116), ("YOUTUBE TV", 14.2, 210)],
        music=[("SPOTIFY", 58.2, 186), ("APPLE MUSIC", 33.1, 273), ("PANDORA MUSIC", 12.1, 112), ("SIRIUSXM", 14.8, 166), ("IHEART", 9.2, 220)],
        podcasts=[("THE BILL SIMMONS PODCAST", 14.2, 610), ("PARDON MY TAKE", 12.1, 420), ("THE DAILY", 6.2, 80)],
        mpb=[("NIKE", 41.2, 179), ("ADIDAS", 28.3, 158), ("COCA COLA", 24.1, 384), ("STARBUCKS", 36.2, 141), ("NEW BALANCE", 18.2, 183), ("UNDER ARMOUR", 17.1, 175), ("CROCS", 15.2, 90), ("OLD NAVY", 17.9, 82), ("LULULEMON", 16.4, 190), ("STANLEY", 12.1, 191), ("GATORADE", 22.4, 310), ("SKECHERS", 11.1, 68)],
        shops=[("AMAZON", 82.3, 104), ("WALMART", 58.1, 93), ("TARGET", 49.2, 166), ("COSTCO", 44.1, 231), ("BEST BUY", 27.3, 145)],
        creators=[("STEPHEN CURRY", 71.2, 920), ("MRBEAST", 66.1, 86), ("JJ REDICK", 31.2, 650), ("DRAYMOND GREEN", 42.1, 810)],
        media=[("ESPN", 61.2, 240), ("BLEACHER REPORT", 44.3, 191), ("THE ATHLETIC", 29.1, 330), ("SF CHRONICLE", 21.2, 790)],
        dmas=[("San Francisco Oakland San Jose CA", 18.2, 821), ("Los Angeles CA", 7.1, 126), ("New York NY", 5.2, 77), ("Sacramento CA", 4.1, 390), ("Seattle WA", 2.9, 180), ("Fresno CA", 2.4, 410)],
        games=[("NBA 2K", 38.2, 478), ("MADDEN", 27.1, 301), ("FORTNITE", 19.2, 180)],
        theaters=[("AMC THEATRES", 22.1, 118), ("CINEMARK", 12.1, 112)],
        gyms=[("24 HOUR FITNESS", 18.2, 290), ("PLANET FITNESS", 14.1, 108)],
        dine=[("IN N OUT BURGER", 41.2, 420), ("CHIPOTLE", 40.1, 146), ("STARBUCKS", 52.1, 131)],
    ),
    "Zed Plays": make_profile(
        "Zed Plays", "CREATOR/INFLUENCER",
        age=[15.7, 34.6, 28.4, 10.4, 5.9, 3.8, 1.2], gender=(61.6, 36.1), income=[22.1, 28.3, 19.2, 11.4, 10.3, 5.1, 3.6], parents=27.7,
        social=[("YOUTUBE", 95.0, 116), ("TWITCH", 81.4, 677), ("TIKTOK", 77.6, 174), ("INSTAGRAM", 64.7, 143), ("SNAPCHAT", 55.5, 187), ("FACEBOOK", 55.0, 113), ("LINKEDIN", 35.7, 145), ("DISCORD", 31.6, 189), ("X", 27.5, 91), ("PINTEREST", 27.0, 102)],
        app=[("REDDIT", 43.2, 79)],
        search=[("GOOGLE", 93.9, 106), ("CHAT GPT", 45.9, 170), ("BING", 9.2, 26)],
        streaming=[("NETFLIX", 75.0, 192), ("AMAZON PRIME VIDEO", 67.0, 180), ("Disney+/Hulu", 43.2, 84), ("HBO MAX", 28.4, 123), ("PEACOCK", 23.8, 148), ("KICK", 16.4, 739), ("PARAMOUNT+", 17.7, 92)],
        music=[("SPOTIFY", 80.4, 258), ("APPLE MUSIC", 37.9, 313), ("PANDORA MUSIC", 11.6, 107), ("SIRIUSXM", 8.2, 92), ("IHEART", 17.3, 400)],
        podcasts=[("THE JOE ROGAN EXPERIENCE", 31.2, 277), ("NO JUMPER", 19.8, 580), ("CALL HER DADDY", 12.4, 477)],
        mpb=[("JOURNEYS", 22.1, 410), ("OFF WHITE", 18.2, 520), ("BAPE", 15.1, 610), ("NIKE", 44.2, 192), ("ADIDAS", 33.1, 185), ("CROCS", 28.2, 167), ("WINGSTOP", 30.1, 272), ("SHEIN", 29.4, 230), ("STANLEY", 14.2, 224), ("OLD NAVY", 19.1, 87), ("SKECHERS", 12.1, 74), ("CERAVE", 21.3, 173)],
        shops=[("AMAZON", 84.2, 106), ("WALMART", 66.1, 106), ("SHEIN", 29.4, 230), ("TEMU", 31.2, 157), ("TARGET", 38.1, 129)],
        creators=[("KAI CENAT", 78.2, 290), ("ISHOWSPEED", 61.2, 410), ("MRBEAST", 88.1, 114), ("DRUSKI", 44.2, 380)],
        media=[("COMPLEX", 38.2, 272), ("BARSTOOL SPORTS", 29.1, 154), ("WORLDSTAR", 33.1, 610), ("BLEACHER REPORT", 36.2, 156)],
        dmas=[("New York NY", 7.8, 115), ("Los Angeles CA", 6.4, 113), ("Chicago IL", 3.9, 128), ("Atlanta GA", 3.8, 165), ("Houston TX", 2.9, 133), ("Miami FL", 2.6, 171)],
        games=[("FORTNITE", 48.2, 452), ("ROBLOX", 36.1, 390), ("NBA 2K", 33.2, 415)],
        theaters=[("AMC THEATRES", 26.1, 139), ("REGAL", 17.2, 137)],
        gyms=[("PLANET FITNESS", 26.1, 200), ("LA FITNESS", 10.2, 182)],
        dine=[("WINGSTOP", 30.1, 272), ("CHICK FIL A", 46.2, 157), ("MCDONALDS", 58.1, 121)],
    ),
}

BANNED = ["synth", "modeled", "modelled", "estimated", "predicted", "panel", "\u2014", "\u2013", "door", "claude",
          "anthropic", "hostmap", "pipeline", "jitter", "multiplier"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeS3:
    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.puts = 0

    def add_csv(self, key, df, etag):
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        self.objects[key] = buf.getvalue().encode("utf-8")
        self.etags[key] = etag

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey {Key}")
        return {"ETag": f'"{self.etags.get(Key, "x")}"'}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey {Key}")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.puts += 1
        return {}


def _tenths_ok(v):
    t = int(round(float(v) * 10))
    return t % 10 != 0


def _check_vector(vals, label):
    total = round(sum(vals), 6)
    assert abs(total - 100.0) < 1e-6, f"{label}: sums to {total}, not 100.0"
    for v in vals:
        assert _tenths_ok(v), f"{label}: {v} ends in a zero tenth"


def _plan_text(plan):
    out = []
    for ch in plan["channels"]:
        out += [ch["label"], ch["description"], ch["rationale"], ch["signal_line"]]
        for p in ch["platforms"]:
            out += [p["name"], p["copy"], p["signal_line"]]
            out += [f["name"] + " " + f["note"] for f in p["formats"]]
    out += [str(plan["audience"])]
    return "\n".join(out)


def _meta(plan):
    ch = next(c for c in plan["channels"] if c["key"] == "paidSocial")
    return next(p for p in ch["platforms"] if p["key"] == "meta")


def _vec(items, key):
    return tuple(round(float(x[key]), 1) for x in sorted(items, key=lambda x: x.get("name") or x.get("key") or x.get("label")))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_vectors_distinct_and_valid():
    plans = {n: mp.build_plan(df, s3_key=f"{n.replace(' ', '_')}_10_30_2026_09_00.csv", use_model=False) for n, df in FIXTURES.items()}
    mixes, splits, metas = {}, {}, {}
    for n, plan in plans.items():
        _check_vector([c["share"] for c in plan["channels"]], f"{n} channel mix")
        for ch in plan["channels"]:
            _check_vector([p["share"] for p in ch["platforms"]], f"{n} {ch['key']} platform split")
            for p in ch["platforms"]:
                if p["formats"]:
                    _check_vector([f["pct"] for f in p["formats"]], f"{n} {p['key']} formats")
                    assert p["copy"], f"{n} {p['key']} has no copy"
                    for f in p["formats"]:
                        assert f["note"], f"{n} {p['key']} {f['name']} has no note"
            assert mp.has_tier2_language(ch["rationale"]), f"{n} {ch['key']} rationale lacks directional language: {ch['rationale']}"
            assert ch["rationale"].strip(), f"{n} {ch['key']} rationale empty"
        mixes[n] = _vec(plan["channels"], "share")
        social = next(c for c in plan["channels"] if c["key"] == "paidSocial")
        splits[n] = _vec(social["platforms"], "share")
        metas[n] = _vec(_meta(plan)["formats"], "pct")
        text = _plan_text(plan).lower()
        for b in BANNED:
            assert b not in text, f"{n}: banned word {b!r} in plan text"
        # the pill thresholds both reach and index: a 25% reach platform at index 56 is not High
    for name, table in (("channel mix", mixes), ("paid social split", splits), ("Meta formats", metas)):
        vals = list(table.values())
        assert len(set(vals)) == len(vals), f"two profiles share the same {name}: {table}"
    # a media buyer's sanity: the young beverage leads with Reels, the 65+ sitcom with feed video
    fizz = {f["name"]: f["pct"] for f in _meta(plans["Fizz Energy"])["formats"]}
    maple = {f["name"]: f["pct"] for f in _meta(plans["Maple Street"])["formats"]}
    assert max(fizz, key=fizz.get).startswith("Reels"), fizz
    assert max(maple, key=maple.get).startswith("Feed video"), maple
    assert fizz["Reels (short vertical video, 9-15s)"] - maple["Reels (short vertical video, 9-15s)"] >= 10
    # TikTok pill: high for the creator audience, low for the 65+ sitcom
    def pill(plan, key):
        ch = next(c for c in plan["channels"] if c["key"] == "paidSocial")
        return next(p for p in ch["platforms"] if p["key"] == key)["priority"]["key"]
    assert pill(plans["Zed Plays"], "tiktok") == "high"
    assert pill(plans["Maple Street"], "tiktok") in ("skip", "consider")
    # no fixed Reels 35 anywhere
    for n, plan in plans.items():
        assert abs(next(f["pct"] for f in _meta(plan)["formats"] if f["name"].startswith("Reels")) - 35.0) > 0.05, n
    print(f"  ok  five profiles: distinct mixes / splits / Meta vectors, all sum to 100 with messy tenths")
    return plans


def test_messy_split():
    out = mp.messy_split({"a": 50, "b": 30, "c": 20}, "salt")
    _check_vector(out.values(), "messy_split")
    out2 = mp.messy_split({"a": 50, "b": 30, "c": 20}, "other salt")
    assert out != out2, "two salts should not 1dp-collide"
    out3 = mp.messy_split({"a": 1, "b": 0, "c": 0}, "s")
    _check_vector(out3.values(), "messy_split degenerate")
    print("  ok  messy_split sums to 100.0, no zero tenths, salt moves it")


def test_scrub():
    assert mp.scrub_copy("Leans modeled toward Reels") == ""
    assert mp.scrub_copy("Panel reach is high") == ""
    assert mp.scrub_copy("Opens the door to new viewers") == ""
    assert "\u2014" not in mp.scrub_copy("Reels lead \u2014 the audience is young")
    assert mp.scrub_copy("Leans Reels; AI search reach is 36.4%") != ""
    assert mp.has_tier2_language("Leans Reels first") and not mp.has_tier2_language("Reels are 35%")
    print("  ok  scrub drops banned vocabulary, keeps plain lines")


def test_cache_hit_and_fallback_and_adoption():
    mp._memo.clear()
    mp._memo_order.clear()
    s3 = FakeS3()
    key = "Fizz_Energy_10_30_2026_09_00.csv"
    s3.add_csv(key, FIXTURES["Fizz Energy"], etag="etag-one")
    calls = {"n": 0}

    def boom(system, user):
        calls["n"] += 1
        raise RuntimeError("model down")

    plan = mp.get_or_build_plan(key, s3_client=s3, bucket="b", wait_for_model=True, model_fn=boom)
    assert plan["reasoned"] is False and plan["cache"] == "miss" and calls["n"] == 1
    _check_vector([c["share"] for c in plan["channels"]], "fallback mix")
    assert mp.cache_key_for(key) in s3.objects, "plan not written to the S3 cache"
    plan2 = mp.get_or_build_plan(key, s3_client=s3, bucket="b", wait_for_model=True, model_fn=boom)
    assert plan2["cache"] == "hit" and calls["n"] == 1, "second read re-billed the model"
    # a changed file (new ETag) rebuilds; a successful model answer is adopted within the clamp
    s3.etags[key] = "etag-two"
    det = mp.build_plan(FIXTURES["Fizz Energy"], s3_key=key, use_model=False)
    det_meta = {f["name"]: f["pct"] for f in _meta(det)["formats"]}

    def answer(system, user):
        calls["n"] += 1
        req = json.loads(user.split("PROFILE DIGEST (JSON):\n", 1)[1].rsplit("\n\nReturn", 1)[0])
        out = {"channel_rationale": {k: f"Leans into {v} because 61.7% of this audience is 18 to 34 and TikTok indexes 158." for k, v in req["channel_labels"].items()},
               "platform_copy": {k: "Seed with Coca-Cola, Alani Nu and Gymshark; weight New York and Los Angeles; Reels first for the 25-34 core." for k in req["requested_platform_keys"]},
               "format_weights": {}}
        for k, fw in req["current_format_weights"].items():
            names = list(fw)
            out["format_weights"][k] = {nm: (60.0 if i == 0 else 40.0 / max(1, len(names) - 1)) for i, nm in enumerate(names)}
        return json.dumps(out)

    plan3 = mp.get_or_build_plan(key, s3_client=s3, bucket="b", wait_for_model=True, model_fn=answer)
    assert plan3["cache"] == "miss" and plan3["reasoned"] is True and calls["n"] == 2
    meta3 = {f["name"]: f["pct"] for f in _meta(plan3)["formats"]}
    _check_vector(meta3.values(), "adopted Meta formats")
    assert meta3 != det_meta, "model answer was not blended in"
    for nm, v in meta3.items():
        dv = det_meta[nm]
        assert 0.5 * dv <= v <= 1.6 * dv + 0.2, f"{nm}: {v} outside the clamp around {dv}"
    for ch in plan3["channels"]:
        assert ch["rationale"].startswith("Leans into"), ch["rationale"]
        assert "\u2014" not in ch["rationale"]
    # pending flow: the first read returns at once, the background job lands the reasoned plan
    mp._memo.clear()
    mp._memo_order.clear()
    s3.objects.pop(mp.cache_key_for(key), None)
    s3.etags[key] = "etag-three"
    pend = mp.get_or_build_plan(key, s3_client=s3, bucket="b", wait_for_model=False, model_fn=answer)
    assert pend["pending"] is True and pend["reasoned"] is False
    deadline = time.time() + 10
    landed = None
    while time.time() < deadline:
        landed = mp.get_or_build_plan(key, s3_client=s3, bucket="b", wait_for_model=False, model_fn=answer)
        if landed.get("cache") == "hit":
            break
        time.sleep(0.2)
    assert landed and landed["cache"] == "hit" and landed["reasoned"] is True and landed["pending"] is False, landed and landed.get("cache")
    print("  ok  ETag cache hit on the second read, deterministic fallback on model failure, model answer adopted within the clamp, pending flow lands")


def test_page_renders_from_plan():
    html = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
    assert "/api/marketing-plan/" in html, "page does not fetch the per-profile plan"
    assert "_mkLoadPlan" in html and "_mkRenderMediaMix" in html
    assert "Reels (short vertical video, 9-15s)', pct: 35" not in html, "fixed Reels 35% default still in the page"
    assert "PLATFORM_AD_FORMATS = {" not in html and "const CHANNEL_META = {" not in html
    assert "% panel reach" not in html, "the tab still says panel reach"
    assert "panel-observed digital behavior" not in html
    app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "/api/marketing-plan/<path:s3_key>" in app_src
    print("  ok  page renders from /api/marketing-plan; no fixed Reels 35% table; no panel wording on the tab")


def test_module_text_clean():
    src = open(mp.__file__, encoding="utf-8").read()
    assert "\u2014" not in src, "em dash in marketing_plan.py"
    print("  ok  module carries no em dashes")


if __name__ == "__main__":
    print("test_marketing_plan_reasons_per_profile")
    test_messy_split()
    test_scrub()
    test_vectors_distinct_and_valid()
    test_cache_hit_and_fallback_and_adoption()
    test_page_renders_from_plan()
    test_module_text_clean()
    print("ALL OK")
