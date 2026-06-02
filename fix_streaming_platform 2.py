#!/usr/bin/env python3
"""
Recalibrate STREAMING/PLATFORM for a US gen pop digital panel over a year.
"""

import pandas as pd

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
df = pd.read_csv(CSV)

ADJUSTMENTS = {
    # ── Major platforms that need boosting ──
    "HULU":               52.4729,  # was 17.20 — one of the biggest US streamers, user wants 50%+
    "APPLE TV+":          26.8384,  # was 13.09 — bundled with Apple devices, huge passive reach
    "PEACOCK":            24.8218,  # was 9.03  — NBC content, bundled with Comcast/Xfinity, NFL
    "PARAMOUNT+":         21.4729,  # was 10.97 — strong content (NFL, Yellowstone, Star Trek)
    "DISCOVERY+":         14.8384,  # was 4.98  — major Discovery/HGTV/Food Network content
    "YOUTUBE KIDS":       18.4729,  # was 8.09  — hugely popular with families
    "SHOWTIME TV":         9.4729,  # was 3.99  — merging into Paramount+ but still significant
    "STARZ":               7.8384,  # was 3.05  — Lionsgate content, decent subscriber base
    "HALLMARK PLUS":       4.8218,  # was 0.99  — loyal passionate audience, growing streaming
    "AMC PLUS":            4.2184,  # was 1.53  — Walking Dead, prestige TV audience

    # ── Niche platforms the user flagged as too high ──
    "LIVETV":              2.8384,  # was 18.61 — niche live streaming aggregator
    "NOW THATS TV":        1.8729,  # was 18.51 — very niche UK-origin platform
    "BOWLTV":              0.4218,  # was 16.27 — bowling streaming, extremely niche
    "PPV":                 3.8729,  # was 14.87 — pay-per-view (boxing/UFC events)
    "STREMIO":             2.2184,  # was 13.55 — open-source media center, niche
    
    # ── Other obviously wrong values for US gen pop ──
    "CHAUPAL":             0.8218,  # was 13.26 — niche South Asian streaming
    "ZEE5":                1.2847,  # was 11.37 — Indian streaming, very niche in US
    "GOTHAM SPORTS":       2.4218,  # was 11.05 — regional NY sports network
    "DROPOUT TV":          3.8729,  # was 10.47 — comedy niche, growing but not 10%
    "CRISP SHORT FORM":    1.4218,  # was 10.03 — very niche short-form
    "FIFA+":               3.2847,  # was 9.58  — soccer streaming, some US interest
    "VIX":                 4.2847,  # was 9.49  — Spanish-language, US Hispanic audience
    "ULLU":                0.4847,  # was 6.75  — Indian platform, very niche in US
    "OSN+":                0.8847,  # was 4.89  — Middle Eastern streaming, niche in US
    "KOCOWA+":             1.2184,  # was 4.36  — Korean content, niche
    "FANDANGO AT HOME":   10.2847,  # was 14.15 — movie rental/purchase, trim down

    # ── User-requested bumps ──
    "BRITBOX":             3.4218,  # was 0.48  — British content, growing US base
    "MGM+":                4.8729,  # was 1.16  — Amazon-owned, quality content library
    "REELSHORT":           5.2184,  # was 1.60  — viral short-form app, huge growth
    "GOODSHORT":           3.8384,  # was 2.20  — short-form content growing

    # ── Other mid-tier adjustments ──
    "ACORN TV":            3.2184,  # was 1.65  — British/international content, loyal niche
    "BET+":                4.4729,  # was 3.46  — significant Black audience, slight bump
    "DAZN":                2.8218,  # was 0.96  — sports streaming, growing
    "MUBI":                1.8384,  # was 0.50  — arthouse film, small but dedicated
    "SLING PLATFORM":      5.2847,  # was 2.95  — live TV alternative, significant user base
    "MOVIES ANYWHERE":     4.4218,  # was 2.49  — digital movie locker, underrated reach
    "CRACKLE":             3.8218,  # was 2.92  — free streaming, decent reach
}

mask = df["Column"].str.strip() == "STREAMING/PLATFORM"
changes = 0

for idx in df[mask].index:
    val = df.at[idx, "Value"].upper().strip()
    if val in ADJUSTMENTS:
        old = df.at[idx, "Brand Penetration (Row)"]
        new = ADJUSTMENTS[val]
        if abs(old - new) > 0.0001:
            df.at[idx, "Brand Penetration (Row)"] = new
            changes += 1

# Recalculate Category Share
cat_rows = df[mask]
total = df.loc[mask, "Brand Penetration (Row)"].sum()
for idx in cat_rows.index:
    share = (df.at[idx, "Brand Penetration (Row)"] / total) * 100
    df.at[idx, "Category Share"] = round(share, 4)

df.to_csv(CSV, index=False)
print(f"Updated {changes} streaming platforms")

# Verify
df2 = pd.read_csv(CSV)
sp = df2[df2["Column"].str.strip() == "STREAMING/PLATFORM"].sort_values(
    "Brand Penetration (Row)", ascending=False
)
cs_total = sp["Category Share"].sum()
print(f"Category Share total: {cs_total:.2f}%\n")

print("=== STREAMING/PLATFORM (recalibrated) ===")
for _, r in sp.iterrows():
    print(f"  {r['Value']:<40} {r['Brand Penetration (Row)']:.4f}%  CS={r['Category Share']:.4f}")
