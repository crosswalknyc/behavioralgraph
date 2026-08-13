"""Canonical demographics schema for Attribution IQ campaign audiences.

Source of truth: /Users/jennamenking/Downloads/demos.csv (curated canonical
list Jenna maintains). This module is the ONLY place where category
names + bucket lists live; the reasoning agent, backend endpoint, and
frontend all read from here so we can never drift.

Rules (mirrors the BPIQ demographics convention in
`.cursor/rules/bpiq-conventions.mdc` section 4):
  * Every demographics block must use these exact labels.
  * Every bucket in a category must be present in the output (even
    when the reasoning agent doesn't allocate mass to it — floor to
    ~0.6% and renormalize).
  * Every category sums to 100.0 (± 0.05 rounding).
  * Never invent new categories or buckets in downstream code; if a
    new bucket is needed, add it here first + surface to Jenna.

Row_Count in the source CSV is the US-panel size for that bucket
(Behavioral Graph population). We keep it as a US-baseline weight so
the reasoning agent can lean toward the population-shape when the
asset gives no strong signal, and lean AWAY when the content clearly
skews (e.g. TikTok reels featuring Lindsay Lohan skew younger + more
female than the US baseline; the agent must return that skew).

Public API:
  DEMO_SCHEMA          - ordered dict of category -> [bucket, ...]
  DEMO_US_BASELINE     - category -> {bucket: pct_of_US_adults}
  DEMO_CATEGORY_LABELS - category -> pretty-printed label
  MIN_BUCKET_FLOOR_PCT - minimum % floor per bucket (0.6)
  normalize_distribution(cat, dist) - snap a distribution to schema + sum-100
"""

# =====================================================================
# CANONICAL LOCATIONS (both copies MUST be byte-identical):
#   1. /root/finished_codes/migration/attribution_demographics_schema.py
#      (used by scripts/build_intent_*.py + Hetzner runs)
#   2. /root/finished_codes/bg-webapp/migration/attribution_demographics_schema.py
#      (used by the Render Flask worker for dashboard-triggered
#       Attribution IQ ingests, so the reasoning + numbers match
#       whether Jenna hand-runs it or a user submits the Analysis
#       IQ form.)
# Edit BOTH copies + run scripts/verify_attribution_agents_parity.py
# =====================================================================
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List


# The category order below is what the frontend renders top-to-bottom.
# Chosen to lead with the highest-signal cuts (AGE, GENDER, ETHNICITY)
# and end with the more niche cuts.
DEMO_SCHEMA: "OrderedDict[str, List[str]]" = OrderedDict([
    ("AGE", [
        "17 and Under", "18-24", "25-34", "35-44", "45-54",
        "55-64", "65 or Older",
    ]),
    ("GENDER", [
        "Female", "Male", "Non-Binary", "Trans Female", "Trans Male",
        "Prefer Not to Say",
    ]),
    ("ETHNICITY", [
        "White", "Hispanic or Latino", "Black or African American",
        "Asian", "Another Race/Ethnicity",
    ]),
    ("INCOME", [
        "Less than $25,000", "$25,000 - $49,999", "$50,000 - $74,999",
        "$75,000 - $99,999", "$100,000 - $149,999",
        "$150,000 - $249,999", "$250,000 or More",
    ]),
    ("EDUCATION", [
        "High School or Less", "Some College / Associate Degree",
        "Bachelors Degree", "Graduate or Professional Degree",
        "Prefer Not to Say",
    ]),
    ("PARENTAL_STATUS", [
        "No Children", "Has Children", "Prefer Not to Say",
    ]),
    ("NUMBER_OF_CHILDREN", [
        "0", "1", "2", "3", "4+",
    ]),
    ("AGE_OF_CHILDREN", [
        "No Kids", "Under 3", "3 to 5", "6 to 10", "11 to 13", "14 to 17",
    ]),
    ("RELATIONSHIP", [
        "Single", "In a Relationship", "Married",
        "Divorced or Separated", "Widowed", "Prefer Not to Say",
    ]),
    ("SEXUAL_ORIENTATION", [
        "Straight / Heterosexual", "Gay or Lesbian", "LGBTQ+",
        "Another Sexual Orientation", "Prefer Not to Say",
    ]),
    ("OCCUPATION", [
        "Management, Business & Professional",
        "Healthcare Practitioners or Support",
        "Sales & Retail",
        "Education or Library Services",
        "Service & Hospitality",
        "Science, Technology & Technical Professions",
        "Skilled Trades/Construction or Maintenance",
        "Agriculture & Outdoor",
        "Transportation & Logistics",
        "Manufacturing & Production",
        "Public Safety & Protective Services",
        "Legal",
        "Other",
    ]),
    ("PRIMARY_LANGUAGE", [
        "English", "Spanish", "Chinese", "Other",
    ]),
])


