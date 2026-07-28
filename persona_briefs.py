"""Persona brief builder for per-category agent prompts.

Every category-agent prompt (TALENT, GAMES, SOCIAL MEDIA, STREAMING/
PLATFORM, MPB, etc.) should include a structured persona brief that
tells the model:

  1. ELEVATE: which brand/talent clusters this specific audience over-
     indexes on, with panel-realistic BP bands (e.g. 30-60%, 15-25%).
  2. ATTENUATE: which clusters this audience under-indexes on, with
     bands (e.g. 0.3-2%, 2-5%).
  3. PANEL-ANCHOR: numeric anchoring for the most common brands in
     the category so the model calibrates rather than guessing.

Without this, the model defaults to "well-known-name ranking" instead
of "who-in-this-audience-actually-engaged". That default failure mode
produced:

  - Macy's TALENT skewing Gen Z (55-year-old female shopper)
  - WoF GAMES compressing Solitaire / Sudoku / NYT Games
  - Reba peer set featuring Kendrick Lamar
  - Nimrods audience skewing 18-24 (Green Day comedy)

Usage
-----

    from migration.persona_briefs import build_category_persona_brief

    brief = build_category_persona_brief(
        subject="Wheel of Fortune",
        category="GAMES",
        df_source=df,
    )
    # append `brief` to your category-agent user prompt

Design
------

Two-layer table:
  * ARCHETYPES: audience shape (older_female_mainstream, gen_z, etc.)
  * CATEGORY_ARCHETYPE_BRIEFS: (category, archetype) -> ELEVATE +
    ATTENUATE + panel anchors.

Archetype is derived from the source df's AGE + GENDER + ETHNICITY
composition. If no strong archetype is detectable, falls back to
`broad_mainstream` which contains general panel anchoring for major
mainstream brands.

Extending
---------

Adding a new archetype or (category, archetype) pair does not require
touching any agent code. The brief builder pulls from tables and
returns a string. The agent prompts already interpolate the brief.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd


# =============================================================================
# 1) Derive audience archetype from demographic distribution
# =============================================================================

CAT_COL = "Column"
VAL_COL = "Value"
BP_COL = "Brand Penetration (Row)"


def _bp(x):
    try:
        return float(str(x).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _cat_rows(df: pd.DataFrame, cat_name: str) -> pd.DataFrame:
    return df[df[CAT_COL].astype(str).str.upper().str.strip()
              == cat_name.upper()]


def _pct(df: pd.DataFrame, cat: str, value_predicate) -> float:
    total = 0.0
    matched = 0.0
    for _, r in _cat_rows(df, cat).iterrows():
        v = _bp(r[BP_COL])
        if v is None:
            continue
        total += v
        if value_predicate(str(r[VAL_COL]).strip().upper()):
            matched += v
    if total <= 0:
        return 0.0
    return matched * 100.0 / total


def _age_55_plus_pct(df: pd.DataFrame) -> float:
    return _pct(
        df, "AGE",
        lambda v: any(tag in v for tag in ("55", "65", "45-54", "55-64", "65+"))
                  and "45-54" not in v or "55" in v or "65" in v,
    )


def _age_bucket_pct(df: pd.DataFrame, low: int, high: Optional[int]) -> float:
    """Sum BP of AGE buckets whose numeric mid falls within [low, high]."""
    total = 0.0
    matched = 0.0
    for _, r in _cat_rows(df, "AGE").iterrows():
        v = _bp(r[BP_COL])
        if v is None:
            continue
        total += v
        lbl = str(r[VAL_COL]).strip()
        m = re.search(r"(\d{1,2})\s*[-\u2013\u2014to]+\s*(\d{1,2})", lbl)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            mid = (lo + hi) / 2.0
        else:
            m2 = re.search(r"(\d{2,3})\+", lbl)
            if m2:
                mid = int(m2.group(1)) + 5
            else:
                m3 = re.search(r"(?:UNDER|<)\s*(\d{2})", lbl.upper())
                if m3:
                    mid = int(m3.group(1)) - 2
                else:
                    continue
        if mid >= low and (high is None or mid <= high):
            matched += v
    if total <= 0:
        return 0.0
    return matched * 100.0 / total


def _female_pct(df: pd.DataFrame) -> float:
    return _pct(df, "GENDER", lambda v: v == "FEMALE" or v == "F")


def _ethnicity_pct(df: pd.DataFrame, tag: str) -> float:
    return _pct(df, "ETHNICITY",
                 lambda v: tag.upper() in v)


def derive_audience_archetype(df: pd.DataFrame) -> str:
    """Return an archetype string based on the source df's demos. Returns
    'broad_mainstream' if no strong tilt is detected."""
    if df is None or len(df) == 0:
        return "broad_mainstream"
    a55p = _age_bucket_pct(df, 55, None)
    a45_54 = _age_bucket_pct(df, 45, 54)
    a35_44 = _age_bucket_pct(df, 35, 44)
    a25_34 = _age_bucket_pct(df, 25, 34)
    a18_24 = _age_bucket_pct(df, 18, 24)
    fem = _female_pct(df)

    older = a55p + a45_54  # 45+
    gen_z = a18_24
    millennial = a25_34
    gen_x = a35_44 + a45_54

    if older >= 55 and fem >= 55:
        return "older_female_mainstream"
    if older >= 55 and fem <= 45:
        return "older_male_mainstream"
    if older >= 55:
        return "older_mixed_mainstream"
    if gen_z >= 30 and fem >= 55:
        return "gen_z_female"
    if gen_z >= 30 and fem <= 40:
        return "gen_z_male_gaming"
    if gen_z + millennial >= 60 and fem >= 55:
        return "young_female_urban"
    if gen_z + millennial >= 60 and fem <= 40:
        return "young_male_urban"
    if gen_z + millennial >= 55:
        return "young_adult_mixed"
    if 35 <= (gen_x) <= 100 and 45 <= fem <= 55:
        return "middle_age_family"
    return "broad_mainstream"


# =============================================================================
# 2) Category brief tables
# =============================================================================
#
# Each key: (category, archetype). Value: dict with elevate / attenuate /
# panel_anchor / notes.
#
# ELEVATE / ATTENUATE clusters format:
#   [(cluster_name, band_str, [example_brand_names_or_talents])]
#
# The band is a panel-realistic BP range for this specific archetype.

_TALENT_OLDER_FEMALE_MAINSTREAM = {
    "elevate": [
        ("Broadcast daytime + evening TV mainstays",
         "45-70%",
         ["Vanna White", "Pat Sajak", "Ryan Seacrest", "Kelly Clarkson",
          "Oprah Winfrey", "Ellen DeGeneres", "Whoopi Goldberg",
          "Reba McEntire", "Dolly Parton"]),
        ("Country / mainstream music stalwarts",
         "20-50%",
         ["Garth Brooks", "Reba McEntire", "Dolly Parton", "Blake Shelton",
          "Carrie Underwood", "George Strait", "Alan Jackson",
          "Kenny Chesney", "Tim McGraw", "Faith Hill", "Vince Gill"]),
        ("Classic actors + prestige character actors 50+",
         "18-40%",
         ["Tom Hanks", "Meryl Streep", "Denzel Washington", "Julia Roberts",
          "Sandra Bullock", "Harrison Ford", "Diane Keaton",
          "Morgan Freeman", "Robert De Niro", "Jane Fonda"]),
        ("Mainstream broadcast personalities + journalists",
         "15-35%",
         ["Hoda Kotb", "Savannah Guthrie", "Robin Roberts", "Gayle King",
          "Norah O'Donnell", "Anderson Cooper", "Lester Holt",
          "David Muir"]),
    ],
    "attenuate": [
        ("Gen Z internet creators / TikTokers",
         "0.2-2%",
         ["Karl Jacobs", "Charli D'Amelio", "Addison Rae", "Bella Poarch",
          "Khaby Lame", "Emma Chamberlain", "Chloe Forero",
          "Ur Mom Ashley", "Father Steve", "Kelly Wakasa"]),
        ("Rap / hip-hop young-audience anchors",
         "0.3-3%",
         ["Kendrick Lamar", "Drake", "Travis Scott", "Lil Nas X",
          "Doja Cat", "Playboi Carti", "Ice Spice", "Sexyy Red",
          "GloRilla", "Latto"]),
        ("Young-male athlete-influencer culture",
         "0.5-4%",
         ["Bronny James", "Cooper Flagg", "Livvy Dunne", "Angel Reese",
          "Caitlin Clark", "Sha'Carri Richardson"]),
        ("Gen Z pop stars",
         "0.5-5%",
         ["Olivia Rodrigo", "Sabrina Carpenter", "Tate McRae",
          "Chappell Roan", "Gracie Abrams", "Renee Rapp",
          "The Kid LAROI"]),
    ],
    "panel_anchor": (
        "The core is the mainstream broadcast + country + prestige-actor "
        "block. Wheel of Fortune / QVC / HGTV / Hallmark viewership overlaps "
        "heavily. Gen Z internet culture is a very thin sliver: think 0.5% "
        "reach at most for TikTok-native talents this audience wouldn't "
        "recognize by name. Vanna White / Pat Sajak / Kelly Clarkson type "
        "reach 40-70%. Reba/Dolly type 30-50%. Kendrick Lamar type < 3%."
    ),
}

_TALENT_YOUNG_MALE_GAMING = {
    "elevate": [
        ("Rap / hip-hop young-male anchors",
         "35-70%",
         ["Drake", "Kendrick Lamar", "Travis Scott", "Kanye West",
          "Playboi Carti", "Future", "21 Savage", "Metro Boomin",
          "Lil Baby", "J. Cole"]),
        ("Streamer / esports personalities",
         "25-60%",
         ["Ninja", "Kai Cenat", "IShowSpeed", "Adin Ross", "xQc",
          "Dr Disrespect", "Pokimane", "TimTheTatman", "Nickmercs",
          "Tfue"]),
        ("Athlete-influencer young male",
         "20-50%",
         ["LeBron James", "Kevin Durant", "Stephen Curry",
          "Giannis Antetokounmpo", "Nikola Jokic", "Cooper Flagg",
          "Victor Wembanyama", "Bronny James"]),
        ("Comedy / podcast bros",
         "20-45%",
         ["Joe Rogan", "Theo Von", "Bobby Lee", "Andrew Schulz",
          "Shane Gillis", "Tom Segura", "Tim Dillon"]),
    ],
    "attenuate": [
        ("Country / broadcast mainstream older",
         "0.5-4%",
         ["Reba McEntire", "Dolly Parton", "Vanna White", "Pat Sajak",
          "Hoda Kotb", "Kelly Clarkson"]),
        ("Prestige female / older female pop",
         "1-5%",
         ["Meryl Streep", "Julia Roberts", "Sandra Bullock",
          "Jane Fonda", "Diane Keaton"]),
        ("K-pop / young-female-anchor pop",
         "1-6%",
         ["BLACKPINK", "BTS", "NewJeans", "Stray Kids", "TWICE",
          "aespa", "Sabrina Carpenter"]),
    ],
    "panel_anchor": (
        "Audience is 18-34 male-dominant gaming / rap / streamer culture. "
        "Rap superstars and streamers should read 30-60%+. Country / "
        "broadcast-daytime figures rarely clear 5%. K-pop female-anchor "
        "acts are 1-6% here (they'd read 30%+ on a K-pop girl-group "
        "audience but this is not it)."
    ),
}

_GAMES_OLDER_FEMALE_MAINSTREAM = {
    "elevate": [
        ("Word / puzzle mainstream",
         "30-60%",
         ["Wordle", "Words With Friends", "Wordscapes", "NY Times Games",
          "Sudoku", "Solitaire", "Boggle With Friends", "Bejeweled",
          "Angry Birds", "Chess.com"]),
        ("Casino / slots daily habit",
         "8-15%",
         ["DoubleDown Casino", "Wizard of Oz Slots", "Jackpot Party Casino",
          "Heart of Vegas", "Big Fish Casino", "Slotomania"]),
        ("Casual mobile match-3 / kingdom / farm",
         "10-30%",
         ["Candy Crush Saga", "Gardenscapes", "Homescapes", "Toy Blast",
          "Cooking Fever", "FarmVille", "Fishdom", "Township",
          "Royal Match", "Monopoly Go!"]),
        ("Family / board-game IP mainstream",
         "5-25%",
         ["Scrabble Go", "Boggle", "Monopoly", "Uno"]),
    ],
    "attenuate": [
        ("FPS / battle-royale / esports young-male gaming",
         "0.3-2%",
         ["Call of Duty", "Fortnite", "Apex Legends", "Valorant",
          "Overwatch", "Rainbow Six", "PUBG", "Warzone",
          "League of Legends", "DOTA 2", "Counter-Strike", "Riot Games"]),
        ("Streaming-platform gaming",
         "2-6%",
         ["Steam", "Epic Games", "Twitch", "Discord (as gaming)"]),
        ("Action-adventure / soulslike / open-world",
         "0.5-3%",
         ["Elden Ring", "Dark Souls", "God of War", "Grand Theft Auto",
          "Cyberpunk 2077"]),
        ("Extreme-sports young-male gaming",
         "0.5-3%",
         ["Tony Hawk"]),
    ],
    "panel_anchor": (
        "Word/puzzle mainstream at 30-60%. Casino/slots daily habit at "
        "8-15%. Casual match-3 at 10-30%. FPS/esports at 0.3-2% (floor). "
        "Family IP games (Mario/Zelda/Minecraft) modest 2-8% (grandparent-"
        "purchase driven, not native play)."
    ),
}

_GAMES_YOUNG_MALE_GAMING = {
    "elevate": [
        ("FPS / battle-royale / esports",
         "35-75%",
         ["Fortnite", "Call of Duty", "Apex Legends", "Valorant",
          "Overwatch", "Rainbow Six", "PUBG", "Warzone"]),
        ("MOBA / esports",
         "25-55%",
         ["League of Legends", "DOTA 2", "Counter-Strike", "Rocket League",
          "Teamfight Tactics"]),
        ("Streaming-platform gaming",
         "45-80%",
         ["Steam", "Epic Games", "Twitch", "Discord"]),
        ("Action-adventure / open-world",
         "25-60%",
         ["Grand Theft Auto", "Elden Ring", "Zelda", "Skyrim",
          "Cyberpunk 2077", "God of War"]),
    ],
    "attenuate": [
        ("Word / puzzle mainstream",
         "3-10%",
         ["Wordle", "Words With Friends", "NY Times Games", "Solitaire",
          "Sudoku", "Wordscapes"]),
        ("Casino / slots daily habit older-female",
         "1-4%",
         ["DoubleDown Casino", "Wizard of Oz Slots", "Jackpot Party Casino",
          "Heart of Vegas"]),
        ("Casual match-3 older-female",
         "3-10%",
         ["Candy Crush Saga", "Gardenscapes", "Homescapes",
          "Royal Match", "Fishdom"]),
    ],
    "panel_anchor": (
        "FPS / battle-royale at 35-75% (top of the ranking). Steam / Epic "
        "at 45-80%. MOBA / esports at 25-55%. Word/puzzle at 3-10% floor "
        "(dad plays Wordle sometimes). Casino / match-3 at 1-4% floor."
    ),
}

_SOCIAL_MEDIA_OLDER_FEMALE_MAINSTREAM = {
    "elevate": [
        ("Facebook + Pinterest + YouTube core",
         "55-85%",
         ["Facebook", "Pinterest", "YouTube"]),
        ("Older-female adjacent",
         "10-30%",
         ["Nextdoor", "Poshmark"]),
    ],
    "attenuate": [
        ("Young-audience anchored",
         "5-15%",
         ["TikTok", "Snapchat", "Discord", "Twitch", "BeReal", "Threads"]),
        ("Male-tech-early-adopter",
         "3-10%",
         ["Reddit", "Twitter/X", "Mastodon", "Bluesky"]),
    ],
    "panel_anchor": (
        "Facebook 65-85% (core social platform for this cohort). "
        "Pinterest 45-70%. YouTube 55-75% (used more for how-to / recipe / "
        "hymns than for creator subscriptions). TikTok 8-18% max. Reddit "
        "3-8% max. Threads / BeReal / Mastodon at floor 1-4%."
    ),
}

_STREAMING_PLATFORM_OLDER_FEMALE_MAINSTREAM = {
    "elevate": [
        ("Broad household streaming",
         "40-70%",
         ["Netflix", "Amazon Prime Video", "Hulu"]),
        ("Broadcast-adjacent",
         "20-45%",
         ["Peacock", "Paramount+", "MAX"]),
        ("Family / Disney household",
         "20-45%",
         ["Disney+"]),
    ],
    "attenuate": [
        ("Niche / arthouse",
         "1-6%",
         ["Criterion Channel", "MUBI", "Shudder", "The Criterion Channel",
          "Kanopy", "Ovid.tv"]),
        ("Young-audience-native",
         "3-12%",
         ["Twitch", "Kick"]),
    ],
    "panel_anchor": (
        "Netflix 50-70%. Prime 40-60%. Hulu 35-55%. MAX 20-40%. Disney+ "
        "25-45%. Paramount+ 20-35% (elevated by CBS crossover). Peacock "
        "18-32%. Criterion / MUBI 1-4%. Twitch 3-8%."
    ),
}

_SEARCH_ENGINE_AI_OLDER_FEMALE_MAINSTREAM = {
    "elevate": [
        ("Mainstream search leaders",
         "60-92%",
         ["Google", "Bing"]),
        ("Voice / device assistant",
         "20-50%",
         ["Alexa", "Google Assistant", "Siri"]),
    ],
    "attenuate": [
        ("AI-native tools",
         "3-14%",
         ["ChatGPT", "Gemini", "Copilot", "Perplexity", "Claude", "Grok"]),
    ],
    "panel_anchor": (
        "Google 80-92% (near-universal search on-device / on the browser). "
        "Bing 12-25%. ChatGPT 3-10% (early adoption). Gemini 2-8%. "
        "Copilot / Perplexity / Claude at floor 1-5%."
    ),
}


# The lookup dict. Keys are (CATEGORY_UPPER, archetype).
_BRIEFS = {
    ("TALENT", "older_female_mainstream"):
        _TALENT_OLDER_FEMALE_MAINSTREAM,
    ("ACTOR", "older_female_mainstream"):
        _TALENT_OLDER_FEMALE_MAINSTREAM,
    ("MUSICIAN/BAND", "older_female_mainstream"):
        _TALENT_OLDER_FEMALE_MAINSTREAM,
    ("HOST/PERSONALITY", "older_female_mainstream"):
        _TALENT_OLDER_FEMALE_MAINSTREAM,

    ("TALENT", "young_male_urban"):
        _TALENT_YOUNG_MALE_GAMING,
    ("ACTOR", "young_male_urban"):
        _TALENT_YOUNG_MALE_GAMING,
    ("MUSICIAN/BAND", "young_male_urban"):
        _TALENT_YOUNG_MALE_GAMING,
    ("TALENT", "gen_z_male_gaming"):
        _TALENT_YOUNG_MALE_GAMING,

    ("GAMES", "older_female_mainstream"):
        _GAMES_OLDER_FEMALE_MAINSTREAM,
    ("GAMES", "older_mixed_mainstream"):
        _GAMES_OLDER_FEMALE_MAINSTREAM,
    ("GAMES", "gen_z_male_gaming"):
        _GAMES_YOUNG_MALE_GAMING,
    ("GAMES", "young_male_urban"):
        _GAMES_YOUNG_MALE_GAMING,

    ("SOCIAL MEDIA", "older_female_mainstream"):
        _SOCIAL_MEDIA_OLDER_FEMALE_MAINSTREAM,
    ("STREAMING/PLATFORM", "older_female_mainstream"):
        _STREAMING_PLATFORM_OLDER_FEMALE_MAINSTREAM,
    ("STREAMING VIDEO", "older_female_mainstream"):
        _STREAMING_PLATFORM_OLDER_FEMALE_MAINSTREAM,
    ("SEARCH ENGINE/AI", "older_female_mainstream"):
        _SEARCH_ENGINE_AI_OLDER_FEMALE_MAINSTREAM,
}


# =============================================================================
# 3) Brief builder
# =============================================================================

def _fmt_cluster_block(clusters: list, verb: str) -> str:
    if not clusters:
        return ""
    L = [f"{verb} clusters (should read at the indicated BP band):"]
    for name, band, examples in clusters:
        ex = ", ".join(examples[:8])
        if len(examples) > 8:
            ex += ", ..."
        L.append(f"  * {name}  [{band}]:")
        L.append(f"      {ex}")
    return "\n".join(L)


def build_category_persona_brief(
    subject: str,
    category: str,
    df_source: Optional[pd.DataFrame] = None,
    *,
    archetype: Optional[str] = None,
    include_header: bool = True,
) -> str:
    """Return a persona-brief string suitable for appending to a category-
    agent user prompt.

    Returns '' when the (category, archetype) combination has no explicit
    brief (the agent falls back to the default prompt with no extra
    guidance).

    Args:
      subject: the subject the profile is for
      category: category name (upper-case preferred; case-insensitive)
      df_source: source df to derive archetype from. Ignored if
        `archetype` is passed explicitly.
      archetype: optional explicit archetype override
      include_header: if True, prepends a "PERSONA BRIEF" header
    """
    cat_upper = str(category).strip().upper()
    if archetype is None:
        archetype = derive_audience_archetype(df_source) if df_source \
            is not None else "broad_mainstream"

    brief = _BRIEFS.get((cat_upper, archetype))
    # Fallback: try broad archetype family
    if brief is None:
        family_fallbacks = {
            "older_female_mainstream": ["older_mixed_mainstream"],
            "older_male_mainstream": ["older_mixed_mainstream"],
            "gen_z_female": ["young_adult_mixed"],
            "gen_z_male_gaming": ["young_male_urban", "young_adult_mixed"],
            "young_female_urban": ["young_adult_mixed"],
            "young_male_urban": ["young_adult_mixed"],
            "middle_age_family": ["broad_mainstream"],
        }
        for alt in family_fallbacks.get(archetype, []):
            brief = _BRIEFS.get((cat_upper, alt))
            if brief:
                break
    if brief is None:
        return ""

    L = []
    if include_header:
        L.append(f"PERSONA BRIEF FOR {subject.upper()} - {cat_upper} - "
                 f"[audience archetype: {archetype}]")
        L.append("")
    ele = _fmt_cluster_block(brief.get("elevate", []), "ELEVATE")
    att = _fmt_cluster_block(brief.get("attenuate", []), "ATTENUATE")
    if ele:
        L.append(ele)
        L.append("")
    if att:
        L.append(att)
        L.append("")
    anchor = brief.get("panel_anchor")
    if anchor:
        L.append(f"PANEL ANCHOR: {anchor}")
    notes = brief.get("notes")
    if notes:
        L.append(f"NOTES: {notes}")
    return "\n".join(L).rstrip()


__all__ = [
    "derive_audience_archetype",
    "build_category_persona_brief",
]
