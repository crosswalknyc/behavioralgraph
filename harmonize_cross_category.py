#!/usr/bin/env python3
"""
Cross-category harmonization: ensure every Value that appears in multiple
categories carries the SAME Brand Penetration (Row) everywhere (except
INTEREST, which is allowed to differ). Category Share is recalculated
per-category after harmonization.
"""

import pandas as pd
import numpy as np

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"

CALIBRATED = {
    "MOST PURCHASED BRANDS", "WHERE THEY SHOP", "QSR", "INTEREST",
    "AMUSEMENT PARKS", "APP/PLATFORM USAGE", "AUTOMOBILE", "BANKING",
    "DIGITAL BANKING", "CREDIT PROVIDER", "INVESTMENTS", "BETTING",
    "FRANCHISE", "GAMES", "INSURANCE", "MEDIA", "PHARMACY", "TOYS",
    "TRAVEL", "WHERE THEY DINE", "SEARCH ENGINE/AI", "STREAMING/MUSIC",
    "VIRTUAL MVPD FAST", "TECHNOLOGY/DEVICE", "TELECOM", "WORKOUT FACILITY",
    "EVENTS", "VENUE", "TICKETING", "TALENT", "SPORTS ORGANIZATIONS",
    "SPORTS TEAM", "COLLEGE/UNIVERSITY",
}

EXCLUDE = {"INTEREST"}

df = pd.read_csv(CSV)
df["val_upper"] = df["Value"].str.upper().str.strip()
df["col_upper"] = df["Column"].str.upper().str.strip()
df["pct"] = pd.to_numeric(df["Brand Penetration (Row)"], errors="coerce")

# ── Step 1: determine the "best" penetration for each value ──────────
non_interest = df[~df["col_upper"].isin(EXCLUDE)]
best = {}

for val, grp in non_interest.groupby("val_upper"):
    cal_rows = grp[grp["col_upper"].isin(CALIBRATED - EXCLUDE)]
    if len(cal_rows) > 0:
        best[val] = cal_rows["pct"].max()
    else:
        best[val] = grp["pct"].max()

# ── Step 2: apply the best value to every non-INTEREST row ───────────
changes = 0
changed_cats = set()

for idx in df.index:
    if df.at[idx, "col_upper"] in EXCLUDE:
        continue
    val = df.at[idx, "val_upper"]
    target = best.get(val)
    if target is None or pd.isna(target):
        continue
    current = df.at[idx, "pct"]
    if pd.isna(current) or abs(current - target) > 0.00005:
        df.at[idx, "Brand Penetration (Row)"] = round(target, 4)
        df.at[idx, "pct"] = round(target, 4)
        changed_cats.add(df.at[idx, "col_upper"])
        changes += 1

print(f"Harmonised {changes} rows across {len(changed_cats)} categories")
print(f"Categories touched: {sorted(changed_cats)}")

# ── Step 3: recalculate Category Share for EVERY category ────────────
for cat, grp in df.groupby("col_upper"):
    total = grp["pct"].sum()
    if total > 0:
        for idx in grp.index:
            share = (df.at[idx, "pct"] / total) * 100
            df.at[idx, "Category Share"] = round(share, 4)

# ── Step 4: format Brand Penetration to 4 decimal places ────────────
df["Brand Penetration (Row)"] = df["pct"].apply(
    lambda v: round(v, 4) if pd.notna(v) else v
)

# ── Step 5: drop helper columns and save ─────────────────────────────
df.drop(columns=["val_upper", "col_upper", "pct"], inplace=True)
df.to_csv(CSV, index=False)
print(f"\nSaved to {CSV}")

# ── Step 6: verification — spot-check some well-known cross-cat values
print("\n── Verification spot-checks ──")
df2 = pd.read_csv(CSV)
df2["val_upper"] = df2["Value"].str.upper().str.strip()
df2["col_upper"] = df2["Column"].str.upper().str.strip()

spot_checks = [
    "TAYLOR SWIFT", "BEYONCE", "ARIANA GRANDE", "LEBRON JAMES",
    "DALLAS COWBOYS", "LEGO", "STAR WARS", "POKEMON", "MINECRAFT",
    "ABC", "ESPN", "FORTNITE", "BARBIE", "ROBLOX", "VIZIO",
    "TONY HAWK", "AARON JUDGE", "ARSENAL FC",
]
for name in spot_checks:
    rows = df2[df2["val_upper"] == name]
    if len(rows) == 0:
        continue
    pcts = rows["Brand Penetration (Row)"].unique()
    cats = list(zip(rows["col_upper"], rows["Brand Penetration (Row)"]))
    status = "✓ CONSISTENT" if len(pcts) == 1 else "✗ INCONSISTENT"
    print(f"\n  {name} ({status}):")
    for cat, pct in cats:
        cs = rows[rows["col_upper"] == cat]["Category Share"].values[0]
        print(f"    {cat:<35} BPR={pct:.4f}  CS={cs:.4f}")
