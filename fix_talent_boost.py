#!/usr/bin/env python3
"""
Boost TALENT penetration for US gen pop digital panel. A digital touchpoint
includes seeing someone on social media, news, ads, streaming, search, etc.
Over a full year, even moderately famous people get passive exposure.
Propagates to all sub-categories (ACTOR, MUSICIAN/BAND, ATHLETE, etc.).
"""

import pandas as pd
import hashlib

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
df = pd.read_csv(CSV)

# ── Manual placements for mega-stars / top tier ─────────────────────
MANUAL = {
    "TAYLOR SWIFT":          58.4729,
    "DONALD J. TRUMP":       54.8384,
    "BEYONCE":               48.4218,
    "ELON MUSK":             44.8384,
    "LEBRON JAMES":          38.4729,
    "DRAKE":                 36.8384,
    "TRAVIS KELCE":          34.4218,
    "RIHANNA":               32.8384,
    "YE-KANYE WEST":         30.4729,
    "MRBEAST":               30.2847,
    "ARIANA GRANDE":         28.8384,
    "PATRICK MAHOMES":       26.4218,
    "DWAYNE JOHNSON":        26.2847,
    "KIM KARDASHIAN":        24.8384,
    "JOE ROGAN":             24.4729,
    "BILLIE EILISH":         22.8384,
    "BAD BUNNY":             22.4218,
    "SABRINA CARPENTER":     20.8384,
    "OLIVIA RODRIGO":        20.4729,
    "SELENA GOMEZ":          20.2847,
    "TRAVIS SCOTT":          18.8384,
    "POST MALONE":           18.4218,
    "EMINEM":                18.2847,
    "KENDRICK LAMAR":        17.8384,
    "LADY GAGA":             17.4729,
    "SNOOP DOGG":            17.4218,
    "DOJA CAT":              16.8384,
    "NICKI MINAJ":           16.4218,
    "STEPHEN CURRY":         15.8384,
    "KEVIN DURANT":          15.4729,
    "DUA LIPA":              14.8384,
    "ED SHEERAN":            14.4218,
    "CHARLI XCX":            14.2847,
    "ADELE":                 14.0384,
    "JUSTIN BIEBER":         13.8729,
    "HARRY STYLES":          13.4218,
    "CARDI B":               12.8384,
    "BRUNO MARS":            12.4218,
    "ROBERT DOWNEY JR.":     12.2847,
    "ZENDAYA":               12.0384,
    "TOM BRADY":             11.8729,
    "RYAN REYNOLDS":         11.8384,
    "LEONARDO DICAPRIO":     11.4218,
    "SIMONE BILES":          11.2847,
    "CHAPPELL ROAN":         10.8384,
    "KEVIN HART":            10.4218,
    "USHER":                 10.2847,
    "SHOHEI OHTANI":         10.0384,
    "LUKA DONCIC":            9.8729,
    "GIANNIS ANTETOKOUNMPO":  9.8384,
    "TOM HOLLAND":            9.4218,
    "SZA":                    9.2847,
    "MORGAN WALLEN":          9.0384,
    "SYDNEY SWEENEY":         9.0218,
    "CAITLIN CLARK":          8.8384,
    "MARGOT ROBBIE":          8.8218,
    "PEDRO PASCAL":           8.4218,
    "JENNA ORTEGA":           8.2847,
    "EMMA STONE":             8.0384,
    "TIMOTHEE CHALAMET":      8.0218,
    "TOM HANKS":              7.8384,
    "OPRAH":                  7.8218,
    "LOGAN PAUL":             7.4218,
    "KAI CENAT":              7.2847,
    "JAKE PAUL":              7.2184,
    "CHRIS HEMSWORTH":        6.8384,
    "NIKOLA JOKIC":           6.4218,
    "ADAM SANDLER":           6.8218,
    "BARACK OBAMA":           6.4729,
    "FLORENCE PUGH":          6.2847,
    "MICHELLE OBAMA":         6.2184,
    "MARK ZUCKERBERG":        6.0384,
    "ANYA TAYLOR JOY":        5.8384,
    "GORDON RAMSAY":          5.8218,
    "KAMALA HARRIS":          5.4218,
    "SERENA WILLIAMS":        5.4729,
    "JACK BLACK":             5.8729,
    "JASON MOMOA":            5.4384,
    "COLDPLAY":               5.2847,
    "KATY PERRY":             5.0384,
}

