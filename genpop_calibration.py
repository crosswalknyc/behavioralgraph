"""
genpop_calibration.py

Index-based calibration system that anchors all behavioral data to
verified US general population penetration rates.

HOW IT WORKS:
    For each (category, value) we store:
        - corrected_pct: the real-world US gen pop penetration (ground truth)
        - original_pct:  what the pipeline naturally produced for gen pop
                         (biased due to digitally-engaged sample)

    correction_factor = corrected_pct / original_pct

    For any profile (Taylor Swift, NFL, Kroger, etc.):
        calibrated = profile_pipeline_value * correction_factor

    This preserves the relative signal:
        - If Taylor Swift fans over-index on Twitch (60% vs gen pop 51%),
          the index (60/51 = 1.18x) is preserved in the output:
          8.5% * 1.18 = 10.0%

    Values NOT in this lookup get correction_factor = 1.0 (unchanged).

UPDATING:
    To add more corrections, add entries to GENPOP_CORRECTIONS below.
    Format: (CATEGORY, VALUE): (corrected_pct, original_pct)
"""

import pandas as pd

SILENCE_VERBOSE_OUTPUT = False

US_POPULATION = 329_900_000
SAMPLE_CAP = 10_000_000

# ── Ground-truth corrections ─────────────────────────────────────────────────
# (CATEGORY, VALUE): (corrected_pct, original_pipeline_pct)
#
# corrected_pct    = verified real-world US penetration
# original_pipeline_pct = what the bg.py pipeline produced for gen pop
#                         (before we hand-corrected the CSV)
GENPOP_CORRECTIONS: dict[tuple[str, str], tuple[float, float]] = {

    # ── SOCIAL MEDIA ──────────────────────────────────────────────────────
    ('SOCIAL MEDIA', 'TWITCH'):       (8.5,   50.9977),
    ('SOCIAL MEDIA', 'DISCORD'):      (16.5,  41.8806),
    ('SOCIAL MEDIA', 'X'):            (27.5,  36.0697),
    ('SOCIAL MEDIA', 'PATREON'):      (4.0,   19.4275),
    ('SOCIAL MEDIA', 'TUMBLR'):       (4.0,   15.522),
    ('SOCIAL MEDIA', 'ONLYFANS'):     (2.5,   12.6628),
    ('SOCIAL MEDIA', 'SNAPCHAT'):     (37.5,  10.9857),
    ('SOCIAL MEDIA', 'LETTERBOXD'):   (1.5,   7.3208),
    ('SOCIAL MEDIA', 'BLUESKY'):      (1.5,   6.4427),

    # ── STREAMING / MUSIC ────────────────────────────────────────────────
    ('STREAMING/MUSIC', 'SPOTIFY'):       (33.0,  91.9063),
    ('STREAMING/MUSIC', 'APPLE MUSIC'):   (17.0,  87.3336),
    ('STREAMING/MUSIC', 'YOUTUBE MUSIC'): (9.0,   76.1088),
    ('STREAMING/MUSIC', 'SIRIUSXM'):      (13.0,  62.1221),
    ('STREAMING/MUSIC', 'PANDORA MUSIC'): (17.5,  53.994),
    ('STREAMING/MUSIC', 'AMAZON MUSIC'):  (16.0,  45.1202),
    ('STREAMING/MUSIC', 'LAST FM'):       (2.5,   40.0746),
    ('STREAMING/MUSIC', 'DEEZER'):        (1.5,   32.6124),
    ('STREAMING/MUSIC', 'SOUNDCLOUD'):    (6.0,   27.6805),
    ('STREAMING/MUSIC', 'QOBUZ'):         (0.5,   23.2356),
    ('STREAMING/MUSIC', 'TIDAL'):         (1.5,   14.4542),

    # ── APP / PLATFORM USAGE ─────────────────────────────────────────────
    ('APP/PLATFORM USAGE', 'SLACK'):        (5.0,   37.3897),
    ('APP/PLATFORM USAGE', 'FIVERR'):       (2.5,   31.9298),
    ('APP/PLATFORM USAGE', 'FIGMA'):        (2.5,   31.1447),
    ('APP/PLATFORM USAGE', 'UPWORK'):       (3.5,   28.7336),
    ('APP/PLATFORM USAGE', 'CRUNCHYROLL'):  (4.5,   25.9617),
    ('APP/PLATFORM USAGE', 'HUBSPOT'):      (1.5,   24.9309),
    ('APP/PLATFORM USAGE', 'TINDER'):       (9.0,   34.5362),
    ('APP/PLATFORM USAGE', 'DUOLINGO'):     (9.0,   35.6859),
    ('APP/PLATFORM USAGE', 'WHATSAPP'):     (26.0,  41.316),
    ('APP/PLATFORM USAGE', 'IMDB'):         (11.0,  37.9748),

    # ── DIGITAL BANKING ──────────────────────────────────────────────────
    ('DIGITAL BANKING', 'PAYPAL'):    (47.0,  77.5729),
    ('DIGITAL BANKING', 'COINBASE'):  (10.0,  58.6475),
    ('DIGITAL BANKING', 'BILT'):      (1.5,   19.8593),

    # ── GAMES ────────────────────────────────────────────────────────────
    ('GAMES', 'ROBLOX'):              (16.0,  52.5852),
    ('GAMES', 'MINECRAFT'):           (16.0,  50.2475),
    ('GAMES', 'FORTNITE'):            (11.0,  37.8612),
    ('GAMES', 'LEAGUE OF LEGENDS'):   (4.0,   32.9464),
    ('GAMES', 'OVERWATCH'):           (2.5,   30.9613),
    ('GAMES', 'GENSHIN IMPACT'):      (2.5,   30.6784),
    ('GAMES', 'CALL OF DUTY'):        (9.0,   29.8885),

    # ── BROADCAST / CABLE ────────────────────────────────────────────────
    ('BROADCAST/CABLE', 'ESPN'):      (27.0,  88.448),
    ('BROADCAST/CABLE', 'FOX NEWS'):  (16.0,  46.6019),
    ('BROADCAST/CABLE', 'CNN'):       (13.0,  44.6471),
    ('BROADCAST/CABLE', 'MSNBC'):     (9.0,   41.1418),

    # ── MEDIA ────────────────────────────────────────────────────────────
    ('MEDIA', 'ESPN'):                (27.0,  88.448),
    ('MEDIA', 'FOX NEWS'):            (16.0,  46.6019),
    ('MEDIA', 'CNN'):                 (13.0,  44.6471),
    ('MEDIA', 'MSNBC'):              (9.0,   41.1418),
    ('MEDIA', 'NEW YORK TIMES'):      (11.0,  29.4251),
    ('MEDIA', 'BUZZFEED'):            (9.0,   23.5606),

    # ── STREAMING / PLATFORM ─────────────────────────────────────────────
    ('STREAMING/PLATFORM', 'ESPN'):   (27.0,  88.448),

    # ── FRANCHISE ────────────────────────────────────────────────────────
    ('FRANCHISE', 'ROBLOX'):          (16.0,  52.5852),
    ('FRANCHISE', 'MINECRAFT'):       (16.0,  50.2475),
    ('FRANCHISE', 'FORTNITE'):        (11.0,  37.8612),

    # ── EDUCATION & LEARNING ─────────────────────────────────────────────
    ('EDUCATION & LEARNING', 'UDEMY'):        (4.0,   26.4987),
    ('EDUCATION & LEARNING', 'W3 SCHOOLS'):   (2.5,   22.5087),
    ('EDUCATION & LEARNING', 'MASTER CLASS'): (2.5,   14.977),
    ('EDUCATION & LEARNING', 'SKILLSHARE'):   (1.5,   11.4138),

    # ── INVESTMENTS (corrected upward — originals were too low) ──────────
    ('INVESTMENTS', 'CHARLES SCHWAB'): (11.0,  6.0148),
    ('INVESTMENTS', 'FIDELITY'):       (13.0,  3.5321),
    ('INVESTMENTS', 'ROBINHOOD'):      (7.5,   2.5387),

    # ── AUTOMOBILE ───────────────────────────────────────────────────────
    ('AUTOMOBILE', 'BMW'):             (9.0,   42.1315),
    ('AUTOMOBILE', 'MERCEDES-BENZ'):   (7.0,   40.133),
    ('AUTOMOBILE', 'AUDI'):            (5.0,   31.2163),
    ('AUTOMOBILE', 'PORSCHE'):         (1.5,   29.3845),
    ('AUTOMOBILE', 'FERRARI'):         (0.5,   23.7824),
    ('AUTOMOBILE', 'LAMBORGHINI'):     (0.3,   17.0387),
}