# US-baseline % of adults per bucket, computed from demos.csv Row_Count
# with the empty / typo-variant rows (e.g. "$75,000 – $99,999" with
# en-dash) filtered out. Populations are Behavioral Graph panel counts,
# treated as the best available proxy for US adults.
DEMO_US_BASELINE: Dict[str, Dict[str, float]] = {
    "AGE": {
        "17 and Under":  8.32,
        "18-24":        17.09,
        "25-34":        28.44,
        "35-44":        17.07,
        "45-54":        11.05,
        "55-64":         6.86,
        "65 or Older":   3.81,
        # 3,362,381 "Other" ages spread proportionally (~9.36%) across
        # the named buckets when we run normalization; we don't render
        # an "Other" bucket in the schema.
    },
    "GENDER": {
        "Female":              47.53,
        "Male":                43.37,
        "Prefer Not to Say":    3.88,
        "Trans Male":           2.11,
        "Trans Female":         1.61,
        "Non-Binary":           1.54,
    },
    "ETHNICITY": {
        "White":                        63.90,
        "Another Race/Ethnicity":       12.87,
        "Hispanic or Latino":           10.61,
        "Black or African American":     7.76,
        "Asian":                         5.44,
    },
    "INCOME": {
        "Less than $25,000":        0.60,
        "$25,000 - $49,999":        0.60,
        "$50,000 - $74,999":       27.29,
        "$75,000 - $99,999":       23.42,
        "$100,000 - $149,999":     22.12,
        "$150,000 - $249,999":     15.66,
        "$250,000 or More":        11.95,
        # NOTE: the Behavioral Graph panel massively over-indexes on
        # $50K+ vs the true US household distribution (which has ~10%
        # < $25K, ~15% $25-49K). We keep the panel shape as the baseline
        # because that's what the rest of the pipeline uses, but the
        # floors on the low-income buckets prevent them from ever
        # rendering as 0 — see MIN_BUCKET_FLOOR_PCT.
    },
    "EDUCATION": {
        "Bachelors Degree":                    63.87,
        "High School or Less":                 30.51,
        "Graduate or Professional Degree":      4.80,
        "Prefer Not to Say":                    0.68,
        "Some College / Associate Degree":      0.55,
    },
    "RELATIONSHIP": {
        "Single":                 33.19,
        "In a Relationship":      24.13,
        "Married":                22.00,
        "Prefer Not to Say":      13.10,
        "Divorced or Separated":   9.32,
        "Widowed":                 0.60,
    },
    "PARENTAL_STATUS": {
        "No Children":         54.86,
        "Has Children":        29.85,
        "Prefer Not to Say":   15.29,
    },
    "OCCUPATION": {
        "Other":                                      31.87,
        "Management, Business & Professional":        23.71,
        "Healthcare Practitioners or Support":        11.11,
        "Sales & Retail":                             10.74,
        "Education or Library Services":               7.99,
        "Service & Hospitality":                       2.85,
        "Science, Technology & Technical Professions": 1.97,
        "Skilled Trades/Construction or Maintenance":  1.77,
        "Agriculture & Outdoor":                       1.66,
        "Transportation & Logistics":                  1.58,
        "Manufacturing & Production":                  1.50,
        "Public Safety & Protective Services":         1.46,
        "Legal":                                       0.60,
    },
    "NUMBER_OF_CHILDREN": {
        "0":  75.32,
        "2":  10.52,
        "1":   8.77,
        "3":   4.43,
        "4+":  0.90,
    },
    "AGE_OF_CHILDREN": {
        "No Kids":     80.16,
        "14 to 17":     6.38,
        "3 to 5":       4.45,
        "6 to 10":      3.89,
        "Under 3":      3.88,
        "11 to 13":     3.17,
    },
    "SEXUAL_ORIENTATION": {
        "Straight / Heterosexual":       74.13,
        "Prefer Not to Say":             15.21,
        "Gay or Lesbian":                10.13,
        "Another Sexual Orientation":     1.11,
        "LGBTQ+":                         0.60,
        # "Other" (5,939) is folded into "Another Sexual Orientation"
        # for the baseline; the reasoning agent should assign LGBTQ+
        # or Another based on the asset's content signals.
    },
    "PRIMARY_LANGUAGE": {
        "Other":     66.19,
        "English":   26.80,
        "Spanish":    7.35,
        "Chinese":    0.60,
    },
}


# Pretty label per category for the frontend.
DEMO_CATEGORY_LABELS: Dict[str, str] = {
    "AGE":                 "Age",
    "GENDER":              "Gender",
    "ETHNICITY":           "Ethnicity",
    "INCOME":              "Household income",
    "EDUCATION":           "Education",
    "PARENTAL_STATUS":     "Parental status",
    "NUMBER_OF_CHILDREN":  "Number of children",
    "AGE_OF_CHILDREN":     "Age of children",
    "RELATIONSHIP":        "Relationship",
    "SEXUAL_ORIENTATION":  "Sexual orientation",
    "OCCUPATION":          "Occupation",
    "PRIMARY_LANGUAGE":    "Primary language",
}


# Minimum floor per bucket so no row renders as literally 0% (matches
# BPIQ convention in .cursor/rules/bpiq-conventions.mdc section 4).
MIN_BUCKET_FLOOR_PCT = 0.60


# ---------------------------------------------------------------------------
# Normalization helper
# ---------------------------------------------------------------------------

