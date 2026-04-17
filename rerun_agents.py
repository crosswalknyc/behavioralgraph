#!/usr/bin/env python3
"""Rerun the 3-step AI agent pipeline on existing profile CSVs."""

import os, sys, time

# Setup environment
from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import bg

FILES = [
    ("/Users/jennamenking/Downloads/Laura_Dern_04_17_2026_18_21.csv", "Laura Dern", "ACTOR"),
    ("/Users/jennamenking/Downloads/Sean_Kaufman_04_17_2026_18_19.csv", "Sean Kaufman", "ACTOR"),
]

for filepath, subject, brand_category in FILES:
    print(f"\n{'='*70}")
    print(f"  REPROCESSING: {subject} ({filepath})")
    print(f"{'='*70}\n")

    df = pd.read_csv(filepath)

    # Rename columns to match pipeline expectations
    col_map = {}
    for c in df.columns:
        if 'Brand Penetration' in c:
            col_map[c] = 'Brand Penetration (Row)'
        elif 'Category Share' in c:
            col_map[c] = 'Category Share'
        elif 'Original Raw' in c:
            col_map[c] = 'Original Raw Numbers'
        elif 'US Gen Pop' in c or 'Gen Pop' in c:
            col_map[c] = 'US Gen Pop Projection'
    if col_map:
        df = df.rename(columns=col_map)

    print(f"Loaded {len(df)} rows, columns: {list(df.columns)}")

    # Extract brands from BRAND INPUT row
    brands = []
    bi_mask = df['Column'].astype(str).str.strip().str.upper() == 'BRAND INPUT'
    if bi_mask.any():
        raw = str(df.loc[bi_mask, 'Value'].iloc[0])
        brands = [b.strip() for b in raw.split(',') if b.strip()][:5]
    print(f"Brands (first 5): {brands}")

    # Get sample size
    ss_mask = df['Column'].astype(str).str.strip().str.upper() == 'SAMPLE SIZE'
    sample_size = 50000
    if ss_mask.any():
        try:
            sample_size = int(float(str(df.loc[ss_mask, 'Original Raw Numbers'].iloc[0]).replace(',', '')))
        except Exception:
            pass
    print(f"Sample size: {sample_size}")

    start = time.time()

    # Step 1: Persona Research Agent
    print(f"\n--- Step 1: Persona Research Agent for '{subject}' ---")
    persona_doc = bg.persona_research_agent(subject, brand_category)
    print(f"Persona summary: {persona_doc.get('persona_summary', '')[:200]}...")
    demos = persona_doc.get('demographics', {})
    if 'ETHNICITY' in demos:
        print(f"Ethnicity from persona: {demos['ETHNICITY']}")
    if 'AGE' in demos:
        print(f"Age from persona: {demos['AGE']}")

    # Step 2: Parallel Category Agents
    print(f"\n--- Step 2: Parallel Category Agents ---")
    df = bg.parallel_category_agents(df, persona_doc, subject, brands)

    # Step 3: Final Sanity Check
    print(f"\n--- Step 3: Final Sanity Check ---")
    df = bg.agent_pipeline_final_sanity_check(df, brands)

    # Ensure 210 DMAs
    if hasattr(bg, 'enforce_exact_210_dmas'):
        df = bg.enforce_exact_210_dmas(df)

    elapsed = time.time() - start
    print(f"\n✅ Pipeline complete in {elapsed:.1f}s")

    # Save
    out_path = filepath.replace('.csv', '_FIXED.csv')
    df.to_csv(out_path, index=False)
    print(f"💾 Saved to: {out_path}")

    # Print a quick sanity check on key values
    print(f"\n--- Quick sanity check for {subject} ---")
    for cat in ['ETHNICITY', 'EVENTS', 'BANKING']:
        mask = df['Column'].astype(str).str.strip().str.upper() == cat
        if mask.any():
            subset = df.loc[mask, ['Value', 'Brand Penetration (Row)']].head(5)
            print(f"\n  {cat} (top 5):")
            for _, row in subset.iterrows():
                print(f"    {row['Value']}: {row['Brand Penetration (Row)']}%")

print(f"\n{'='*70}")
print("  ALL DONE")
print(f"{'='*70}")
