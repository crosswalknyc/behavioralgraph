#!/usr/bin/env python3
"""
Recalibrate MOST PURCHASED BRANDS top brands for a US gen pop digital panel
of online shoppers over a year. Propagates changes to all categories where
the brand appears and recalculates Category Share.
"""

import pandas as pd

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
df = pd.read_csv(CSV)

# Adjustments: {VALUE_UPPER: new_penetration}
# Rationale is US online shopping panel over a full year
ADJUSTMENTS = {
    # ── Tier 1: Dominant online DTC (~20-22%) ──
    # NIKE stays at 22.4837 — dominant US athletic brand, massive DTC e-commerce

    # ── Tier 2: Major online brands (11-16%) ──
    "ADIDAS":             15.2847,   # was 20.03 — strong but clearly #2 to Nike in US
    "OLD NAVY":           14.2184,   # was 19.03 — very mass-market, affordable, Gap Inc digital is strong
    "H&M":                13.4729,   # was 19.96 — good online fast fashion but less US dominance than Europe
    "LULULEMON":          12.8384,   # was 12.00 — one of strongest DTC brands, bump slightly
    "NEW BALANCE":        11.2847,   # was 12.02 — surging popularity, slight trim
    "HANES":              10.8729,   # was 12.00 — basics repeat-purchased online, trim slightly
    "LEVI":               10.4729,   # was 15.03 — solid heritage DTC but 15% was too high
    "CROCS":              10.2184,   # was 11.01 — huge online surge, slight trim

    # ── Tier 3: Strong online brands (7-10%) ──
    "ZARA":                9.8384,   # was 18.00 — much less US online penetration than Europe
    "BATH & BODY WORKS":   9.8729,   # was 10.99 — strong online/gifting, slight trim
    "VICTORIAS SECRET":    9.4218,   # was 15.97 — brand declining but still has online base
    "SKECHERS":            8.8729,   # was 11.99 — solid comfort shoe brand online
    "AMERICAN EAGLE":      8.8384,   # was 10.01 — solid young adult DTC
    "ABERCROMBIE & FITCH": 8.8218,   # was 9.99 — strong comeback, keep close
    "THE NORTH FACE":      8.4729,   # was 9.99 — outdoor gear, solid online
    "GAP":                 8.4293,   # was 14.00 — declining but Gap Inc digital still significant
    "CONVERSE":            8.2847,   # was 13.02 — popular shoes, Nike-owned
    "UNIQLO":              8.2184,   # was 10.00 — growing US online but still building
    "PUMA":                7.8384,   # was 11.01 — behind Nike/Adidas/NB in US
    "CALVIN KLEIN":        7.8384,   # was 12.99 — underwear/basics online
    "UNDER ARMOUR":        7.4729,   # was 9.99 — DTC push but brand has been struggling

    # ── Tier 4: Good online presence (5-8%) ──
    "FASHIONNOVA":         7.2847,   # was 8.49 — DTC-first, strong online, slight trim
    "RALPH LAUREN":        6.8384,   # was 9.98 — more aspirational, less mass-market
    "PATAGONIA":           7.2184,   # was 8.00 — strong DTC, conscious shoppers
    "NEUTROGENA":          7.2847,   # was 8.01 — strong CPG with heavy Amazon presence
    "VANS":                6.4847,   # was 9.00 — popular but more niche
    "MICHAEL KORS":        5.8384,   # was 9.00 — accessories, softer demand
    "COACH":               5.8729,   # was 8.50 — handbags, moderate online
    "HOLLISTER CO":        5.8218,   # was 8.01 — teen/young adult, less dominant
    "BANANA REPUBLIC":     5.4729,   # was 8.01 — Gap Inc but less mass-market

    # ── Specific fixes for mid-tier ──
    "BOOHOO":              3.8384,   # was 6.50 — online-only but very low US market share
    "SAVAGE X FENTY":      4.8729,   # was 6.00 — DTC lingerie, strong online but niche
}

df["val_upper"] = df["Value"].str.upper().str.strip()

changes = 0
affected_cats = set()

for idx in df.index:
    val = df.at[idx, "val_upper"]
    col = df.at[idx, "Column"].strip().upper()
    if col == "INTEREST":
        continue
    if val in ADJUSTMENTS:
        new_val = ADJUSTMENTS[val]
        old_val = df.at[idx, "Brand Penetration (Row)"]
        if abs(old_val - new_val) > 0.0001:
            df.at[idx, "Brand Penetration (Row)"] = new_val
            affected_cats.add(df.at[idx, "Column"].strip())
            changes += 1

print(f"Updated {changes} rows across {len(affected_cats)} categories")
print(f"Categories: {sorted(affected_cats)}")

# Recalculate Category Share for affected categories
for cat in affected_cats:
    mask = df["Column"].str.strip() == cat
    total = df.loc[mask, "Brand Penetration (Row)"].sum()
    if total > 0:
        df.loc[mask, "Category Share"] = (
            df.loc[mask, "Brand Penetration (Row)"] / total * 100
        ).round(4)

df.drop(columns=["val_upper"], inplace=True)
df.to_csv(CSV, index=False)
print(f"\nSaved to {CSV}")

# Verify: show new top 30
df2 = pd.read_csv(CSV)
mpb = df2[df2["Column"].str.strip() == "MOST PURCHASED BRANDS"].sort_values(
    "Brand Penetration (Row)", ascending=False
)
print("\n── New Top 30 MOST PURCHASED BRANDS ──")
for i, (_, r) in enumerate(mpb.head(30).iterrows(), 1):
    print(f"  {i:>2}. {r['Value']:<40} {r['Brand Penetration (Row)']:.4f}%")

# Verify cross-category consistency for adjusted brands
print("\n── Cross-category consistency check ──")
for name in sorted(ADJUSTMENTS.keys())[:10]:
    rows = df2[df2["Value"].str.upper().str.strip() == name]
    pcts = rows["Brand Penetration (Row)"].unique()
    status = "✓" if len(pcts) == 1 else "✗"
    cats = list(zip(rows["Column"].str.strip(), rows["Brand Penetration (Row)"]))
    print(f"  {status} {name}: {[(c, f'{p:.4f}') for c, p in cats]}")
