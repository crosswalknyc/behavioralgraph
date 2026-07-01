#!/usr/bin/env python3
"""
Add deterministic organic noise to Brand Penetration values so no two
entries in the same category share the exact same value. Noise is seeded
by value name so cross-category consistency is preserved (same name →
same penetration everywhere except INTEREST).
"""

import pandas as pd
import numpy as np
import hashlib

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"


def name_hash(name, salt=""):
    """Return two floats in [0,1) from a hash of name+salt."""
    h = hashlib.sha256((salt + name).encode()).hexdigest()
    r1 = int(h[:8], 16) / 0xFFFFFFFF
    r2 = int(h[8:16], 16) / 0xFFFFFFFF
    return r1, r2


def noise_for(value, name, salt=""):
    """Add small deterministic noise proportional to value magnitude."""
    if pd.isna(value) or value <= 0:
        r1, _ = name_hash(name, salt)
        return round(0.0001 + r1 * 0.0040, 4)

    r1, r2 = name_hash(name, salt)
    offset_frac = r1 - 0.5  # range -0.5 to +0.5

    if value < 0.03:
        spread = 0.008
    elif value < 0.06:
        spread = 0.012
    elif value < 0.12:
        spread = 0.018
    elif value < 0.30:
        spread = 0.035
    elif value < 0.60:
        spread = 0.055
    elif value < 1.0:
        spread = 0.075
    elif value < 2.0:
        spread = 0.12
    elif value < 5.0:
        spread = 0.18
    elif value < 10.0:
        spread = 0.22
    elif value < 16.0:
        spread = 0.28
    else:
        spread = 0.35

    new_val = round(value + offset_frac * spread, 4)
    return max(0.0001, new_val)


df = pd.read_csv(CSV)
df["val_upper"] = df["Value"].str.upper().str.strip()
df["col_upper"] = df["Column"].str.upper().str.strip()
df["pct"] = pd.to_numeric(df["Brand Penetration (Row)"], errors="coerce")

# ── Step 1: build noised values for non-INTEREST ────────────────────
# Group by val_upper to get one noise per value name
non_interest = df[df["col_upper"] != "INTEREST"]
noised_map = {}
for val in non_interest["val_upper"].unique():
    current = non_interest.loc[non_interest["val_upper"] == val, "pct"].iloc[0]
    noised_map[val] = noise_for(current, val, salt="gen_pop_2026")

# ── Step 2: build noised values for INTEREST (independent noise) ────
interest_mask = df["col_upper"] == "INTEREST"
interest_noised = {}
for val in df.loc[interest_mask, "val_upper"].unique():
    current = df.loc[interest_mask & (df["val_upper"] == val), "pct"].iloc[0]
    interest_noised[val] = noise_for(current, val, salt="interest_2026")

# ── Step 3: apply ────────────────────────────────────────────────────
changes = 0
for idx in df.index:
    col = df.at[idx, "col_upper"]
    val = df.at[idx, "val_upper"]

    if col == "INTEREST":
        new_pct = interest_noised.get(val)
    else:
        new_pct = noised_map.get(val)

    if new_pct is not None:
        old_pct = df.at[idx, "pct"]
        if pd.isna(old_pct) or abs(old_pct - new_pct) > 0.00005:
            df.at[idx, "Brand Penetration (Row)"] = new_pct
            df.at[idx, "pct"] = new_pct
            changes += 1

print(f"Noised {changes} rows")

# ── Step 4: recalculate Category Share for every category ────────────
for cat, grp in df.groupby("col_upper"):
    total = grp["pct"].sum()
    if total > 0:
        for idx2 in grp.index:
            share = (df.at[idx2, "pct"] / total) * 100
            df.at[idx2, "Category Share"] = round(share, 4)

# ── Step 5: save ─────────────────────────────────────────────────────
df["Brand Penetration (Row)"] = df["pct"].apply(
    lambda v: round(v, 4) if pd.notna(v) else v
)
df.drop(columns=["val_upper", "col_upper", "pct"], inplace=True)
df.to_csv(CSV, index=False)
print(f"Saved to {CSV}")

# ── Step 6: verify — check for remaining duplicates ─────────────────
print("\n── Duplicate check after noise ──")
df2 = pd.read_csv(CSV)
df2["pct"] = pd.to_numeric(df2["Brand Penetration (Row)"], errors="coerce")
from collections import Counter

worst_cats = []
for cat in sorted(df2["Column"].str.strip().unique()):
    mask = df2["Column"].str.strip() == cat
    vals = df2.loc[mask, "pct"].values
    vals = [round(v, 4) for v in vals if not np.isnan(v)]
    counts = Counter(vals)
    dupes = {v: c for v, c in counts.items() if c > 1}
    total_dupes = sum(c for c in dupes.values()) if dupes else 0
    worst = max(dupes.values()) if dupes else 0
    if dupes:
        worst_cats.append((cat, len(vals), total_dupes, worst, dupes))

if worst_cats:
    worst_cats.sort(key=lambda x: -x[2])
    print(f"  {len(worst_cats)} categories still have some dupes:")
    for cat, total, td, w, dupes in worst_cats[:15]:
        print(f"    {cat:<35} {td} dupes (worst: {w}x)")
        top3 = sorted(dupes.items(), key=lambda x: -x[1])[:3]
        for v, c in top3:
            print(f"      {v:.4f}% × {c}")
else:
    print("  NO duplicates remain!")

# ── Step 7: verify cross-category consistency ────────────────────────
print("\n── Cross-category consistency spot-check ──")
df2["val_upper"] = df2["Value"].str.upper().str.strip()
df2["col_upper"] = df2["Column"].str.upper().str.strip()
checks = [
    "TAYLOR SWIFT", "LEBRON JAMES", "DALLAS COWBOYS", "NIKE",
    "LEGO", "STAR WARS", "ESPN", "AMAZON",
]
for name in checks:
    rows = df2[df2["val_upper"] == name]
    non_int = rows[rows["col_upper"] != "INTEREST"]
    if len(non_int) > 1:
        pcts = non_int["Brand Penetration (Row)"].unique()
        status = "✓" if len(pcts) == 1 else "✗"
        cats = [(r["col_upper"], r["Brand Penetration (Row)"]) for _, r in non_int.iterrows()]
        print(f"  {status} {name}: {[(c, f'{p:.4f}') for c, p in cats]}")

# Category Share totals
print("\n── Category Share totals (sample) ──")
for cat in ["MOST PURCHASED BRANDS", "TALENT", "ACTOR", "SPORTS TEAM", "INTEREST"]:
    mask = df2["Column"].str.strip() == cat
    cs = df2.loc[mask, "Category Share"].sum()
    print(f"  {cat:<35} {cs:.2f}%")
