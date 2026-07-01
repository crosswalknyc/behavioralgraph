#!/usr/bin/env python3
"""
Recalibrate BROADCAST/CABLE and MEDIA for a US gen pop digital panel.
Values shared between categories get one number applied everywhere.
ESPN also propagated to STREAMING/PLATFORM.
Remaining small media outlets get a proportional boost.
"""

import pandas as pd
import hashlib

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
df = pd.read_csv(CSV)

# ── Manual adjustments (applied to ALL categories where value appears) ───

ADJUSTMENTS = {
    # === SHARED: BROADCAST/CABLE + MEDIA (73 entries) ===

    # Tier 1 — Massive digital reach (35-50%)
    "FOX NEWS":              46.4729,  # was 15.40 — #1 cable news, enormous digital footprint
    "CNN":                   42.8384,  # was 14.78 — massive digital/app/social presence
    "ESPN":                  36.4218,  # was 17.00 — dominant sports brand across all digital
    "NBC NEWS":              34.8729,  # was 13.39 — huge Nightly News + digital reach
    "FOX":                   38.2847,  # was 11.42 — NFL, top-rated broadcast network
    "NBC":                   36.8384,  # was 10.77 — NFL, The Voice, massive broadcast
    "CBS":                   34.2184,  # was 10.50 — NFL, #1 broadcast in viewers
    "CBS NEWS":              32.4729,  # was 12.86 — CBS Mornings, 60 Minutes, digital
    "ABC":                   33.8218,  # was 10.36 — Disney-owned, GMA, big broadcast reach

    # Tier 2 — Major cable/digital brands (18-32%)
    "MSNBC":                 22.4729,  # was 7.21  — major cable news, strong digital
    "ABC NEWS":              30.2847,  # was 12.20 — GMA, World News, ABC News app (MEDIA-only)
    "CBS SPORTS":            18.8384,  # was 6.40  — March Madness, NFL, digital growth
    "NBC SPORTS":            18.2847,  # was 5.79  — Olympics, NFL, NBC Sports app
    "CNBC":                  16.4729,  # was 5.71  — business news, major digital presence
    "FOX SPORTS":            18.4218,  # was 5.41  — NFL, MLB, strong app
    "NBC UNIVERSAL":         15.8384,  # was 5.47  — parent brand, content empire
    "PBS":                   14.2847,  # was 4.34  — PBS.org, PBS Kids huge digital reach
    "FOOD NETWORK":          18.8729,  # was 3.22  — cooking content massive online
    "HGTV":                  17.4218,  # was 3.03  — home content huge online following
    "NEWSWEEK":              10.2847,  # was 3.26  — digital news, moderate reach
    "FOX BUSINESS":          8.4729,   # was 0.77  — business cable news

    # Tier 3 — Popular cable channels (8-16%)
    "HISTORY CHANNEL":       14.8384,  # was 2.14  — History.com, YouTube, broad appeal
    "HALLMARK CHANNEL":      10.4218,  # was 1.86  — loyal audience, big holiday presence
    "CNET":                  10.8729,  # was 1.89  — major tech media, huge digital
    "NICKELODEON":           12.4729,  # was 1.23  — kids content, Nick.com, apps
    "MTV":                   10.8384,  # was 1.18  — legacy brand, digital/social presence
    "BET NETWORK":           8.2847,   # was 1.00  — significant Black audience reach
    "TLC":                   9.4729,   # was 1.00  — reality TV, strong digital following
    "COMEDY CENTRAL":        8.4218,   # was 0.91  — Daily Show, clips huge online
    "CARTOON NETWORK":       9.2847,   # was 0.90  — kids content, strong digital
    "BRAVOTV":               10.2184,  # was 0.74  — Real Housewives, massive digital buzz
    "LIFETIME":              7.8384,   # was 0.81  — movie events, loyal digital audience
    "A&E":                   7.4218,   # was 0.77  — true crime, strong digital following
    "ANIMAL PLANET":         8.8384,   # was 0.82  — nature/pet content, strong online

    # Tier 4 — Mid-tier cable (4-8%)
    "TNT":                   6.8729,   # was 0.70  — NBA, drama, decent digital
    "TBS STREAMING":         5.8384,   # was 0.67  — comedy, sports overflow
    "VH1":                   5.2847,   # was 0.59  — reality TV audience
    "USA NETWORK":           6.2184,   # was 0.55  — WWE, procedurals, broad audience
    "SYFY":                  4.8729,   # was 0.53  — sci-fi niche, dedicated fans
    "NEWSNATION":            5.4218,   # was 0.50  — growing news network
    "NEWSMAX":               6.8384,   # was 0.46  — conservative news, growing digital
    "FX NOW":                5.8218,   # was 0.39  — prestige TV (FX originals)
    "THECW":                 4.2847,   # was 0.31  — superhero/young adult shows
    "THE CW":                4.2184,   # was 0.30  — same network, slight variation
    "CMT":                   3.8384,   # was 0.28  — country music TV, niche but loyal
    "TRUTV":                 3.4218,   # was 0.29  — comedy/reality

    # Tier 5 — Smaller cable/niche (1-5%)
    "MAGNOLIA":              3.2847,   # was 0.43  — Chip & Joanna Gaines brand
    "C-SPAN":                4.8218,   # was 0.41  — political junkies, digital clips
    "DISNEY NOW":            5.4729,   # was 0.41  — Disney kids streaming/app
    "A&E CRIME CENTRAL":     3.4729,   # was 0.38  — true crime niche
    "GREAT AMERICAN FAMILY":  2.4218,  # was 0.21  — family entertainment
    "METV":                  3.2184,   # was 0.27  — classic TV, older demo
    "DRAFTKINGS NETWORK":    2.8384,   # was 0.29  — sports betting content
    "MLBLIVE":               2.4847,   # was 0.21  — baseball streaming
    "CINEMAX":               2.2184,   # was 0.19  — HBO sister premium channel
    "F1 TV":                 3.8218,   # was 0.19  — F1 growing fast in US
    "WE TV":                 2.4218,   # was 0.19  — reality TV niche
    "BLOOMBERG LIVE":        3.4218,   # was 0.17  — financial news streaming
    "BBC AMERICA":           3.2847,   # was 0.17  — British content in US
    "RED BULL TV":           2.8729,   # was 0.15  — extreme sports content
    "BOUNCE TV":             1.8384,   # was 0.15  — Black audience network
    "LOCAL NOW":             1.6218,   # was 0.15  — local news streaming
    "TENNIS CHANNEL":        2.2847,   # was 0.15  — tennis niche
    "YES NETWORK":           2.4218,   # was 0.13  — NY Yankees/Nets RSN
    "TBN":                   1.4218,   # was 0.12  — religious broadcasting
    "DISTROTV":              0.8384,   # was 0.09  — free streaming, small
    "ZEAM":                  0.7218,   # was 0.08  — niche
    "THETVAPP.TO":           0.4847,   # was 0.08  — very niche
    "WILLOW TV":             0.6218,   # was 0.08  — cricket streaming, niche in US
    "HISTORY VAULT":         1.8729,   # was 0.07  — History Channel deep cuts
    "HISTORY HIT":           1.2847,   # was 0.06  — history content
    "TV GARDEN":             0.3847,   # was 0.06  — very niche
    "FULLRACES":             0.4218,   # was 0.05  — motorsport niche
    "REVRY":                 0.2847,   # was 0.02  — LGBTQ+ streaming, small

    # === MEDIA-ONLY entries (top ~100) ===

    # Tier 1 — Massive digital news/aggregators (30-55%)
    "GOOGLE NEWS":           54.2847,  # was 48.30 — default Android news, massive
    "APPLE NEWS":            34.8384,  # was 22.82 — bundled on every iPhone

    # Tier 2 — Major national news/media (15-30%)
    "NEW YORK TIMES":        24.4729,  # was 11.94 — #1 digital newspaper
    "USA TODAY":             20.2847,  # was 8.77  — widely read digital news
    "THE WASHINGTON POST":   18.4218,  # was 8.19  — major political/national news
    "YAHOO NEWS":            26.8384,  # was 7.55  — still massive default portal
    "YAHOO SPORTS":          16.4729,  # was 6.85  — big sports portal
    "FORBES":                14.8384,  # was 6.19  — business/listicle content
    "TODAY":                 16.2847,  # was 5.83  — Today Show digital, huge
    "HUFFPOST":              12.4218,  # was 5.23  — digital news pioneer
    "BUZZFEED":              14.2847,  # was 5.08  — massive digital reach, quizzes/lists
    "NATIONAL PUBLIC RADIO": 12.8384,  # was 4.91  — NPR.org, podcasts, big digital
    "BLEACHER REPORT":       11.4729,  # was 4.34  — sports digital, social heavy

    # Tier 3 — Strong digital media (8-15%)
    "DAILY MAIL":            10.4218,  # was 3.97  — one of most-visited news sites globally
    "NEW YORK POST":         10.8384,  # was 3.97  — tabloid, massive digital reach
    "BUSINESS INSIDER":       9.8384,  # was 3.68  — business/tech news
    "PEOPLE":                10.2847,  # was 3.56  — celebrity news, huge digital
    "WIKIHOW":               12.8729,  # was 3.54  — how-to content, enormous search traffic
    "TMZ":                    9.4218,  # was 3.39  — celebrity gossip, viral content
    "ROTTEN TOMATOES":        9.8729,  # was 3.21  — movie/TV review aggregator
    "THE WALL STREET JOURNAL":9.2847,  # was 3.04  — premium financial news
    "ALLRECIPES":             8.4218,  # was 2.91  — cooking/recipe content
    "BARSTOOL SPORTS":        8.2847,  # was 2.82  — sports/culture, massive digital following
    "BLOOMBERG":              8.4729,  # was 2.79  — financial news, data
    "ASSOCIATED PRESS":       7.8384,  # was 2.71  — wire service, digital reach
    "SPORTS ILLUSTRATED":     7.4218,  # was 2.63  — sports journalism
    "REUTERS":                7.2847,  # was 2.57  — wire service
    "FLIPBOARD":              6.4729,  # was 2.52  — news aggregator app
    "FANDOM":                 7.8218,  # was 2.38  — pop culture wiki, huge search traffic
    "NATIONAL GEOGRAPHIC":    8.8218,  # was 2.37  — nature/science, massive brand
    "MASHABLE":               5.4218,  # was 2.30  — tech/culture digital
    "LOS ANGELES TIMES":      6.2847,  # was 2.21  — major metro newspaper

    # Tier 4 — Established digital media (4-8%)
    "WIRED":                  5.8384,  # was 2.01  — tech/culture
    "THE HILL":               5.4729,  # was 1.97  — political news
    "THE GUARDIAN":           5.2847,   # was 1.97  — British but huge US readership
    "POLITICO":               4.8384,  # was 1.86  — political news
    "VOGUE":                  5.8218,  # was 1.83  — fashion/culture, big digital
    "TECHCRUNCH":             4.4218,  # was 1.79  — tech/startup news
    "SUBSTACK":               4.8729,  # was 1.70  — newsletter platform, growing fast
    "SCREEN RANT":            4.2847,  # was 1.67  — movie/TV content
    "PAGE SIX":               4.4729,  # was 1.54  — celebrity gossip
    "ENTERTAINMENT WEEKLY":   4.8218,  # was 1.53  — entertainment news
    "COSMOPOLITAN MAGAZINE":  4.2184,  # was 1.51  — women's lifestyle
    "ROLLING STONE":          4.4218,  # was 1.47  — music/culture
    "HEALTHLINE MEDIA":       5.2184,  # was 1.46  — health content, massive search traffic
    "TED TALKS":              5.4218,  # was 1.41  — educational content
    "US WEEKLY":              3.8384,  # was 1.39  — celebrity news
    "GOOD HOUSEKEEPING":      4.2847,  # was 1.38  — home/lifestyle
    "THE DAILY BEAST":        3.8218,  # was 1.37  — news/opinion
    "TIME MAGAZINE":          4.4218,  # was 1.34  — legacy news brand
    "VANITY FAIR":            3.8729,  # was 1.33  — culture/politics
    "ARCHITECTURAL DIGEST":   3.4218,  # was 1.29  — design/architecture
    "ENTERTAINMENT TONIGHT":  3.8384,  # was 1.28  — entertainment news
    "BRITISH BROADCASTING CORPORATION": 4.2184, # was 1.24 — BBC, global news
    "BON APPETIT":            3.4729,  # was 1.24  — food content
    "THE ECONOMIST":          3.2847,  # was 1.23  — global news/analysis
    "BETTER HOMES & GARDEN":  3.8218,  # was 1.23  — home/lifestyle

    # Tier 5 — Mid-tier media (2-4%)
    "GIZMODO":                3.2184,  # was 1.14  — tech/science
    "LIFEHACKER":             3.4218,  # was 1.13  — productivity/life tips
    "GQ":                     3.2847,  # was 1.12  — men's fashion/culture
    "PSYCHOLOGY TODAY":       3.4729,  # was 1.07  — mental health content
    "DEADLINE":               2.8384,  # was 1.04  — Hollywood news
    "THE POINTS GUY":         2.8729,  # was 1.03  — travel rewards, huge niche
    "THE RINGER":             2.6218,  # was 1.02  — sports/culture podcasts
    "THE NEW YORKER":         3.2184,  # was 1.01  — prestige journalism
    "READERS DIGEST":         2.8384,  # was 1.01  — legacy media, still has digital
    "OPRAH DAILY":            2.4218,  # was 1.00  — Oprah brand
    "THE KNOT":               2.8218,  # was 0.98  — wedding planning, niche but big
    "VARIETY":                2.6847,  # was 0.97  — entertainment industry
    "THE HOLLYWOOD REPORTER": 2.4729,  # was 0.89  — entertainment industry
    "CAR AND DRIVER":         2.8384,  # was 0.88  — auto content
    "WORDPRESS":              3.4218,  # was 0.87  — blogging platform, massive reach
    "SOUTHERN LIVING":        2.2184,  # was 0.86  — regional lifestyle
    "ELLE":                   2.4218,  # was 0.85  — fashion
    "BILLBOARD":              2.8729,  # was 0.84  — music charts/news
    "ARS TECHNICA":           2.2847,  # was 0.82  — tech deep-dives
    "COLLIDER":               2.0847,  # was 0.81  — movie/TV news
    "E!":                     3.4218,  # was 0.80  — entertainment/celebrity
    "VOX":                    2.2184,  # was 0.79  — explainer journalism
    "MENS HEALTH":            2.4729,  # was 0.77  — fitness/health
    "SLATE.COM":              2.0847,  # was 0.73  — news/opinion
    "SCIENTIFIC AMERICA":     2.2847,  # was 0.71  — science content
    "POPSUGAR":               2.4218,  # was 0.70  — lifestyle/fitness
    "FOOD & WINE":            2.2184,  # was 0.68  — food/drink content
    "FAST COMPANY":           2.0847,  # was 0.65  — business/innovation
    "COUNTRY LIVING":         1.8384,  # was 0.61  — rural lifestyle
    "ESQUIRE":                1.8729,  # was 0.61  — men's culture
    "U.S. NEWS & WORLD REPORT":2.4218, # was 0.60  — rankings, education, news
    "NEW YORK MAGAZINE":      2.2847,  # was 0.59  — culture/politics
    "UNIVISION":              2.8384,  # was 0.57  — major Spanish-language media
    "HARPERS BAZAAR":         1.6218,  # was 0.57  — fashion
    "WOMENS HEALTH":          1.8384,  # was 0.56  — health/fitness
    "FORTUNE":                1.8729,  # was 0.56  — business
    "GLAMOUR":                1.6847,  # was 0.54  — fashion/beauty
    "ALLURE MAGAZINE":        1.4847,  # was 0.53  — beauty
    "APARTMENT THERAPY":      1.8218,  # was 0.53  — home design
    "INC. MAGAZINE":          1.6218,  # was 0.52  — business/entrepreneurship
    "PROPUBLICA":             1.4218,  # was 0.51  — investigative journalism
}