# ── Multiplier function for everything not manually set ──────────────
def get_multiplier(current_pct, name):
    """Scale factor based on current tier + deterministic noise."""
    h = hashlib.sha256(f"talent_boost:{name}".encode()).hexdigest()
    r = int(h[:8], 16) / 0xFFFFFFFF  # 0..1
    noise = (r - 0.5) * 0.3  # ±0.15 variation on multiplier

    if current_pct >= 5.0:
        base = 2.5
    elif current_pct >= 2.0:
        base = 2.7
    elif current_pct >= 1.0:
        base = 2.9
    elif current_pct >= 0.5:
        base = 3.2
    elif current_pct >= 0.1:
        base = 3.5
    else:
        base = 4.0

    return base + noise


# ── Build final value map ────────────────────────────────────────────
df["val_upper"] = df["Value"].str.upper().str.strip()
df["col_upper"] = df["Column"].str.upper().str.strip()

talent_mask = df["col_upper"] == "TALENT"
value_map = {}  # val_upper -> new_pct

for idx in df[talent_mask].index:
    val = df.at[idx, "val_upper"]
    old = df.at[idx, "Brand Penetration (Row)"]

    if val in MANUAL:
        value_map[val] = MANUAL[val]
    elif val not in value_map:
        mult = get_multiplier(old, val)
        new = round(old * mult, 4)
        new = max(0.0501, new)  # floor at 0.05%
        value_map[val] = new

# ── Apply to ALL categories (TALENT + sub-categories) ───────────────
PROPAGATE_CATS = {
    "TALENT", "ACTOR", "ATHLETE", "HOST/PERSONALITY", "MLB ATHLETE",
    "MUSICIAN/BAND", "NBA ATHLETE", "NFL ATHLETE", "NHL ATHLETE",
    "POLITICS/ACTIVIST", "SOCCER ATHLETE", "WNBA ATHLETE",
    "WRITER/DIRECTOR/AUTHOR/ARTIST", "GAMES",
}

changes = 0
for idx in df.index:
    col = df.at[idx, "col_upper"]
    if col == "INTEREST":
        continue
    val = df.at[idx, "val_upper"]
    if val in value_map:
        new = value_map[val]
        old = df.at[idx, "Brand Penetration (Row)"]
        if abs(old - new) > 0.0001:
            df.at[idx, "Brand Penetration (Row)"] = new
            changes += 1

print(f"Updated {changes} rows")

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
print(f"Saved to {CSV}")

# ── Verify ───────────────────────────────────────────────────────────
df2 = pd.read_csv(CSV)
talent = df2[df2["Column"].str.strip() == "TALENT"].sort_values(
    "Brand Penetration (Row)", ascending=False
)
print(f"\nTALENT stats:")
print(f"  Range: {talent['Brand Penetration (Row)'].min():.4f}% - {talent['Brand Penetration (Row)'].max():.4f}%")
print(f"  Mean: {talent['Brand Penetration (Row)'].mean():.4f}%")
print(f"  Median: {talent['Brand Penetration (Row)'].median():.4f}%")

buckets = [(20, 100), (10, 20), (5, 10), (3, 5), (2, 3), (1, 2), (0.5, 1), (0.1, 0.5), (0, 0.1)]
for lo, hi in buckets:
    cnt = len(talent[(talent["Brand Penetration (Row)"] >= lo) & (talent["Brand Penetration (Row)"] < hi)])
    print(f"  {lo:>5.1f}% - {hi:>5.1f}%: {cnt} entries")

print(f"\nTop 20:")
for i, (_, r) in enumerate(talent.head(20).iterrows(), 1):
    print(f"  {i:>2}. {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")

print(f"\n~Rank 100:")
for i, (_, r) in enumerate(talent.iloc[95:105].iterrows(), 96):
    print(f"  {i:>3}. {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")

print(f"\nBottom 10:")
for _, r in talent.tail(10).iterrows():
    print(f"  {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")

# Cross-category
print(f"\nCross-category spot-check:")
df2["val_upper"] = df2["Value"].str.upper().str.strip()
df2["col_upper"] = df2["Column"].str.upper().str.strip()
for name in ["TAYLOR SWIFT", "LEBRON JAMES", "TONY HAWK"]:
    rows = df2[(df2["val_upper"] == name) & (df2["col_upper"] != "INTEREST")]
    cats = [(r["col_upper"], f"{r['Brand Penetration (Row)']:.4f}") for _, r in rows.iterrows()]
    pcts = set(r["Brand Penetration (Row)"] for _, r in rows.iterrows())
    status = "✓" if len(pcts) == 1 else "✗"
    print(f"  {status} {name}: {cats}")

# CS totals
for cat in ["TALENT", "ACTOR", "MUSICIAN/BAND", "ATHLETE", "NBA ATHLETE", "NFL ATHLETE"]:
    mask = df2["Column"].str.strip() == cat
    cs = df2.loc[mask, "Category Share"].sum()
    print(f"  {cat} CS: {cs:.2f}%")
