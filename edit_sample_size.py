#!/usr/bin/env python3
"""
================================================================================
                    SAMPLE SIZE / UNIVERSE EDITOR
================================================================================

Two ways to use this:

1) INTERACTIVE (original behavior, unchanged):
     python3 edit_sample_size.py
     → prompts for CSV path + new sample size, updates in place.

2) NON-INTERACTIVE (added 2026-07-21 so batch fixes are scriptable):
     python3 edit_sample_size.py \
         --file /path/to/Gen_Pop_2023.csv \
         --sample-size 10000000 \
         --us-pop 321010000

     Both --sample-size and --us-pop are optional:
       * --sample-size default = keep whatever the file already has
         (looked up from BRAND INPUT row's Original Raw Numbers cell)
       * --us-pop default = 329_900_000 (2026 US total). Pass a
         year-specific value when re-basing older Gen Pop cuts:
             2023 → 321_010_000
             2024 → 323_400_000
             2025 → 324_770_000
             2026 → 329_900_000

     Use --yes to skip the confirmation prompt (batch mode).

What the script does, either way:
  * Sets BRAND INPUT Original Raw Numbers = new_sample_size
  * Sets SAMPLE SIZE Category Share = new_sample_size
  * Recalculates every non-metadata row's Original Raw Numbers from
        Brand Penetration % × new_sample_size / 100
    (skips BRAND INPUT, SAMPLE SIZE, INPUT_METADATA, AVID FAN, CASUAL FAN)
  * Renormalizes Category Share within each Column
  * Recalculates US Gen Pop Projection using
        (Original Raw Numbers / 10_000_000) × us_pop
    (per Rule #3a in profile-iq-pipeline-rules — the 10M denominator is
     a fixed virtual-panel size; us_pop is the year-specific US total.)

Rows in the file are NOT sorted here. Callers who edit a dashboard-inputs
file should follow up with scripts/sort_rows_by_category_bp.sort_and_upload
per Rule #11.
================================================================================
"""

import argparse
import os
import sys

import pandas as pd


# 2026 US total population. Callers should override via --us-pop when
# operating on an older Gen Pop cut.
DEFAULT_US_POP = 329_900_000
# Fixed virtual-panel denominator per Rule #3a. Do not confuse with
# the file's actual SAMPLE SIZE (which can differ, e.g. per-subject
# audience counts).
PANEL_DENOM = 10_000_000

# Rows whose Original Raw Numbers / Category Share should NOT be
# recomputed from BP × sample. These are metadata / anchor rows.
SKIP_COLUMNS_RAW = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE',
    'AVID FAN', 'CASUAL FAN',
}
# Rows to skip when recomputing US Gen Pop Projection. BRAND INPUT is
# INCLUDED here so its projection also reflects the new universe.
SKIP_COLUMNS_PROJ = {'INPUT_METADATA', 'SAMPLE SIZE'}


def _parse_int(val, default=None):
    try:
        return int(str(val).replace(',', '').strip())
    except (ValueError, TypeError, AttributeError):
        return default


def _get_current_sample_size(df):
    """Read the sample size out of BRAND INPUT's Original Raw Numbers cell."""
    if 'Column' not in df.columns or 'Original Raw Numbers' not in df.columns:
        return None
    mask = df['Column'].astype(str).str.upper() == 'BRAND INPUT'
    if not mask.any():
        return None
    raw = df.loc[mask, 'Original Raw Numbers'].values[0]
    return _parse_int(raw)


def update_sample_size_cells(df, new_sample_size):
    """Set the sample-size cells on the BRAND INPUT and SAMPLE SIZE rows.

    NOTE: the older version of this script (see worktree) wrote the sample
    size into SAMPLE SIZE.Category Share, which corrupted that cell from
    "100.0000%" to an integer. That was a latent bug — SAMPLE SIZE always
    represents 100% of itself. We now write to the sample-size-bearing
    cells only:
       * BRAND INPUT .Original Raw Numbers = new_sample_size
       * SAMPLE SIZE .Value                = new_sample_size
       * SAMPLE SIZE .Original Raw Numbers = new_sample_size (int, no comma
         string so it round-trips through pandas cleanly)
       * SAMPLE SIZE .Category Share       = LEFT ALONE ('100.0000%')
    """
    print(f"\n[sample-cells] Setting sample-size anchor cells -> {new_sample_size:,}")
    col_upper = df['Column'].astype(str).str.upper()
    bi_mask = col_upper == 'BRAND INPUT'
    if bi_mask.any():
        df.loc[bi_mask, 'Original Raw Numbers'] = new_sample_size
    ss_mask = col_upper == 'SAMPLE SIZE'
    if ss_mask.any():
        if 'Value' in df.columns:
            df.loc[ss_mask, 'Value'] = new_sample_size
        df.loc[ss_mask, 'Original Raw Numbers'] = new_sample_size
        # Category Share on SAMPLE SIZE stays at whatever it was (typically
        # "100.0000%"). Do NOT overwrite - see docstring above.
    return df


