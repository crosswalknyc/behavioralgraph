#!/usr/bin/env python3
"""
Add realistic decimal variation to all Brand Penetration values that are
exact whole numbers or have fewer than 4 meaningful decimal places.

Uses a deterministic hash of the brand name so the same brand always gets
the same offset — preserving cross-category consistency.

Run:  python3 add_decimal_variation.py
"""

import pandas as pd
import hashlib

CSV_PATH = '/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv'
SAMPLE_SIZE = 10_000_000
US_POP = 329_900_000

SKIP_CATEGORIES = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
    'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
    'OCCUPATION', 'LOCATION', 'BRAND CATEGORY', 'GEN POP',
    'MOST PURCHASED CATEGORIES',
}


def deterministic_offset(brand_name: str, base_pct: float) -> float:
    """
    Generate a small, deterministic decimal offset from the brand name.
    The offset is proportional to the base value so small values get
    small offsets and large values get appropriately scaled offsets.
    """
    h = hashlib.md5(brand_name.encode('utf-8')).hexdigest()

    # Use different parts of the hash for different decimal digits
    d1 = int(h[0:4], 16) % 10000  # 0-9999
    fraction = d1 / 10000.0        # 0.0000 - 0.9999

    if base_pct >= 10.0:
        # Large values: offset ±0.50 around the base
        offset = (fraction - 0.5) * 1.0
    elif base_pct >= 1.0:
        # Medium values: offset ±0.25
        offset = (fraction - 0.5) * 0.5
    elif base_pct >= 0.1:
        # Small values: offset ±0.05
        offset = (fraction - 0.5) * 0.1
    else:
        # Tiny values: offset ±0.01
        offset = (fraction - 0.5) * 0.02

    return offset


def needs_variation(pct: float) -> bool:
    """Check if a value looks too round (whole number, .5, .25, etc.)"""
    if pct == round(pct, 0):
        return True
    if pct == round(pct, 1):
        return True
    # Check for .25, .50, .75 patterns
    frac = pct - int(pct)
    if frac in (0.25, 0.50, 0.75, 0.5):
        return True
    return False


def main():
    print(f"Reading CSV: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"  {len(df)} rows loaded")

    # First pass: build master lookup of brand → new_pct
    # so we apply the same variation everywhere a brand appears
    brand_new_pct: dict[str, float] = {}

    modified = 0

    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue

        val = str(row.get('Value', '')).strip().upper()

        try:
            pct = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', ''))
        except (ValueError, TypeError):
            continue

        if not needs_variation(pct):
            continue

        # Check if we already computed a new value for this brand
        if val in brand_new_pct:
            new_pct = brand_new_pct[val]
        else:
            offset = deterministic_offset(val, pct)
            new_pct = pct + offset
            new_pct = max(new_pct, 0.0001)
            new_pct = round(new_pct, 4)

            # Make sure it doesn't accidentally round to a whole number
            if new_pct == round(new_pct, 0):
                new_pct += 0.0137
                new_pct = round(new_pct, 4)

            brand_new_pct[val] = new_pct

        new_raw = int(round((new_pct / 100.0) * SAMPLE_SIZE))
        new_genpop = int(round((new_raw / SAMPLE_SIZE) * US_POP))

        df.at[idx, 'Brand Penetration (Row)'] = new_pct
        df.at[idx, 'Original Raw Numbers'] = new_raw
        df.at[idx, 'US Gen Pop Projection'] = new_genpop
        modified += 1

    print(f"  {modified} values given decimal variation ({len(brand_new_pct)} unique brands)")

    # Recalculate Category Share for all categories
    for cat in df['Column'].unique():
        cat_upper = str(cat).strip().upper()
        if cat_upper in SKIP_CATEGORIES:
            continue
        mask = df['Column'] == cat
        raws = []
        for i in df.loc[mask].index:
            try:
                r = int(float(str(df.at[i, 'Original Raw Numbers']).replace(',', '')))
            except (ValueError, TypeError):
                r = 0
            raws.append((i, r))
        total = sum(r for _, r in raws)
        if total > 0:
            for i, r in raws:
                df.at[i, 'Category Share'] = round((r / total) * 100.0, 4)

    print("  Category Share recalculated")

    # Verify: no more round values
    round_remaining = 0
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue
        try:
            pct = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', ''))
        except:
            continue
        if pct == round(pct, 0):
            round_remaining += 1

    if round_remaining == 0:
        print("  ✅ No whole-number values remaining")
    else:
        print(f"  ⚠️ {round_remaining} whole-number values remaining")

    # Verify cross-category consistency
    from collections import defaultdict
    val_pcts = defaultdict(set)
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue
        val = str(row.get('Value', '')).strip().upper()
        try:
            pct = round(float(str(row.get('Brand Penetration (Row)', 0)).replace(',', '')), 4)
        except:
            continue
        val_pcts[val].add(pct)

    inconsistent = sum(1 for v, pcts in val_pcts.items() if len(pcts) > 1)
    if inconsistent == 0:
        print("  ✅ All values consistent across categories")
    else:
        print(f"  ⚠️ {inconsistent} values inconsistent across categories")

    df.to_csv(CSV_PATH, index=False)
    print(f"\n  Saved to {CSV_PATH}")
    print(f"  Done!")


if __name__ == '__main__':
    main()
