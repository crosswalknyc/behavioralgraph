"""
Anchor re-level pass for the historical stream_estimates backfill.

Why this exists (2026-09-04): the per-date ANCHOR levels the v2 organic
renderer multiplies against come from the original daily research runs,
and for some items those runs alternated between wildly different
scales on adjacent days (research variance, not audience behavior).
Example: 'Up First from NPR' anchored at 4.97M on Jul 20 and 446K on
Jul 21 - a 10x overnight swing with no event. The v2 factor layer only
moves ~0.9-1.1x on those dates, so the flap survived into the rendered
history. A swing like that is exactly the kind of detectable artifact
the organic mandate forbids.

What this module does: per item, compute a SMOOTHED, slowly-varying
level trajectory across the item's appearance dates, working in log
space with a robust kernel estimator. One-day flaps get absorbed
(the robust weights treat them as outliers); genuine sustained ramps
survive (kernel smoothing preserves multi-week trends). A gentle
per-item drift term (direction informed by the item's rhythm-profile
`trend` where present) keeps the level series item-specific and never
constant across dates - a perfectly flat level is itself an artifact.

The renderer (`apply_daily_variation_backfill.py`) then composes:

    rendered = releveled_level(item, date) x organic_factor(item, date)

with the SAME deterministic v2 organic factor, so weekly shape, trend
arcs, reasoned events and micro-events all carry over unchanged.

Everything here is deterministic and costs $0: same anchors + same
profiles in, same levels out. The pre-v1 backups remain the permanent
read-only anchor source and are never written.

Clone-repeat weighting
----------------------
The 2026-09-04 blast-radius audit showed the dominant anchor pathology
is a VERBATIM-REPEATED value: dated snapshots that inherited the same
us_estimate from a stale latest/ carry the identical integer across
dozens of dates (Up First: 4,973,931 on ~35 dates, alternating with
genuine researched values near 500K). Exact-integer repetition across
dates is a clone artifact, never independent research - real research
output varies run to run. So each observation is weighted by
1 / multiplicity(exact value within the item's series): a value
repeated 35 times contributes one observation's worth of evidence in
total, and the level converges to the researched scale instead of
majority-voting for the clone.

Level guarantees
----------------
* Adjacent-appearance-date level ratio <= ~1.35 for 1-day gaps,
  <= ~2.35 for any single step regardless of gap (multi-week ramps
  accumulate across steps, so a title climbing 10x over six weeks
  still climbs 10x).
* Level series never constant: per-item log-drift magnitude is always
  non-zero, so consecutive dates always differ.
* Items appearing on a single date keep their anchor as the level.
"""
from __future__ import annotations

import bisect
import hashlib
import logging
import math
from datetime import date
from typing import Optional

logger = logging.getLogger('anchor_relevel')

# Robust smoothing knobs. All deterministic; changing any of these is a
# formula change and must ride a version bump in the renderer.
_KERNEL_SIGMA_DAYS = 10.0   # Gaussian kernel width on the date axis
_ROBUST_PASSES = 3          # IRLS iterations (outlier downweighting)
_MAD_FLOOR = 0.08           # log-space scale floor (stops tiny-noise
                            # series from overreacting to small resid)
_CAUCHY_C = 2.5             # Cauchy weight constant (in scale units)

# Step caps on the LEVEL series between consecutive appearance dates.
_MAX_STEP_RATIO_1D = 1.35     # per-day cap
_MAX_STEP_LOG_TOTAL = math.log(2.35)  # cap for one step at any gap

# Gentle per-item level drift (per-day log-slope band). Keeps the level
# gently moving even when the raw anchors were identical across dates.
_DRIFT_MIN = 0.0004   # ~0.04%/day  (~3.8% across a 93-day window)
_DRIFT_MAX = 0.0018   # ~0.18%/day (~18% across a 93-day window)


