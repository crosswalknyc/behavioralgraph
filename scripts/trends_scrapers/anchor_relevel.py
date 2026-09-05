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

Plausibility-aware cluster selection (v3.1, 2026-09-04 scale fix)
-----------------------------------------------------------------
Some items' anchor histories flap between two log-space scale
clusters: a plausible one (tens of thousands to millions) and a tiny
one (double/triple digits, a wrong-unit or wrong-field research
artifact). Worse, when the plausible side is a verbatim clone
(Radiolab: 169,238 on 42 dates) the clone-repeat weighting derates it
to ~one observation's worth of evidence, so a handful of full-weight
artifact points (673..9,307, plus a literal `1`) wins the local fit
and the whole releveled trajectory collapses to an absurd level
(Radiolab rendered 129 on Sept 3 against a fresh researched 169,238).

`_select_fit_dates` runs BEFORE smoothing: it clusters the item's
unique anchor values in log space (single-linkage, gap >= ~6x),
classifies clusters against a per-kind absolute plausibility floor,
and drops sub-floor clusters that sit >= 20x below the dominant
plausible cluster - but ONLY when the plausible clusters hold the
raw DATE-COUNT majority (clones count as dates here: 42 dates of the
pipeline asserting 169K is evidence of scale, even though it is not
42 independent observations). Items with no plausible cluster, no
>= 20x split, or a raw-date-minority plausible cluster are left
exactly as before - the selector never invents a level. The smoother
then fits on the kept dates only and evaluates the trajectory at
every original appearance date (constant log-extension outside the
kept span), so formerly-garbage dates re-render at the item's
plausible scale.

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

# ---------------------------------------------------------------------------
# Plausibility-aware cluster selection knobs (v3.1, 2026-09-04 scale fix).
# Changing any of these is a formula change and must ride a version bump
# in the renderer.
# ---------------------------------------------------------------------------
# Single-linkage gap that separates two log-space value clusters. 6x is
# below every observed artifact-to-plausible void (Radiolab 9,307 ->
# 77,919 is 8.4x; Dr. Death 20,313 -> 202,204 is 9.95x) and above every
# observed within-regime gap, including Up First's genuine 1.5M -> 4.97M
# research swing (3.3x) which must stay chained.
_CLUSTER_LINK_LOG = math.log(6.0)
# A sub-floor cluster is only dropped when it sits at least this far
# below the dominant plausible cluster - the unmistakable bimodal
# signature from the 2026-09-04 defect (observed gaps 35 vs 202,131).
_CLUSTER_DROP_LOG = math.log(20.0)

# Per-kind absolute plausibility floors, derived from the 2026-09-04
# corpus quantification (per-kind current-level percentiles + anchor-
# point log10 histograms + split-cluster center distributions). A floor
# NEVER judges an item on its own: it only classifies the clusters of
# an item that ALSO carries a >= 20x bimodal split, so a consistently
# small item (a niche book selling 300 copies/day, a Wattpad story with
# 8 readers) is never touched. Values are conservative: they sit inside
# the empty void between observed artifact clusters (p90 of split-item
# lower-cluster centers: 83..900 for chart kinds) and observed
# plausible clusters (p10 of upper-cluster centers: 22K..58K).
_KIND_PLAUSIBILITY_FLOOR = {
    # National-chart audio/video kinds: an item charting on Apple /
    # Spotify / Netflix / Prime / FAST national top lists cannot have a
    # true daily US audience below the tens of thousands. Artifact
    # clusters on these kinds sit at 1..9,307 (Radiolab's 673..9,307
    # regime included); genuine clusters start ~21K.
    'podcast':      10_000,
    'song':         10_000,
    'film':         10_000,
    'tv':           10_000,
    'title':        10_000,
    'fast_film':    10_000,
    'fast_tv':      10_000,
    # FAST linear channels: corpus p1 of current levels is 7.9K; a
    # national FAST channel below 5K daily viewers would not hold
    # carriage.
    'fast_channel':  5_000,
    # Charting games (Steam / Twitch tops): corpus p10 = 5.0K.
    'game':          5_000,
    # Google-Trends-charting search terms: corpus p5 = 5.0K. Genuine
    # small-term regimes exist below that, but they are never 20x
    # below a raw-date-majority plausible cluster.
    'search_term':   5_000,
    # Person search interest is legitimately spiky (viral swings from
    # ~2K lulls to millions). Low floor so genuine lull regimes
    # survive; observed lull clusters bottom out at ~1.8K.
    'trending_person': 1_000,
    # Wikipedia topic views: corpus minimum ~65K; floor is a formality.
    'wiki_topic':    5_000,
    # Charting books legitimately sell hundreds per day (51% of anchor
    # points in the 100..999 decade). Only single/double-digit
    # clusters are artifacts.
    'book':            100,
    # Goodreads weekly community readers: corpus p1 = 2.9K.
    'goodreads_book':  500,
    # Comics legitimately read at tens (13% of anchor points in the
    # 10..99 decade); only single-digit clusters below a 20x gap are
    # artifacts.
    'comic':            20,
    # Wattpad niche stories legitimately read at single digits (8% of
    # anchor points in the 1..9 decade); only a ~1-reader cluster
    # sitting 20x under a real cluster is an artifact.
    'wattpad_story':     5,
}