def recalculate_raw_numbers(df, new_sample_size):
    """Recompute Original Raw Numbers = BP% × new_sample_size / 100."""
    print(f"[raw-recalc]  Recomputing Original Raw Numbers from Brand Penetration ...")
    rows = 0
    for idx in df.index:
        col_val = str(df.at[idx, 'Column']).upper()
        if col_val in SKIP_COLUMNS_RAW:
            continue
        pen = df.at[idx, 'Brand Penetration (Row)']
        if isinstance(pen, str):
            pen = pen.replace('%', '').replace(',', '').strip()
        try:
            pen = float(pen)
        except (ValueError, TypeError):
            continue
        df.at[idx, 'Original Raw Numbers'] = int(round((pen / 100.0) * new_sample_size))
        rows += 1
    print(f"              -> {rows} rows updated")
    return df


def recalculate_category_share(df):
    """Category Share = (row raw / sum(raw in same Column)) × 100, 4dp."""
    print("[cat-share]   Recomputing Category Share within each Column ...")
    cats_done = 0
    for cat in df['Column'].unique():
        if str(cat).upper() in SKIP_COLUMNS_RAW:
            continue
        mask = df['Column'] == cat
        indices = df[mask].index.tolist()
        if not indices:
            continue
        total = 0.0
        valid = []
        for i in indices:
            raw = df.at[i, 'Original Raw Numbers']
            if isinstance(raw, str):
                raw = raw.replace(',', '').strip()
            try:
                r = float(raw)
            except (ValueError, TypeError):
                continue
            total += r
            valid.append((i, r))
        if total == 0:
            continue
        for i, r in valid:
            df.at[i, 'Category Share'] = round((r / total) * 100.0, 4)
        cats_done += 1
    print(f"              -> {cats_done} categories updated")
    return df


def recalculate_genpop_projection(df, us_pop):
    """US Gen Pop Projection = (raw / PANEL_DENOM) × us_pop.

    us_pop is a per-call value; earlier versions hardcoded 324_700_000
    which was wrong for anything but the 2024 cut."""
    print(f"[proj-recalc] Recomputing US Gen Pop Projection with universe {us_pop:,}")
    rows = 0
    for idx in df.index:
        col_val = str(df.at[idx, 'Column']).upper()
        if col_val in SKIP_COLUMNS_PROJ:
            continue
        raw = df.at[idx, 'Original Raw Numbers']
        if isinstance(raw, str):
            raw = raw.replace(',', '').strip()
        try:
            r = float(raw)
        except (ValueError, TypeError):
            continue
        df.at[idx, 'US Gen Pop Projection'] = int(round((r / PANEL_DENOM) * us_pop))
        rows += 1

    # SAMPLE SIZE's projection cell also needs to reflect the new universe
    # (it's the "US" column value the dashboard renders in the comparison
    # header). We compute it separately since it's skipped above.
    ss_mask = df['Column'].astype(str).str.upper() == 'SAMPLE SIZE'
    if ss_mask.any():
        df.loc[ss_mask, 'US Gen Pop Projection'] = us_pop

    print(f"              -> {rows} rows + SAMPLE SIZE cell set to {us_pop:,}")
    return df


