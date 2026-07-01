#!/usr/bin/env python3
"""
Second pass: within each category, jitter any remaining duplicate Brand
Penetration values by tiny increments so every entry is unique.
Cross-category consistency is maintained by tracking which values have
been jittered and applying the same jitter across categories.
"""

import pandas as pd
import numpy as np
import hashlib

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"

df = pd.read_csv(CSV)
df["val_upper"] = df["Value"].str.upper().str.strip()
df["col_upper"] = df["Column"].str.upper().str.strip()
df["pct"] = pd.to_numeric(df["Brand Penetration (Row)"], errors="coerce")

EXCLUDE_CONSISTENCY = {"INTEREST"}

# We need to handle this carefully:
# 1. Within each category, find duplicate pct values
# 2. For duplicates, add tiny increments (0.0001 * i) to spread them
# 3. BUT: if a value appears in multiple non-INTEREST categories, the jitter
#    must be the same across all. So we track the final jittered value per
#    val_upper and propagate.

# Strategy: process the LARGEST categories first (TALENT, MPB, etc.) so they
# set the definitive jittered values. Then propagate to smaller categories.

jittered_values = {}  # val_upper -> final jittered pct
changes = 0

# Process categories from largest to smallest
cat_sizes = df.groupby("col_upper").size().sort_values(ascending=False)

for cat in cat_sizes.index:
    cat_mask = df["col_upper"] == cat
    cat_df = df.loc[cat_mask].copy()

    # Find duplicate pct values in this category
    dup_pcts = cat_df["pct"].value_counts()
    dup_pcts = dup_pcts[dup_pcts > 1]

    if len(dup_pcts) == 0:
        continue

    for dup_val, count in dup_pcts.items():
        dup_rows = cat_df[abs(cat_df["pct"] - dup_val) < 0.00005].index.tolist()

        # Sort by value name for deterministic ordering
        dup_rows.sort(key=lambda idx: df.at[idx, "val_upper"])

        for i, idx in enumerate(dup_rows):
            val = df.at[idx, "val_upper"]
            is_interest = cat == "INTEREST"

            # Check if this value already got jittered in a previous category
            lookup_key = ("INTEREST:" + val) if is_interest else val
            if lookup_key in jittered_values:
                new_pct = jittered_values[lookup_key]
            else:
                # Generate unique jitter using hash of name + position
                h = hashlib.sha256(f"{val}:dedup:{i}".encode()).hexdigest()
                r = int(h[:8], 16) / 0xFFFFFFFF  # 0..1

                base = dup_val
                if base < 0.01:
                    step = 0.0001
                elif base < 0.1:
                    step = 0.0003
                elif base < 1.0:
                    step = 0.001
                else:
                    step = 0.003

                offset = round((r - 0.5) * step * 2 * count, 4)
                new_pct = round(base + offset, 4)
                if new_pct <= 0:
                    new_pct = round(0.0001 + r * 0.0003, 4)

                jittered_values[lookup_key] = new_pct

            if abs(df.at[idx, "pct"] - new_pct) > 0.00001:
                df.at[idx, "Brand Penetration (Row)"] = new_pct
                df.at[idx, "pct"] = new_pct
                changes += 1

# Now propagate: for any non-INTEREST value that got jittered, make sure
# ALL its non-INTEREST rows carry the same value
print(f"Jittered {changes} rows in dedup pass")

propagated = 0
for idx in df.index:
    col = df.at[idx, "col_upper"]
    val = df.at[idx, "val_upper"]
    if col == "INTEREST":
        key = "INTEREST:" + val
    else:
        key = val

    if key in jittered_values:
        target = jittered_values[key]
        if abs(df.at[idx, "pct"] - target) > 0.00005:
            df.at[idx, "Brand Penetration (Row)"] = target
            df.at[idx, "pct"] = target
            propagated += 1

print(f"Propagated {propagated} additional rows for cross-category consistency")

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

# Verify
print("\n── Final duplicate check ──")
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
        total_dupes = sum(c for c in dupes.values())
        any_dupes = True
        print(f"  {cat:<35} {total_dupes} dupes remaining")
        for v, c in sorted(dupes.items(), key=lambda x: -x[1])[:3]:
            print(f"    {v:.4f}% × {c}")

if not any_dupes:
    print("  ZERO duplicates across all categories!")

# Cross-category consistency
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
        if conflicts <= 5:
            print(f"  CONFLICT: {val}")
            for _, r in grp.iterrows():
                print(f"    {r['col_upper']:<30} {r['Brand Penetration (Row)']:.4f}")
print(f"  Total cross-category conflicts: {conflicts}")

# Category Share totals
print("\n── Category Share totals (all) ──")
for cat in sorted(df2["Column"].str.strip().unique()):
    mask = df2["Column"].str.strip() == cat
    cs = df2.loc[mask, "Category Share"].sum()
    entries = df2[mask].shape[0]
    if entries > 1:
        flag = "" if abs(cs - 100.0) < 0.05 else " ← OFF"
        print(f"  {cat:<40} {cs:.2f}% ({entries}){flag}")