def _fuzzy_bucket_match(cat: str, key: str) -> str:
    """Map common misspellings / aliases the LLM might return back to
    the canonical bucket label. Case-insensitive."""
    if not key:
        return ""
    buckets = DEMO_SCHEMA.get(cat, [])
    if key in buckets:
        return key
    kl = key.strip().lower().replace("–", "-").replace("—", "-")
    for b in buckets:
        if b.strip().lower() == kl:
            return b
    # Common aliases
    aliases = {
        ("AGE", "under 17"):        "17 and Under",
        ("AGE", "under 18"):        "17 and Under",
        ("AGE", "13-17"):           "17 and Under",
        ("AGE", "65+"):             "65 or Older",
        ("AGE", "18-24"):           "18-24",
        ("AGE", "25-34"):           "25-34",
        ("AGE", "35-44"):           "35-44",
        ("AGE", "45-54"):           "45-54",
        ("AGE", "55-64"):           "55-64",
        ("GENDER", "nonbinary"):    "Non-Binary",
        ("GENDER", "nb"):           "Non-Binary",
        ("GENDER", "trans man"):    "Trans Male",
        ("GENDER", "trans woman"):  "Trans Female",
        ("ETHNICITY", "hispanic"):        "Hispanic or Latino",
        ("ETHNICITY", "latino"):          "Hispanic or Latino",
        ("ETHNICITY", "latinx"):          "Hispanic or Latino",
        ("ETHNICITY", "black"):           "Black or African American",
        ("ETHNICITY", "african american"):"Black or African American",
        ("ETHNICITY", "other"):           "Another Race/Ethnicity",
        ("ETHNICITY", "mixed"):           "Another Race/Ethnicity",
        ("ETHNICITY", "native american"): "Another Race/Ethnicity",
        ("INCOME", "under $25k"):    "Less than $25,000",
        ("INCOME", "$25k-$49k"):     "$25,000 - $49,999",
        ("INCOME", "$50k-$74k"):     "$50,000 - $74,999",
        ("INCOME", "$75k-$99k"):     "$75,000 - $99,999",
        ("INCOME", "$100k-$149k"):   "$100,000 - $149,999",
        ("INCOME", "$150k-$249k"):   "$150,000 - $249,999",
        ("INCOME", "$250k+"):        "$250,000 or More",
        ("EDUCATION", "hs or less"):        "High School or Less",
        ("EDUCATION", "bachelor's"):        "Bachelors Degree",
        ("EDUCATION", "graduate"):          "Graduate or Professional Degree",
        ("EDUCATION", "some college"):      "Some College / Associate Degree",
        ("SEXUAL_ORIENTATION", "straight"): "Straight / Heterosexual",
        ("SEXUAL_ORIENTATION", "gay"):      "Gay or Lesbian",
        ("SEXUAL_ORIENTATION", "lesbian"):  "Gay or Lesbian",
    }
    return aliases.get((cat, kl), "")


def normalize_distribution(cat: str, dist: Dict[str, float]) -> Dict[str, float]:
    """Snap a category distribution to the canonical schema:

      1. Map any alias keys to canonical labels.
      2. Drop keys that don't map to a canonical bucket.
      3. Add missing canonical buckets with 0.
      4. Floor every bucket to MIN_BUCKET_FLOOR_PCT so no row is 0.
      5. Renormalize so the category sums to exactly 100.0.

    Returns an ordered dict in schema order (dict preserves insertion
    order in Python 3.7+, so callers can iterate in the intended
    render order).

    Raises ValueError if `cat` is not a canonical category.
    """
    if cat not in DEMO_SCHEMA:
        raise ValueError(f"Unknown demographic category: {cat!r}")
    buckets = DEMO_SCHEMA[cat]

    # Step 1-2: fuzzy-map and drop non-canonical keys.
    canonical: Dict[str, float] = {b: 0.0 for b in buckets}
    for raw_k, raw_v in (dist or {}).items():
        b = _fuzzy_bucket_match(cat, raw_k)
        if not b:
            continue
        try:
            canonical[b] = max(0.0, canonical[b] + float(raw_v))
        except (TypeError, ValueError):
            continue

    # Step 3-4: floor every bucket, so we always render >= floor.
    for b in buckets:
        if canonical[b] < MIN_BUCKET_FLOOR_PCT:
            canonical[b] = MIN_BUCKET_FLOOR_PCT

    # Step 5: renormalize to 100.
    total = sum(canonical.values())
    if total <= 0:
        # Degenerate: return the US baseline shape (also normalized).
        base = DEMO_US_BASELINE.get(cat, {})
        canonical = {b: max(MIN_BUCKET_FLOOR_PCT, base.get(b, 0.0)) for b in buckets}
        total = sum(canonical.values())
    scale = 100.0 / total
    return {b: round(canonical[b] * scale, 2) for b in buckets}


def blank_distribution() -> Dict[str, Dict[str, float]]:
    """Return a fresh empty distribution keyed by category, buckets 0.
    Used as an accumulator in view-weighted aggregation."""
    return {cat: {b: 0.0 for b in buckets} for cat, buckets in DEMO_SCHEMA.items()}