# ── Pre-compute correction factors ───────────────────────────────────────────

def _build_correction_factors() -> dict[tuple[str, str], float]:
    """Derive correction_factor = corrected / original for every entry."""
    factors: dict[tuple[str, str], float] = {}
    for key, (corrected, original) in GENPOP_CORRECTIONS.items():
        if original > 0:
            factors[key] = corrected / original
        else:
            factors[key] = 1.0
    return factors

CORRECTION_FACTORS = _build_correction_factors()


# ── Public API ────────────────────────────────────────────────────────────────

def calibrate_to_genpop(df: pd.DataFrame) -> pd.DataFrame:
    """Apply gen-pop correction factors to a profile's post-pipeline DataFrame.

    For every (Column, Value) that has a correction factor:
        new_pct = current_pct * factor          (capped at 95 %)
        Original Raw Numbers  recalculated
        US Gen Pop Projection recalculated
        Brand Penetration     recalculated

    Values without a correction factor pass through unchanged.
    Demographics, metadata, and BRAND INPUT rows are never touched.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    skip_categories = {
        'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
        'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
        'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
        'OCCUPATION', 'LOCATION',
    }

    # Resolve sample size for raw-number conversion
    sample_size = _get_sample_size(df)

    calibrated_count = 0

    for idx, row in df.iterrows():
        category = str(row.get('Column', '')).upper().strip()
        if category in skip_categories:
            continue

        value = str(row.get('Value', '')).upper().strip()
        key = (category, value)

        factor = CORRECTION_FACTORS.get(key)
        if factor is None:
            continue

        # Current Brand Penetration (Row) — the pipeline's output
        current_pct = _safe_float(row.get('Brand Penetration (Row)', 0))
        if current_pct <= 0:
            # Fallback: derive from Original Raw Numbers
            raw = _safe_float(row.get('Original Raw Numbers', 0))
            if raw > 0 and sample_size > 0:
                current_pct = (raw / sample_size) * 100.0
            else:
                continue

        # Apply correction factor, cap at 95%
        calibrated_pct = min(current_pct * factor, 95.0)

        # Floor at 0.0001% to avoid zero-ing out values that should exist
        calibrated_pct = max(calibrated_pct, 0.0001)

        # Recalculate downstream columns
        new_raw = int(round((calibrated_pct / 100.0) * sample_size))
        new_genpop = int(round((new_raw / SAMPLE_CAP) * US_POPULATION))

        df.at[idx, 'Brand Penetration (Row)'] = round(calibrated_pct, 4)
        df.at[idx, 'Original Raw Numbers'] = new_raw
        df.at[idx, 'US Gen Pop Projection'] = new_genpop

        calibrated_count += 1

    if not SILENCE_VERBOSE_OUTPUT:
        print(f"🎯 Gen-pop calibration applied: {calibrated_count} values corrected "
              f"({len(CORRECTION_FACTORS)} factors loaded)")

    return df


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_sample_size(df: pd.DataFrame) -> int:
    mask = df['Column'].str.upper() == 'SAMPLE SIZE'
    if not mask.any():
        return SAMPLE_CAP
    for col in ('Percentage', 'Category Share', 'Brand Penetration (Row)'):
        if col in df.columns:
            val = df.loc[mask, col].iloc[0]
            try:
                return int(float(str(val).replace(',', '')))
            except (ValueError, TypeError):
                continue
    return SAMPLE_CAP


def _safe_float(val) -> float:
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0.0
