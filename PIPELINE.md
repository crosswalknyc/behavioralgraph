# Behavioral Graph — Profile Analysis Pipeline

This document describes the end-to-end processing pipeline that runs when a profile analysis is submitted (e.g., "YouTube Viewers").

---

## Overview

```
Snowflake raw data
  → Sort & format
  → Consistency passes
  → Anchor to Gen Pop
  → AI demographic validation
  → AI behavioral gut-check
  → Cross-category consistency
  → Brand Penetration recalculation
  → Final behavioral sanity check
  → Save CSV → S3
```

---

## Pipeline Stages (in execution order)

### 1. Snowflake Query & Raw Data Collection

The pipeline connects to Snowflake and queries behavioral and demographic data for the input brand/search terms. This produces raw counts of unique users who engaged with each value (e.g., how many YouTube viewers also visited Netflix).

**Key outputs:** Raw user counts per value, organized by category (AGE, GENDER, INTEREST, STREAMING/PLATFORM, etc.)

### 2. Sample Size Determination

The profile's sample size is set by looking up the input brand in the Gen Pop CSV:

- If the brand is found (e.g., YouTube at 84.13% Brand Penetration), sample size = `BP% × 10,000,000` = 8,412,970
- If not found, an intelligent estimate is made based on category and digital engagement level (digital brands get higher estimates than offline-first brands)

### 3. Initial Percentage & Raw Number Calculation

Raw Snowflake counts are converted to percentages (Category Share) within each category. `Original Raw Numbers` are scaled to match the determined sample size.

### 4. Pre-Anchor Formatting

Several formatting and consistency passes run before the main anchoring:

- **DMA/Location population**: Ensures all 210+ US DMAs are present in the LOCATION category
- **Cross-category consistency**: If a brand appears in multiple categories (e.g., ESPN in BROADCAST/CABLE and STREAMING/PLATFORM), its `Percentage` and `Original Raw Numbers` are unified to the highest value across categories
- **Input brand enforcement**: The input brand (e.g., YOUTUBE) is set to 100% / sample size in all categories where it appears
- **Streaming platform cleanup**: Normalizes platform names (e.g., "STREAMING MAX" → "HBO MAX")
- **Brand Penetration calculation**: `BP = (Original Raw Numbers / Sample Size) × 100`
- **US Gen Pop Projection**: `Projection = BP% × 324,700,000`

### 5. Anchor to Gen Pop (Core Calibration)

**This is the central calibration step.** For each behavioral row, the pipeline compares the profile's value against the calibrated Gen Pop baseline and constrains it using an index-and-clamp approach.

**How it works:**

1. Load the master Gen Pop CSV (`Gen_Pop_2026_03_04_2026_04_29.csv`) from S3
2. For each behavioral row, compute `raw_index = profile_category_share / genpop_category_share`
3. Apply **Bayesian shrinkage**: blend the raw index toward 1.0 when underlying sample counts are small (< 500 raw), increasing trust only when data is sufficient
4. Apply **adaptive bounds** based on the profile's overall Gen Pop penetration:
   - High-penetration profiles (YouTube at 84%) get **tight bounds** [0.98, 1.05] — they should closely mirror Gen Pop
   - Niche profiles (e.g., a brand at 5% penetration) get **wide bounds** [0.37, 2.80] — they can deviate significantly
   - Formula: `niche_factor = (1 - profile_BP/100)²`
5. Clamp the index to bounds, then: `new_BP = GenPop_BP × clamped_index`
6. Recalculate `Original Raw Numbers` and `US Gen Pop Projection` from the new BP

**Demographics are NOT anchored** — raw Snowflake data passes through to preserve nuances (e.g., The Rock having a larger male/Latino audience than Gen Pop). Demographics are validated separately by the AI step.

**Skipped categories:** INPUT_METADATA, BRAND INPUT, SAMPLE SIZE, AVID FAN, CASUAL FAN, BRAND CATEGORY

### 6. AI-Powered Demographic Validation

An audience archetype is generated (from existing S3 profiles or GPT-4o-mini) describing the expected demographic profile:

- `gender_skew`: male / female / balanced
- `age_skew`: younger / older / balanced
- `ethnicity_over_index`: which ethnic groups might over-index