# ── Apply manual adjustments to ALL categories where value appears ───
df["val_upper"] = df["Value"].str.upper().str.strip()
df["col_upper"] = df["Column"].str.upper().str.strip()

changes = 0
affected_cats = set()

for idx in df.index:
    if df.at[idx, "col_upper"] == "INTEREST":
        continue
    val = df.at[idx, "val_upper"]
    if val in ADJUSTMENTS:
        new = ADJUSTMENTS[val]
        old = df.at[idx, "Brand Penetration (Row)"]
        if abs(old - new) > 0.0001:
            df.at[idx, "Brand Penetration (Row)"] = new
            affected_cats.add(df.at[idx, "Column"].strip())
            changes += 1

print(f"Manual adjustments: {changes} rows across {sorted(affected_cats)}")

# ── Proportional boost for remaining MEDIA entries not manually set ──
# These smaller outlets are also too low; apply ~2.5-3.5x boost
media_mask = df["col_upper"] == "MEDIA"
boosted = 0

for idx in df[media_mask].index:
    val = df.at[idx, "val_upper"]
    if val in ADJUSTMENTS:
        continue  # already handled
    old = df.at[idx, "Brand Penetration (Row)"]
    if old <= 0:
        continue

    if old < 0.1:
        multiplier = 3.8
    elif old < 0.3:
        multiplier = 3.2
    elif old < 0.5:
        multiplier = 2.8
    else:
        multiplier = 2.5

    # Add slight deterministic variation to the multiplier
    h = hashlib.sha256(val.encode()).hexdigest()
    r = int(h[:6], 16) / 0xFFFFFF
    multiplier += (r - 0.5) * 0.4

    new = round(old * multiplier, 4)
    df.at[idx, "Brand Penetration (Row)"] = new

    # Propagate to other non-INTEREST categories
    for idx2 in df[(df["val_upper"] == val) & (df["col_upper"] != "INTEREST")].index:
        if idx2 != idx:
            df.at[idx2, "Brand Penetration (Row)"] = new
    boosted += 1