def kind_plausibility_floor(kind: str) -> int:
    """Absolute plausibility floor for a kind. 0 for unknown kinds =
    cluster selection and the forward-guard floor skip both disabled
    (the conservative direction for a kind we have no distribution
    for)."""
    return _KIND_PLAUSIBILITY_FLOOR.get((kind or '').strip().lower(), 0)


def _select_fit_dates(key: str, by_date: dict[str, int]) -> list[str]:
    """Dates whose anchor values the smoother should FIT on.

    Returns all dates unless the item carries the unmistakable
    bimodal-garbage signature, in which case the dates whose values
    belong to sub-floor clusters >= 20x below the dominant plausible
    cluster are excluded from the fit (they still get levels: the
    smoothed trajectory is evaluated at every original date).

    Guards, in order:
      * kind floor of 0 (unknown kind) -> no selection.
      * fewer than 2 unique values, or no >= 6x linkage split -> no
        selection.
      * no cluster at/above the kind floor -> no selection (never
        invent a level the history does not contain).
      * plausible clusters must hold the raw DATE-COUNT majority over
        the would-be-dropped dates; otherwise the small regime is the
        item's real scale (e.g. a term that lulls at 3K and spiked to
        500K for a week) and nothing is dropped.
    """
    ds = sorted(by_date)
    floor = kind_plausibility_floor(key.split(':', 1)[0])
    if floor <= 0:
        return ds
    uniq = sorted({int(by_date[d]) for d in ds})
    if len(uniq) < 2:
        return ds
    clusters: list[list[int]] = [[uniq[0]]]
    for v in uniq[1:]:
        if math.log(v) - math.log(clusters[-1][-1]) >= _CLUSTER_LINK_LOG:
            clusters.append([v])
        else:
            clusters[-1].append(v)
    if len(clusters) < 2:
        return ds
    # Cluster centers (geometric mean of unique values) and raw date
    # counts (clones count every date they occupy).
    centers = [math.exp(sum(math.log(v) for v in c) / len(c))
               for c in clusters]
    val_cluster = {v: i for i, c in enumerate(clusters) for v in c}
    counts = [0] * len(clusters)
    for d in ds:
        counts[val_cluster[int(by_date[d])]] += 1
    plaus = [i for i, c in enumerate(centers) if c >= floor]
    if not plaus:
        return ds
    ref = max(plaus, key=lambda i: counts[i])
    drop = [i for i in range(len(clusters))
            if centers[i] < floor
            and math.log(centers[ref]) - math.log(centers[i])
            >= _CLUSTER_DROP_LOG]
    if not drop:
        return ds
    n_plaus = sum(counts[i] for i in plaus)
    n_drop = sum(counts[i] for i in drop)
    if n_plaus <= n_drop:
        return ds
    dropped_vals = {v for i in drop for v in clusters[i]}
    kept = [d for d in ds if int(by_date[d]) not in dropped_vals]
    if kept and len(kept) < len(ds):
        logger.info(
            "anchor_relevel scale-fix: %s dropped %d/%d artifact-cluster "
            "point(s) (kept scale ~%s, dropped centers %s, floor %d)",
            key, len(ds) - len(kept), len(ds), f"{centers[ref]:,.0f}",
            [f"{centers[i]:,.0f}" for i in drop], floor)
        return kept
    return ds


