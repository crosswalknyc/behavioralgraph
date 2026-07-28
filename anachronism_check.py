"""Anachronism enforcer for year-specific skins.

When we generate a `Gen_Pop_2021.csv`-style historical year skin, the
skin builder inherits every brand from the 2026 source. But many
brands / products didn't exist yet in the target year, or were still
in pre-launch obscurity. Without a time-sensitivity layer, a 2021
skin ends up carrying Wordle at ~28% (Wordle went viral Jan 2022 and
launched Oct 2021), Threads at ~10% (Threads launched Jul 2023),
ChatGPT at whatever (Nov 2022), Bluesky (2023), Sora (2024), etc.

This enforcer zeros out brands that literally did not exist in the
target year and dampens brands that existed but were still in pre-
mainstream obscurity.

BRAND_LAUNCH_YEAR table structure
---------------------------------
Each entry has:
  - `launch_year`: earliest year the product had ANY real US audience
  - `mainstream_year`: (optional) earliest year the product had a
    material US audience (viral peak, mass adoption)

For a target `year`:
  - year < launch_year         -> BP forced to jittered 0.02..0.10
  - year == launch_year        -> BP capped at min(current, 5-10% of
                                   mainstream level) depending on
                                   how late in the year the product
                                   launched
  - year in [launch_year+1, mainstream_year - 1]:
                                  BP capped at 25-40% of current
                                  (early-adoption dampening)
  - year >= mainstream_year    -> no change

If neither launch_year nor mainstream_year is provided, no adjustment
happens. This is a conservative allow-list: brands not in the table
pass through untouched.

Add entries as we encounter time-sensitive brands the year-skin
builder should be handling. Each addition is a persistent fix — the
next year-skin generation applies it automatically.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional, Tuple

import pandas as pd

CAT_COL = "Column"
VAL_COL = "Value"
BP_COL = "Brand Penetration (Row)"
CS_COL = "Category Share"
RAW_COL = "Original Raw Numbers"
PROJ_COL = "US Gen Pop Projection"


# key: normalized brand name (upper-case, alphanumeric only)
# value: dict with launch_year and optionally mainstream_year + peak
#        year multipliers for post-launch fade/rebrand dynamics.
BRAND_LAUNCH_YEAR: dict = {
    # ---- Social / communication ----
    "TIKTOK":         {"launch_year": 2018, "mainstream_year": 2020},
    "MUSICALLY":      {"launch_year": 2014, "sunset_year": 2018},
    "THREADS":        {"launch_year": 2023, "mainstream_year": 2023},
    "BLUESKY":        {"launch_year": 2023, "mainstream_year": 2024},
    "MASTODON":       {"launch_year": 2016, "mainstream_year": 2022},
    "TRUTHSOCIAL":    {"launch_year": 2022, "mainstream_year": 2022},
    "TRUTH SOCIAL":   {"launch_year": 2022, "mainstream_year": 2022},
    "CLUBHOUSE":      {"launch_year": 2020, "mainstream_year": 2021,
                       "fade_after": 2022},
    "BEREAL":         {"launch_year": 2020, "mainstream_year": 2022,
                       "fade_after": 2023},
    "LEMON8":         {"launch_year": 2020, "mainstream_year": 2023},

    # ---- Streaming / platforms ----
    "MAX":            {"launch_year": 2023, "mainstream_year": 2023},
    "PARAMOUNT+":     {"launch_year": 2021, "mainstream_year": 2021},
    "PARAMOUNTPLUS":  {"launch_year": 2021, "mainstream_year": 2021},
    "APPLETV+":       {"launch_year": 2019, "mainstream_year": 2020},
    "APPLE TV+":      {"launch_year": 2019, "mainstream_year": 2020},
    "APPLETVPLUS":    {"launch_year": 2019, "mainstream_year": 2020},
    "DISNEY+":        {"launch_year": 2019, "mainstream_year": 2020},
    "DISNEYPLUS":     {"launch_year": 2019, "mainstream_year": 2020},
    "PEACOCK":        {"launch_year": 2020, "mainstream_year": 2021},
    "DISCOVERY+":     {"launch_year": 2021, "mainstream_year": 2021,
                       "fade_after": 2023},  # merged into MAX May 2023
    "HBOMAX":         {"launch_year": 2020, "mainstream_year": 2020,
                       "sunset_year": 2023},  # rebranded to MAX May 2023
    "HBO MAX":        {"launch_year": 2020, "mainstream_year": 2020,
                       "sunset_year": 2023},
    "QUIBI":          {"launch_year": 2020, "mainstream_year": 2020,
                       "sunset_year": 2020},
    "CNN+":           {"launch_year": 2022, "sunset_year": 2022},
    "SHUDDER":        {"launch_year": 2015, "mainstream_year": 2020},

    # ---- AI / search ----
    "CHATGPT":        {"launch_year": 2022, "mainstream_year": 2023},
    "COPILOT":        {"launch_year": 2023, "mainstream_year": 2024},
    "GEMINI":         {"launch_year": 2023, "mainstream_year": 2024},
    "GOOGLEBARD":     {"launch_year": 2023, "sunset_year": 2024},
    "GOOGLE BARD":    {"launch_year": 2023, "sunset_year": 2024},
    "BARD":           {"launch_year": 2023, "sunset_year": 2024},
    "CLAUDE":         {"launch_year": 2023, "mainstream_year": 2024},
    "PERPLEXITY":     {"launch_year": 2022, "mainstream_year": 2024},
    "PERPLEXITYAI":   {"launch_year": 2022, "mainstream_year": 2024},
    "GROK":           {"launch_year": 2023, "mainstream_year": 2024},
    "SORA":           {"launch_year": 2024, "mainstream_year": 2025},
    "MIDJOURNEY":     {"launch_year": 2022, "mainstream_year": 2023},
    "DALLE":          {"launch_year": 2021, "mainstream_year": 2023},
    "DALLE2":         {"launch_year": 2022, "mainstream_year": 2023},
    "STABLEDIFFUSION": {"launch_year": 2022, "mainstream_year": 2023},

    # ---- Games ----
    "WORDLE":         {"launch_year": 2021, "mainstream_year": 2022},
    "CONNECTIONS":    {"launch_year": 2023, "mainstream_year": 2023},
    "SPELLINGBEE":    {"launch_year": 2018, "mainstream_year": 2021},
    "NYTGAMES":       {"launch_year": 2014, "mainstream_year": 2020},
    "NY TIMES GAMES": {"launch_year": 2014, "mainstream_year": 2020},
    "AMONGUS":        {"launch_year": 2018, "mainstream_year": 2020,
                       "fade_after": 2022},
    "AMONG US":       {"launch_year": 2018, "mainstream_year": 2020,
                       "fade_after": 2022},
    "PALWORLD":       {"launch_year": 2024, "mainstream_year": 2024},
    "HELLDIVERS2":    {"launch_year": 2024, "mainstream_year": 2024},
    "MARVELRIVALS":   {"launch_year": 2024, "mainstream_year": 2025},
    "MONOPOLYGO":     {"launch_year": 2023, "mainstream_year": 2023},
    "MONOPOLY GO":    {"launch_year": 2023, "mainstream_year": 2023},
    "ROYALMATCH":     {"launch_year": 2021, "mainstream_year": 2023},
    "TONYHAWKPROSKATER1PLUS2": {"launch_year": 2020},
    "TONYHAWKPROSKATER3PLUS4": {"launch_year": 2025},

    # ---- Retail / commerce ----
    "TEMU":           {"launch_year": 2022, "mainstream_year": 2023},
    "SHEIN":          {"launch_year": 2008, "mainstream_year": 2021},
    "GOAT":           {"launch_year": 2015, "mainstream_year": 2020},
    "STOCKX":         {"launch_year": 2016, "mainstream_year": 2019},
    "OZEMPIC":        {"launch_year": 2017, "mainstream_year": 2022},
    "WEGOVY":         {"launch_year": 2021, "mainstream_year": 2023},
    "MOUNJARO":       {"launch_year": 2022, "mainstream_year": 2023},
    "ZEPBOUND":       {"launch_year": 2023, "mainstream_year": 2024},

    # ---- Fintech / crypto ----
    "COINBASE":       {"launch_year": 2012, "mainstream_year": 2020},
    "BITCOIN":        {"launch_year": 2009, "mainstream_year": 2017},
    "ETHEREUM":       {"launch_year": 2015, "mainstream_year": 2020},
    "AFFIRM":         {"launch_year": 2012, "mainstream_year": 2020},
    "AFTERPAY":       {"launch_year": 2014, "mainstream_year": 2020},
    "KLARNA":         {"launch_year": 2005, "mainstream_year": 2020},
    "CHIME":          {"launch_year": 2013, "mainstream_year": 2019},
    "STARLINK":       {"launch_year": 2020, "mainstream_year": 2022},

    # ---- Vertical shorts / short-form ----
    "DRAMABOX":       {"launch_year": 2022, "mainstream_year": 2024},
    "GOODSHORT":      {"launch_year": 2022, "mainstream_year": 2024},
    "REELSHORT":      {"launch_year": 2022, "mainstream_year": 2024},

    # ---- Media / content platforms ----
    "SUBSTACK":       {"launch_year": 2017, "mainstream_year": 2021},
    "ONLYFANS":       {"launch_year": 2016, "mainstream_year": 2020},
    "PATREON":        {"launch_year": 2013, "mainstream_year": 2020},
    "KICK":           {"launch_year": 2022, "mainstream_year": 2023},
    "RUMBLE":         {"launch_year": 2013, "mainstream_year": 2021},
    "LOCKED IN":      {"launch_year": 2023, "mainstream_year": 2024},

    # ---- Other notable recent launches ----
    "APPLEVISIONPRO": {"launch_year": 2024, "mainstream_year": 2024},
    "APPLEVISION":    {"launch_year": 2024, "mainstream_year": 2024},
    "RAYBANMETA":     {"launch_year": 2023, "mainstream_year": 2024},
}


# Categories that never contain brand rows (skip)
DEMO_SKIP = {
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME",
    "OCCUPATION", "PARENTAL_STATUS", "PARENTAL STATUS",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION",
    "AVID FAN", "CASUAL FAN", "BRAND INPUT", "SAMPLE SIZE",
    "SUBJECT", "BRAND CATEGORY", "LOCATION", "INTEREST",
}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _bp(x):
    try:
        return float(str(x).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _seed_jitter(seed: str, amp: float) -> float:
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)
    return ((h / (16 ** 16)) * 2.0 - 1.0) * amp


def _sample_universe(df: pd.DataFrame):
    m = df[CAT_COL].astype(str).str.upper().str.strip() == "BRAND INPUT"
    if m.any():
        r = df[m].iloc[0]
        try:
            return (
                float(str(r[RAW_COL]).replace(",", "").strip() or 0),
                float(str(r[PROJ_COL]).replace(",", "").strip() or 0),
            )
        except Exception:
            pass
    m = df[CAT_COL].astype(str).str.upper().str.strip() == "SAMPLE SIZE"
    if m.any():
        r = df[m].iloc[0]
        try:
            return (
                float(str(r[RAW_COL]).replace(",", "").strip() or 0),
                float(str(r[PROJ_COL]).replace(",", "").strip() or 0),
            )
        except Exception:
            pass
    return None, None


def _target_bp_for_year(current_bp: float, year: int, entry: dict,
                        seed: str) -> Optional[float]:
    """Return new BP for a brand in a given year, based on the launch/
    mainstream/sunset schedule. Returns None if no adjustment needed."""
    launch = entry.get("launch_year")
    mainstream = entry.get("mainstream_year", launch)
    sunset = entry.get("sunset_year")
    fade_after = entry.get("fade_after")

    if launch is not None and year < launch:
        return round(0.02 + abs(_seed_jitter(f"{seed}|zero", 0.06)), 4)

    if sunset is not None and year > sunset:
        return round(0.05 + abs(_seed_jitter(f"{seed}|sunset", 0.10)), 4)

    if launch is not None and mainstream is not None and launch < mainstream:
        if year == launch:
            cap = min(current_bp, current_bp * 0.10)
            cap = max(cap, 0.10)
            return round(cap + _seed_jitter(f"{seed}|launchyear", 0.08), 4)
        if launch < year < mainstream:
            years_pre_mainstream = mainstream - year
            damp = 0.15 * (0.75 ** (years_pre_mainstream - 1))
            damp = max(0.05, min(0.50, damp))
            v = current_bp * damp
            v = max(v, 0.10)
            return round(v + _seed_jitter(f"{seed}|preadopt", 0.10), 4)

    if fade_after is not None and year > fade_after:
        gap = year - fade_after
        damp = 0.65 * (0.80 ** gap)
        damp = max(0.20, min(1.0, damp))
        v = current_bp * damp
        return round(v + _seed_jitter(f"{seed}|fade", 0.10), 4)

    return None


def strip_anachronistic_brands(
    df: pd.DataFrame,
    year: int,
    subject: str = "",
    *,
    verbose: bool = True,
) -> Tuple[pd.DataFrame, int]:
    """Zero out / dampen brands that literally didn't exist in
    `target_year` (or were pre-mainstream). Recomputes Raw + Proj
    for changed rows using the file's own sample/universe.

    Returns (df, n_rows_changed).
    """
    if year is None:
        return df, 0
    df = df.copy()
    sample, universe = _sample_universe(df)
    n_changed = 0
    changes = []

    for idx, row in df.iterrows():
        cat = str(row.get(CAT_COL, "")).strip().upper()
        if cat in DEMO_SKIP:
            continue
        val = str(row.get(VAL_COL, "")).strip()
        if not val:
            continue
        key = _norm(val)
        entry = BRAND_LAUNCH_YEAR.get(key)
        if entry is None:
            continue
        cur = _bp(row.get(BP_COL))
        if cur is None:
            continue
        new_bp = _target_bp_for_year(
            cur, year, entry,
            seed=f"{subject}|{year}|{key}",
        )
        if new_bp is None:
            continue
        if abs(new_bp - cur) < 0.02:
            continue
        new_bp = max(0.02, min(99.49, new_bp))
        existing = str(row[BP_COL])
        bp_cell = (f"{new_bp:.4f}%"
                   if existing.strip().endswith("%")
                   else f"{new_bp:.4f}")
        df.at[idx, BP_COL] = bp_cell
        df.at[idx, CS_COL] = f"{new_bp:.4f}"
        if sample and universe:
            df.at[idx, RAW_COL] = str(int(round(
                new_bp / 100.0 * sample)))
            df.at[idx, PROJ_COL] = str(int(round(
                new_bp / 100.0 * universe)))
        n_changed += 1
        if len(changes) < 12:
            changes.append((cat, val, cur, new_bp))

    if verbose:
        print(f"  [anachronism_check] year={year} changed {n_changed} "
              f"rows")
        for cat, val, old, new in changes[:6]:
            print(f"    {cat}/{val}: {old:.4f} -> {new:.4f}")
    return df, n_changed


__all__ = ["strip_anachronistic_brands", "BRAND_LAUNCH_YEAR"]