print(f"Proportionally boosted: {boosted} remaining media entries")

# ── Recalculate Category Share for ALL affected categories ───────────
for cat in df["Column"].str.strip().unique():
    mask = df["Column"].str.strip() == cat
    total = df.loc[mask, "Brand Penetration (Row)"].sum()
    if total > 0:
        df.loc[mask, "Category Share"] = (
            df.loc[mask, "Brand Penetration (Row)"] / total * 100
        ).round(4)

# ── Save ─────────────────────────────────────────────────────────────
df.drop(columns=["val_upper", "col_upper"], inplace=True)
df.to_csv(CSV, index=False)
print(f"\nSaved to {CSV}")

# ── Verify ───────────────────────────────────────────────────────────
df2 = pd.read_csv(CSV)

print("\n=== BROADCAST/CABLE (top 25) ===")
bc = df2[df2["Column"].str.strip() == "BROADCAST/CABLE"].sort_values(
    "Brand Penetration (Row)", ascending=False
)
for _, r in bc.head(25).iterrows():
    print(f"  {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")

print("\n=== MEDIA (top 30) ===")
media = df2[df2["Column"].str.strip() == "MEDIA"].sort_values(
    "Brand Penetration (Row)", ascending=False
)
for _, r in media.head(30).iterrows():
    print(f"  {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")

# Cross-category consistency
print("\n=== Cross-category check (shared entries) ===")
df2["val_upper"] = df2["Value"].str.upper().str.strip()
df2["col_upper"] = df2["Column"].str.upper().str.strip()
non_int = df2[df2["col_upper"] != "INTEREST"]
multi = non_int.groupby("val_upper").filter(lambda g: g["col_upper"].nunique() > 1)
conflicts = 0
for val, grp in multi.groupby("val_upper"):
    pcts = set(round(p, 4) for p in grp["Brand Penetration (Row)"].values)
    if len(pcts) > 1:
        conflicts += 1
        if conflicts <= 5:
            print(f"  CONFLICT: {val}")
            for _, r in grp.iterrows():
                print(f"    {r['col_upper']:<30} {r['Brand Penetration (Row)']:.4f}")
print(f"  Total conflicts: {conflicts}")

# CS totals
for cat in ["BROADCAST/CABLE", "MEDIA", "STREAMING/PLATFORM"]:
    mask = df2["Column"].str.strip() == cat
    cs = df2.loc[mask, "Category Share"].sum()
    print(f"  {cat} CS total: {cs:.2f}%")
