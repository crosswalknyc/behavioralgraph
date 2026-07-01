#!/usr/bin/env python3
"""
Final dedup: guarantee every entry within a category has a unique Brand
Penetration value. Walk each category, find duplicate clusters, and
spread them with tiny increments. Propagate to other categories.
"""

import pandas as pd
import numpy as np

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"

df = pd.read_csv(CSV)
df["val_upper"] = df["Value"].str.upper().str.strip()
df["col_upper"] = df["Column"].str.upper().str.strip()
df["pct"] = pd.to_numeric(df["Brand Penetration (Row)"], errors="coerce")

final_values = {}  # val_upper -> pct (non-INTEREST)
interest_values = {}  # val_upper -> pct (INTEREST only)

# Process categories largest first so big categories set the values
cat_order = df.groupby("col_upper").size().sort_values(ascending=False).index

total_fixes = 0

for cat in cat_order:
    cat_mask = df["col_upper"] == cat
    is_interest = cat == "INTEREST"

    used_in_cat = set()
    cat_indices = df.loc[cat_mask].sort_values("pct", ascending=False).index.tolist()

    for idx in cat_indices:
        val = df.at[idx, "val_upper"]
        current = df.at[idx, "pct"]

        if is_interest:
            store = interest_values
        else:
            store = final_values

        # If this val already has a final value from another category, use it
        if val in store:
            target = store[val]
        else:
            target = current

        # Ensure uniqueness within this category
        rounded = round(target, 4)
        if rounded in used_in_cat:
            # Jitter up or down by 0.0001 increments until unique
            for delta in range(1, 200):
                up = round(rounded + delta * 0.0001, 4)
                down = round(rounded - delta * 0.0001, 4)
                if up not in used_in_cat:
                    rounded = up
                    break
                if down > 0 and down not in used_in_cat:
                    rounded = down
                    break

        used_in_cat.add(rounded)

        if val not in store:
            store[val] = rounded

        if abs(df.at[idx, "pct"] - rounded) > 0.00005:
            df.at[idx, "Brand Penetration (Row)"] = rounded
            df.at[idx, "pct"] = rounded
            total_fixes += 1

print(f"Fixed {total_fixes} values for within-category uniqueness")

# Propagation pass: ensure non-INTEREST values are consistent
prop = 0
for idx in df.index:
    col = df.at[idx, "col_upper"]
    val = df.at[idx, "val_upper"]
    if col == "INTEREST":
        target = interest_values.get(val)
    else:
        target = final_values.get(val)
    if target is not None and abs(df.at[idx, "pct"] - target) > 0.00005:
        df.at[idx, "Brand Penetration (Row)"] = target
        df.at[idx, "pct"] = target
        prop += 1

print(f"Propagated {prop} values for cross-category consistency")

# Recalculate Category Share
for cat, grp in df.groupby("col_upper"):
    total = grp["pct"].sum()
    if total > 0:
        for idx2 in grp.index:
            share = (df.at[idx2, "pct"] / total) * 100
            df.at[idx2, "Category Share"] = round(share, 4)

# Save
df["Brand Penetration (Row)"] = df["pct"].apply(
    lambda v: round(v, 4) if pd.notna(v) else v
)
df.drop(columns=["val_upper", "col_upper", "pct"], inplace=True)
df.to_csv(CSV, index=False)
print(f"Saved to {CSV}")

# ── Verify ────────────────────────────────────────────────────────────
print("\n── Duplicate check ──")
df2 = pd.read_csv(CSV)
df2["pct"] = pd.to_numeric(df2["Brand Penetration (Row)"], errors="coerce")
from collections import Counter

any_dupes = False
for cat in sorted(df2["Column"].str.strip().unique()):
    mask = df2["Column"].str.strip() == cat
    vals = [round(v, 4) for v in df2.loc[mask, "pct"].values if not np.isnan(v)]
    counts = Counter(vals)
    dupes = {v: c for v, c in counts.items() if c > 1}
    if dupes:
        any_dupes = True
        td = sum(c for c in dupes.values())
        print(f"  {cat:<35} {td} dupes")

if not any_dupes:
    print("  ZERO duplicates across ALL categories!")

# Cross-category
print("\n── Cross-category consistency ──")
df2["val_upper"] = df2["Value"].str.upper().str.strip()
df2["col_upper"] = df2["Column"].str.upper().str.strip()
non_int = df2[df2["col_upper"] != "INTEREST"]
multi = non_int.groupby("val_upper").filter(lambda g: g["col_upper"].nunique() > 1)
conflicts = 0
for val, grp in multi.groupby("val_upper"):
    pcts = set(round(p, 4) for p in grp["Brand Penetration (Row)"].values)
    if len(pcts) > 1:
        conflicts += 1
print(f"  Cross-category conflicts: {conflicts}")

# Sample of TALENT to show organic values
print("\n── Sample: TALENT bottom-tier (was all 0.0380 or 0.0220) ──")
talent = df2[df2["Column"].str.strip() == "TALENT"].sort_values(
    "Brand Penetration (Row)", ascending=True
)
for _, r in talent.head(20).iterrows():
    print(f"  {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")

print("\n── Sample: MPB mid-tier (was all ~1.5000) ──")
mpb = df2[df2["Column"].str.strip() == "MOST PURCHASED BRANDS"]
mid = mpb[(mpb["Brand Penetration (Row)"] > 1.3) & (mpb["Brand Penetration (Row)"] < 1.7)]
mid = mid.sort_values("Brand Penetration (Row)", ascending=False)
for _, r in mid.head(20).iterrows():
    print(f"  {r['Value']:<35} {r['Brand Penetration (Row)']:.4f}%")