**Age reshaping:** If the archetype says "younger" but the top-2 age groups include 65+ or 55-64, directional multipliers are applied:
| Age Group | Younger Multiplier | Older Multiplier |
|-----------|-------------------|-----------------|
| 18-24     | 1.50×             | 0.70×           |
| 25-34     | 1.40×             | 0.85×           |
| 35-44     | 1.00×             | 1.00×           |
| 45-54     | 0.80×             | 1.15×           |
| 55-64     | 0.65×             | 1.30×           |
| 65+       | 0.45×             | 1.40×           |

**Gender reshaping:** If the disfavored gender exceeds the favored one, moderate multipliers (1.15× / 0.85×) correct the imbalance.

**Ethnicity:** Light-touch dampening only — unexpected ethnicities exceeding 2× Gen Pop are blended back.

All categories are renormalized to 100% after reshaping.

### 7. Census Ceiling Constraint

**Critical constraint:** After demographic reshaping, each demographic group's projected US Gen Pop number must not exceed the actual number of people in that group.

For example, if the profile has 8.4M sample (projecting to ~272.7M US viewers):
- If 28% are aged 18-24, that projects to 76.4M — but only 29.2M 18-24 year-olds exist in the US
- The percentage is capped so the projection equals the census limit (29.2M / 272.7M = 10.7%)
- The category is then renormalized to maintain 100% total

This ensures the data is true to the underlying patterns while staying within real-world population limits.

### 8. AI-Powered Behavioral Gut-Check

The archetype's `behavioral_high` and `behavioral_low` category lists adjust the acceptable index range per category. For example:
- YouTube viewers should have higher SOCIAL MEDIA, STREAMING, GAMING → minimum index raised to 0.8
- YouTube viewers might have lower HEAVY MACHINERY → maximum index lowered to 1.1

Values outside the adjusted bounds are clamped, and category shares are recalculated.

### 9. Post-Anchor Consistency

- **Cross-category brand consistency**: Re-enforces that the same brand has the same highest Percentage and Raw Numbers across all categories
- **Input brand 100%**: Re-enforces the input brand at 100%
- **Parental status normalization**: Ensures parental status sums to 100%

### 10. Final Brand Penetration Recalculation

`add_brand_penetration_column_using_final_raw()` recalculates `Brand Penetration (Row)` from the final `Original Raw Numbers`:

```
BP = (Original Raw Numbers / Sample Size) × 100
```

This runs AFTER all consistency passes, so it reflects any changes made by cross-category enforcement.

### 11. Final Behavioral Sanity Check (Last Gate)

**The absolute last behavioral validation.** This catches anything that slipped through the pipeline (e.g., cross-category consistency inflating values back up after anchoring).

For every behavioral row:
1. Compare FINAL `Brand Penetration (Row)` against Gen Pop
2. If `profile_BP / genpop_BP` exceeds adaptive bounds, clamp it
3. Recalculate `Category Share`, `Original Raw Numbers`, and `US Gen Pop Projection`

This ensures no behavioral value can escape the pipeline with an unreasonable index, regardless of what intermediate steps did to it.

### 12. Output Formatting & Save

- Categories sorted by predefined order (demographics first, then behavioral alphabetically)
- Within each category, values sorted by percentage descending
- Column renamed: `Percentage` → `Category Share`
- Final column order: `Column, Value, Brand Penetration (Row), Category Share, Original Raw Numbers, US Gen Pop Projection`
- Saved to CSV and uploaded to S3 (`purgatory/` prefix for review, then moved to root)

---

## Key Concepts

### Brand Penetration (Row)
The percentage of the profile's audience that engaged with a given value. For YouTube Viewers: "What % of YouTube viewers also use Netflix?"

### Category Share
Within a single category, how the audience is distributed. All values in a category sum to 100%.

### Original Raw Numbers
The estimated number of panelists (out of sample size) who engaged with the value.

### US Gen Pop Projection
The estimated number of Americans represented by this value: `BP% × 324,700,000`.

### Adaptive Index Bounds
The allowable deviation from Gen Pop, calculated from profile penetration. High-penetration profiles stay close to Gen Pop; niche profiles can deviate significantly. This prevents YouTube (84% of panel) from showing wildly different values than Gen Pop, while allowing a niche brand (5% of panel) to have distinct characteristics.

### Census Ceiling
A hard constraint ensuring demographic projections never exceed real-world population limits. Even if the underlying data shows 28% of an audience is aged 18-24, the projection to the full US population must not exceed the actual number of 18-24 year-olds (~29.2M).