def load_csv(csv_path):
    print(f"\n[load] {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"       {len(df)} rows")
    return df


def save_csv(df, csv_path):
    print(f"[save] {csv_path}")
    # Canonical column order first, other columns after.
    order = ['Column', 'Value', 'Brand Penetration (Row)', 'Category Share',
             'Original Raw Numbers', 'US Gen Pop Projection']
    existing = [c for c in order if c in df.columns]
    other = [c for c in df.columns if c not in order]
    df = df[existing + other]
    df.to_csv(csv_path, index=False)


def apply_edits(df, sample_size, us_pop):
    """Full pipeline: sample cells -> raw -> cat share -> projection."""
    df = update_sample_size_cells(df, sample_size)
    df = recalculate_raw_numbers(df, sample_size)
    df = recalculate_category_share(df)
    df = recalculate_genpop_projection(df, us_pop)
    return df


# ---------------- INTERACTIVE MODE (backward compatible) ---------------- #

def _interactive_main():
    print("\n" + "=" * 70)
    print("              SAMPLE SIZE EDITOR SCRIPT")
    print("=" * 70 + "\n")

    while True:
        csv_path = input("Enter the path to the CSV file to edit: ").strip()
        csv_path = os.path.expanduser(csv_path)
        if not os.path.exists(csv_path):
            print(f"  File not found: {csv_path}\n")
            continue
        if not csv_path.lower().endswith('.csv'):
            print("  Must be a .csv file\n")
            continue
        break

    while True:
        raw = input("\nEnter the requested sample size (e.g., 10000000 or 10,000,000): ").strip()
        try:
            sample_size = int(raw.replace(',', ''))
            if sample_size <= 0:
                print("  Must be positive.\n")
                continue
            break
        except ValueError:
            print("  Not a valid integer.\n")
            continue

    us_pop_raw = input(f"US population universe (default {DEFAULT_US_POP:,}): ").strip()
    us_pop = _parse_int(us_pop_raw, default=DEFAULT_US_POP)

    print("\n-------------------------------------------------")
    print(f"  File:     {csv_path}")
    print(f"  Sample:   {sample_size:,}")
    print(f"  Universe: {us_pop:,}")
    print("-------------------------------------------------")

    if input("\nProceed? (yes/no): ").strip().lower() not in ('y', 'yes'):
        print("Cancelled.")
        return

    df = load_csv(csv_path)
    df = apply_edits(df, sample_size, us_pop)
    save_csv(df, csv_path)
    print("\nDone.\n")


# ---------------- CLI MODE ---------------- #

def _cli_main(args):
    csv_path = os.path.expanduser(args.file)
    if not os.path.exists(csv_path):
        print(f"  File not found: {csv_path}", file=sys.stderr)
        sys.exit(2)

    df = load_csv(csv_path)

    sample_size = args.sample_size
    if sample_size is None:
        current = _get_current_sample_size(df)
        if current is None or current <= 0:
            print("  Could not read current sample size from BRAND INPUT row.", file=sys.stderr)
            print("  Pass --sample-size explicitly.", file=sys.stderr)
            sys.exit(2)
        sample_size = current
        print(f"[sample-cells] --sample-size not given, keeping current {sample_size:,}")

    us_pop = args.us_pop if args.us_pop is not None else DEFAULT_US_POP

    print("\n-------------------------------------------------")
    print(f"  File:     {csv_path}")
    print(f"  Sample:   {sample_size:,}")
    print(f"  Universe: {us_pop:,}")
    print("-------------------------------------------------")

    if not args.yes:
        if input("\nProceed? (yes/no): ").strip().lower() not in ('y', 'yes'):
            print("Cancelled.")
            return

    df = apply_edits(df, sample_size, us_pop)
    save_csv(df, csv_path)
    print("\nDone.\n")


def main():
    parser = argparse.ArgumentParser(
        description="Edit sample size / US-population universe on a profile-style CSV.",
        add_help=True,
    )
    parser.add_argument('--file', help='Path to CSV. Omit for interactive mode.')
    parser.add_argument('--sample-size', type=lambda s: int(s.replace(',', '')),
                        default=None,
                        help='New sample size (BRAND INPUT raw). Default: keep current.')
    parser.add_argument('--us-pop', type=lambda s: int(s.replace(',', '')),
                        default=None,
                        help=f'US population universe. Default: {DEFAULT_US_POP:,}')
    parser.add_argument('--yes', action='store_true',
                        help='Skip the confirmation prompt (batch mode).')
    args = parser.parse_args()

    try:
        if args.file:
            _cli_main(args)
        else:
            _interactive_main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(1)


if __name__ == "__main__":
    main()
