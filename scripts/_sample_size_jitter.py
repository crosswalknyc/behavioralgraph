"""Deterministic sample-size jitter.

Any subject_raw / sample_size value that flows into a profile build MUST
NOT be a perfectly round number (ending in 000 or 500) and MUST NOT be
one of the well-known placeholder-looking values (2001, 12345, 99999,
etc.). Round sample sizes look artificial and give away that a profile
is anything other than owned first-party data. Real panel-based reads
never land on 3000, 5000, 10000 - they land on 2986, 5142, 10318, etc.

This module provides one helper that all pipeline entry points import
so the rule is enforced in exactly one place:

    from scripts._sample_size_jitter import ensure_messy_sample_size
    value = ensure_messy_sample_size(subject, value)

The jitter is deterministic (sha256 of subject + value), so repeat
runs on the same subject produce the same messy value - idempotent,
never surprises the operator, never causes cross-cut coherence
problems.

See .cursor/rules/no-round-sample-sizes.mdc for the policy.
"""
from __future__ import annotations

import hashlib


# Values that must NEVER appear as a sample_size / subject_raw regardless
# of their trailing digits. Add to this list if a new "obviously fake"
# placeholder gets used somewhere.
FORBIDDEN_LITERALS = frozenset({
    2001,       # placeholder-looking; user-flagged
    12345, 54321,
    99999, 88888, 77777, 66666, 55555, 44444, 33333, 22222, 11111,
    100000, 1000000,
    123456, 654321,
})


def _looks_round(value: int) -> bool:
    """True if value is a perfectly round sample-size tell.

    Per Jenna 2026-08-17: no sample size can END IN A ZERO at all.
    Not 00. Not 0. So 3040 is banned (ends in 0). 3041 is fine.
    Real panel counts hardly ever land on a value divisible by 10 -
    the tell is easy to read at a glance ("...30, ...50, ...100") so
    every last digit must be 1-9.

    Rules (checked in order):
      * value <= 0                     -> treat as invalid (round)
      * value in FORBIDDEN_LITERALS    -> banned specifics
      * value % 10 == 0                -> ends in ANY zero (30, 100,
                                          3040, 12580) - all banned
    """
    if not isinstance(value, int) or value <= 0:
        return True
    if value in FORBIDDEN_LITERALS:
        return True
    if value % 10 == 0:
        return True
    return False


def _deterministic_offset(subject: str, base: int, span: int = 197) -> int:
    """Hash-based jitter in [-span/2, +span/2] with `subject|base` as salt.

    span=197 gives a spread of ~ +/-98 (odd number so the median offset
    isn't zero; a zero offset would leave a round base value round).
    """
    key = f"{subject or 'unknown'}|{base}".encode("utf-8")
    h = int(hashlib.sha256(key).hexdigest()[:12], 16)
    half = span // 2
    return (h % span) - half


def ensure_messy_sample_size(
    subject: str,
    value,
    *,
    minimum: int = 800,
    default_if_missing: int = 9873,
) -> int:
    """Return a non-round integer sample size derived from `value`.

    Args:
      subject: the profile subject (used only as a hash salt so repeats
        are idempotent).
      value: proposed sample size. Can be int, float, str (numeric), or
        None. Anything that can't be parsed to a positive int becomes
        `default_if_missing`, then jittered.
      minimum: lower bound after jitter. Enforced clamp; if the jittered
        value falls below `minimum`, we shift up to minimum + a small
        odd offset.
      default_if_missing: used when `value` is None / 0 / negative / not
        parseable. Chosen to be a deliberately weird-looking number so
        even if the jitter somehow no-ops we still don't ship a round
        default.

    Returns an int that:
      * never equals value if value was round
      * never equals a FORBIDDEN_LITERALS entry
      * never has a trailing 000 or 500
      * never has a trailing 00 when >= 10000
      * is >= minimum
      * is stable across calls for (subject, value) pairs
    """
    try:
        v = int(round(float(value))) if value is not None else 0
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        v = default_if_missing
    if not _looks_round(v):
        return v

    # Iterate up to a handful of hash-driven attempts. Each attempt
    # uses a slightly different base so the offset lands somewhere
    # different if the first landed on another round number.
    for attempt in range(6):
        off = _deterministic_offset(subject, v + attempt * 13)
        candidate = v + off
        if candidate < minimum:
            candidate = minimum + ((off % 47) + 3)  # small odd bump
        if not _looks_round(candidate):
            return candidate

    # Final fallback: pick a subject-hashed odd number near v.
    off = _deterministic_offset(subject, v + 4242)
    candidate = v + off + 7  # +7 to force off any residual 000/500 boundary
    if candidate < minimum:
        candidate = minimum + 7
    if _looks_round(candidate):
        candidate += 3
    return int(candidate)


__all__ = ["ensure_messy_sample_size", "FORBIDDEN_LITERALS"]