def _h01(seed: str) -> float:
    """Deterministic uniform [0, 1) from a string seed. Mirrors the
    helper in apply_daily_variation_backfill (kept local so this module
    has no import cycle with the renderer)."""
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _smooth_item_levels(key: str, by_date: dict[str, int],
                          profile: Optional[dict]) -> dict[str, float]:
    """Robust log-space level trajectory for one item.

    `by_date` maps ISO date -> anchor mid (>0). Returns ISO date ->
    level (float, same dates)."""
    ds = sorted(by_date)
    n = len(ds)
    ts = [date.fromisoformat(d).toordinal() for d in ds]
    logs = [math.log(max(1, int(by_date[d]))) for d in ds]

    # Clone-repeat base weights: an exact integer repeated across K
    # dates is one observation smeared K times (stale-clone artifact),
    # so each instance carries 1/K weight. Genuinely researched values
    # never repeat verbatim, so they keep full weight.
    mult: dict[int, int] = {}
    for d in ds:
        v = int(by_date[d])
        mult[v] = mult.get(v, 0) + 1
    base_w = [1.0 / mult[int(by_date[d])] for d in ds]

    if n == 1:
        mu = list(logs)
    else:
        # Iteratively-reweighted LOCAL-LINEAR Gaussian-kernel smoothing.
        # Local-linear (rather than a plain kernel mean) is boundary-
        # bias-free for log-linear trends, so a title genuinely climbing
        # across weeks keeps nearly its full ramp instead of having the
        # endpoints shrunk toward the interior. Pass 1 fits under the
        # clone-repeat weights; passes 2..N additionally downweight
        # points that sit far from the local fit (the flaps), so the
        # level tracks the researched scale and outlier days stop
        # dragging it around.
        rw = [1.0] * n
        mu = list(logs)
        for _ in range(_ROBUST_PASSES):
            new_mu = []
            for i in range(n):
                s0 = s1 = s2 = sy = s1y = 0.0
                for j in range(n):
                    w = math.exp(
                        -0.5 * ((ts[i] - ts[j]) / _KERNEL_SIGMA_DAYS) ** 2
                    ) * rw[j] * base_w[j]
                    dt = float(ts[j] - ts[i])
                    s0 += w
                    s1 += w * dt
                    s2 += w * dt * dt
                    sy += w * logs[j]
                    s1y += w * dt * logs[j]
                den = s0 * s2 - s1 * s1
                if s0 <= 0:
                    new_mu.append(logs[i])
                elif abs(den) < 1e-9:
                    new_mu.append(sy / s0)
                else:
                    # Intercept of the weighted local line at t_i.
                    new_mu.append((s2 * sy - s1 * s1y) / den)
            mu = new_mu
            resid = [logs[i] - mu[i] for i in range(n)]
            sr = sorted(abs(r) for r in resid)
            if n % 2:
                mad = sr[n // 2]
            else:
                mad = 0.5 * (sr[n // 2 - 1] + sr[n // 2])
            s = max(_MAD_FLOOR, 1.4826 * mad)
            rw = [1.0 / (1.0 + (resid[i] / (_CAUCHY_C * s)) ** 2)
                  for i in range(n)]

    # Gentle per-item drift, centered on the item's span midpoint so the
    # overall scale is preserved. Direction follows the reasoned rhythm
    # profile trend when present; flat/missing items get a small
    # hash-personality drift either way (never zero, so the level is
    # never constant across dates).
    trend = (profile or {}).get('trend') or 'flat'
    if trend == 'climbing':
        sign = 1.0
    elif trend == 'cooling':
        sign = -1.0
    else:
        sign = 1.0 if _h01(f'{key}|levdriftsign') < 0.5 else -1.0
    mag = _DRIFT_MIN + _h01(f'{key}|levdriftmag') * (_DRIFT_MAX - _DRIFT_MIN)
    if trend not in ('climbing', 'cooling'):
        mag *= 0.6
    t_mid = 0.5 * (ts[0] + ts[-1])
    mu = [mu[i] + sign * mag * (ts[i] - t_mid) for i in range(n)]

    # Forward step-cap pass: consecutive appearance dates may not move
    # more than the per-day cap (compounded across the gap) nor more
    # than the single-step total cap. Sustained ramps survive because
    # each capped step still moves in the ramp direction.
    for i in range(1, n):
        gap = max(1, ts[i] - ts[i - 1])
        allowed = min(gap * math.log(_MAX_STEP_RATIO_1D),
                      _MAX_STEP_LOG_TOTAL)
        step = mu[i] - mu[i - 1]
        if step > allowed:
            mu[i] = mu[i - 1] + allowed
        elif step < -allowed:
            mu[i] = mu[i - 1] - allowed

    return {ds[i]: math.exp(mu[i]) for i in range(n)}


def compute_levels(anchor_series: dict[str, dict[str, int]],
                    profiles: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Releveled trajectory for every item.

    `anchor_series` maps item key -> {ISO date: anchor mid > 0}, built
    by the renderer from the permanent pre-v1 backups (fallback: the
    dated file itself for dates that predate v1). Returns item key ->
    {ISO date: level float}."""
    out: dict[str, dict[str, float]] = {}
    for key, by_date in anchor_series.items():
        priced = {d: int(v) for d, v in by_date.items() if int(v) > 0}
        if not priced:
            continue
        out[key] = _smooth_item_levels(key, priced, profiles.get(key))
    logger.info("anchor_relevel: computed level trajectories for %d items",
                 len(out))
    return out


def interpolate_level(levels_for_item: dict[str, float],
                       target_iso: str) -> Optional[float]:
    """Log-linear interpolation of an item's level at `target_iso`.

    Returns None when the item has no levels or when `target_iso` falls
    OUTSIDE the item's [first, last] real-appearance span - the caller-
    facing anachronism guard: an item first seen Jul 20 must never be
    filled into June.
    """
    if not levels_for_item:
        return None
    ds = sorted(levels_for_item)
    if target_iso < ds[0] or target_iso > ds[-1]:
        return None
    if target_iso in levels_for_item:
        return float(levels_for_item[target_iso])
    i = bisect.bisect_left(ds, target_iso)
    lo, hi = ds[i - 1], ds[i]
    t0 = date.fromisoformat(lo).toordinal()
    t1 = date.fromisoformat(hi).toordinal()
    t = date.fromisoformat(target_iso).toordinal()
    f = (t - t0) / max(1, (t1 - t0))
    return math.exp(
        math.log(levels_for_item[lo]) * (1.0 - f)
        + math.log(levels_for_item[hi]) * f)
