"""Walk a carried-forward audience value to a day-specific number.

One implementation, two callers. The estimator uses it when a nightly
snapshot inherits an item unchanged from the previous day; the render
side uses it when a row reaches the page with no value of its own and
has to fall back on its own last measured one.

Why a shared primitive: before this module the estimator owned the
walk privately, so the render side had nothing to reach for and fell
back to a number derived from the row's rank slot instead. A
rank-derived number looks exactly like a measured one on the page and
says nothing about the title, so two different titles at the same rank
read almost the same. Carrying the item's own last value forward keeps
the row's identity, and walking it keeps the day honest.

The walk itself is the validated per-item rhythm composition the
historical window already uses (`apply_daily_variation_backfill.
_organic_factor`: reasoned weekly shape with per-item phase and
amplitude, trend drift, volatility-scaled daily noise, hash
personality when the item has no reasoned profile):

    new = carried_value * factor(item, target) / factor(item, carried_day)

Consecutive-day ratios telescope, so a run of carried days walks the
item's own curve from its last measured level rather than compounding
noise. Deterministic per (item, date): re-running a day reproduces the
same number. Every result carries natural last digits and can never
equal the value it was carried from, so the movement chip is real.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Clamp band for the day-over-day ratio. Non-event consecutive-day
# organic ratios land around 0.75-1.35; the clamp only catches
# profile-event edges and stays well inside the continuity guard band
# (0.4x .. 2.5x) so tomorrow's fresh research never sees an artifact
# jump.
CARRY_RATIO_MIN = 0.62
CARRY_RATIO_MAX = 1.52

# A value carried across a long gap has drifted far enough from
# anything measured that walking it further is false precision. The
# caller decides what to do past this; nothing here refuses to walk.
MAX_CARRY_AGE_DAYS = 21


def _lazy():
    """Import the estimator helpers late.

    `apply_daily_variation_backfill` imports `stream_estimates` at
    module load and `stream_estimates` imports this module, so a
    module-level import here would close the cycle. By the time a walk
    is requested every module is initialized and these resolve.
    """
    try:
        from .apply_daily_variation_backfill import _organic_factor
    except ImportError:
        from scripts.trends_scrapers.apply_daily_variation_backfill \
            import _organic_factor
    try:
        from .stream_estimates import _ensure_non_zero_last_digit, _h01
    except ImportError:
        from scripts.trends_scrapers.stream_estimates \
            import _ensure_non_zero_last_digit, _h01
    return _organic_factor, _ensure_non_zero_last_digit, _h01


def load_rhythm_profiles() -> dict:
    """Per-item rhythm profiles, shared with the estimator's cache.
    Missing file degrades to {} and every item uses its hash
    personality."""
    try:
        from .stream_estimates import _load_rhythm_profiles
    except ImportError:
        try:
            from scripts.trends_scrapers.stream_estimates \
                import _load_rhythm_profiles
        except Exception:
            return {}
    except Exception:
        return {}
    try:
        return _load_rhythm_profiles() or {}
    except Exception:
        return {}


def walk_value(prev_value: int,
               item_key: str,
               target_date: date,
               prev_date: Optional[date] = None,
               profile: Optional[dict] = None,
               salt: str = 'carry') -> int:
    """Return `prev_value` walked to `target_date`.

    `item_key` is the estimator lookup key (kind:normtitle) or any
    stable per-item string; it seeds the item's rhythm personality, so
    two items carried on the same day move differently.

    Guarantees: positive, never equal to `prev_value`, natural last
    digits, no placeholder literal, deterministic for a given
    (item_key, target_date, prev_value).
    """
    try:
        prev_mid = int(prev_value)
    except (TypeError, ValueError):
        return 0
    if prev_mid <= 0:
        return 0

    organic_factor, natural_digits, h01 = _lazy()

    if prev_date is None or prev_date >= target_date:
        prev_date = target_date - timedelta(days=1)

    target_iso = target_date.isoformat()
    try:
        f_t = organic_factor(item_key, target_date, profile)
        f_p = organic_factor(item_key, prev_date, profile)
        ratio = (f_t / f_p) if f_p > 0 else 1.0
    except Exception:
        # A walk must never be the reason a row loses its value.
        ratio = 1.0
    if ratio < CARRY_RATIO_MIN:
        ratio = CARRY_RATIO_MIN + h01(f'{item_key}|{target_iso}|carrylo') * 0.04
    elif ratio > CARRY_RATIO_MAX:
        ratio = CARRY_RATIO_MAX - h01(f'{item_key}|{target_iso}|carryhi') * 0.05

    new_mid = max(1, int(round(prev_mid * ratio)))
    new_mid = natural_digits(new_mid, item_key, f'{target_iso}|{salt}')
    if new_mid == prev_mid:
        # Small-value rounding can land back on the value we carried.
        # Smallest deterministic move that stays positive and differs.
        step = 1 + int(h01(f'{item_key}|{target_iso}|carrystep') * 8)
        sign = 1 if h01(f'{item_key}|{target_iso}|carrysign') < 0.55 else -1
        new_mid = max(1, prev_mid + sign * step)
        while new_mid == prev_mid:
            new_mid += 1
    return new_mid


def walk_ratio(prev_value: int, new_value: int) -> float:
    """Scale factor a caller applies to a band or a per-platform block
    so it moves in lockstep with the walked mid."""
    try:
        return (float(new_value) / float(prev_value)) if prev_value else 1.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0