def _h01(seed: str) -> float:
    """Deterministic uniform [0, 1) from a string seed. Mirrors the
    helper in apply_daily_variation_backfill (kept local so this module
    has no import cycle with the renderer)."""
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _ll_eval(t_i: int, f_ts: list[int], f_logs: list[float],
              rw: list[float], base_w: list[float],
              fallback: float) -> float:
    """Weighted local-linear Gaussian-kernel fit evaluated at `t_i`.

    Exactly the inner loop of the pre-v3.1 smoother (same accumulation
    order, same degenerate-denominator branches), extracted so the fit
    can be evaluated at dates that are not design points."""
    s0 = s1 = s2 = sy = s1y = 0.0
    for j in range(len(f_ts)):
        w = math.exp(
            -0.5 * ((t_i - f_ts[j]) / _KERNEL_SIGMA_DAYS) ** 2
        ) * rw[j] * base_w[j]
        dt = float(f_ts[j] - t_i)
        s0 += w
        s1 += w * dt
        s2 += w * dt * dt
        sy += w * f_logs[j]
        s1y += w * dt * f_logs[j]
    den = s0 * s2 - s1 * s1
    if s0 <= 0:
        return fallback
    if abs(den) < 1e-9:
        return sy / s0
    # Intercept of the weighted local line at t_i.
    return (s2 * sy - s1 * s1y) / den


def _smooth_item_levels(key: str, by_date: dict[str, int],
                          profile: Optional[dict]) -> dict[str, float]:
    """Robust log-space level trajectory for one item.

    `by_date` maps ISO date -> anchor mid (>0). Returns ISO date ->
    level (float, same dates)."""
    ds = sorted(by_date)
    n = len(ds)
    ts = [date.fromisoformat(d).toordinal() for d in ds]
    logs = [math.log(max(1, int(by_date[d]))) for d in ds]

    # v3.1 scale fix: fit on the plausible-cluster dates only (usually
    # all of them; see _select_fit_dates). The trajectory is still
    # evaluated at EVERY original date below.
    fit_ds = _select_fit_dates(key, by_date)
    if len(fit_ds) == n:
        f_ts, f_logs = ts, logs
    else:
        f_ts = [date.fromisoformat(d).toordinal() for d in fit_ds]
        f_logs = [math.log(max(1, int(by_date[d]))) for d in fit_ds]
    m = len(f_ts)

    # Clone-repeat base weights: an exact integer repeated across K
    # dates is one observation smeared K times (stale-clone artifact),
    # so each instance carries 1/K weight. Genuinely researched values
    # never repeat verbatim, so they keep full weight.
    mult: dict[int, int] = {}
    for d in fit_ds:
        v = int(by_date[d])
        mult[v] = mult.get(v, 0) + 1
    base_w = [1.0 / mult[int(by_date[d])] for d in fit_ds]

    if m == 1:
        mu = [f_logs[0]] * n
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
        rw = [1.0] * m
        rw_used = rw
        f_mu = list(f_logs)
        for _ in range(_ROBUST_PASSES):
            rw_used = rw
            f_mu = [_ll_eval(f_ts[i], f_ts, f_logs, rw_used, base_w,
                             fallback=f_logs[i]) for i in range(m)]
            resid = [f_logs[i] - f_mu[i] for i in range(m)]
            sr = sorted(abs(r) for r in resid)
            if m % 2:
                mad = sr[m // 2]
            else:
                mad = 0.5 * (sr[m // 2 - 1] + sr[m // 2])
            s = max(_MAD_FLOOR, 1.4826 * mad)
            rw = [1.0 / (1.0 + (resid[i] / (_CAUCHY_C * s)) ** 2)
                  for i in range(m)]
        # Evaluate the final fit at every original appearance date with
        # the same weights that produced the last pass. Dates outside
        # the kept span get a constant log-extension of the boundary
        # value (never a slope extrapolated into a region whose only
        # observations were artifacts); the step-cap and drift below
        # keep the extended stretch moving organically.
        if len(fit_ds) == n:
            mu = f_mu
        else:
            lo_t, hi_t = f_ts[0], f_ts[-1]
            mu = [_ll_eval(min(max(ts[i], lo_t), hi_t), f_ts, f_logs,
                           rw_used, base_w, fallback=f_logs[0])
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
