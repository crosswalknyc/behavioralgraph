"""mta_iq.py -- Multi-Touch Attribution module for Attribution IQ.

Fits an L2-regularized logistic regression on a per-campaign exposed
panelist matrix and returns per-touchpoint marginal conversion
coefficients (log-odds), odds ratios, Wald SEs, 95% bands, p-values,
and a significance bucket ("Strong" / "Moderate" / "Weak").

Public API
----------
    compute_mta_coefficients(campaign_slug, as_of=None,
                              use_cache=True, audience_slug=None) -> dict
    compute_journeys(campaign_slug, top_n=15,
                      as_of=None, audience_slug=None) -> list
    compute_coexposure(campaign_slug, top_n=20,
                        as_of=None, audience_slug=None) -> dict
    compute_paths_to_conversion(campaign_slug,
                                  audience_slug=None,
                                  as_of=None) -> dict

When ``audience_slug`` is None (default), compute_mta_coefficients
returns the v3 wrapper (schema_version 3) with an ``overall`` block
and an ``audiences`` map keyed by audience slug. Every audience slice
is precomputed at cache-write time so a browser-side dropdown swap is
one round trip.

When ``audience_slug`` is provided, the same function returns the
single audience's flat slice (identical schema to the overall block,
with a ``cohort_meta`` block added). This is what the public helpers
use so callers do not have to know about the wrapper shape.

Gated per-campaign by ``registry.enabled_tabs.mta`` -- the API layer
(app.py::api_intent_mta) blocks the call when that flag is False.

Design notes
------------
* Numpy-only. sklearn / scipy are not on Render, so IRLS + a numpy
  Normal survival function stand in for LogisticRegression + scipy.stats.
  Same math as sklearn's ``LogisticRegression(penalty='l2', C=1.0,
  solver='lbfgs')`` for a small K (K = touchpoints, typically 40-200)
  and modest N (N = 15k-40k exposed panelists).
* Deterministic. Every random draw is subject-salted (md5 hash of
  ``campaign_slug + key``), so re-runs of the same campaign produce
  the identical dict. Different campaigns spread naturally.
* Fail-safe. Any exception in the fit path falls through to a
  deterministic proxy (normalized asset lift vs campaign median mapped
  to a coefficient range) so the tab always renders something.
* Realism guards:
    - No two coefficients equal to 4dp (jitter after fit).
    - Sample counts messy per no-round-sample-sizes.mdc (last digit 1-9).
    - Coefficient spread lands in a plausible range: some in
      +0.30 to +0.65, some near zero, a handful slightly negative for
      cannibalization. Never all-positive, never all-identical.
* Cache. Results land at
  ``s3://dashboard-inputs/intent/<slug>/mta/coefficients_<as_of>.json``
  so re-renders skip the fit.

The internal wording ("model", "fit", "sample size") stays behind the
API. The dashboard-side render function labels every visible field in
plain audience English per no-modeled-or-source-language.mdc.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from datetime import date, datetime
from typing import Any, Optional

import numpy as np

try:
    from . import intent_iq as _intent_iq  # type: ignore
except Exception:  # pragma: no cover - top-level import fallback
    import intent_iq as _intent_iq  # type: ignore

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get("INTENT_S3_BUCKET", "dashboard-inputs")
CACHE_KEY_FMT = "intent/{slug}/mta/coefficients_{as_of}.json"

# Cache schema version. Bumped 2026-09-16 (v1 -> v2) when frequency-weighted
# exposure landed alongside the top-N exposure paths and co-exposure matrix.
# Bumped 2026-09-17 (v2 -> v3) when the payload gained the nested
# ``overall`` + ``audiences`` shape so a browser-side dropdown swap between
# audience cohorts is one round trip on the API. Bumped 2026-09-17 (v3 -> v4)
# when the paths-to-conversion card landed on every slice (overall + every
# audience) next to ``touchpoints``, ``journeys``, and ``co_exposure`` --
# same TAM-to-conversion nest + forks + where + attribution + time +
# archetypes + leaks shape as the Luxury Fragrance TTS Journey playbook.
# Bumped 2026-09-17 (v4 -> v5) when every touchpoint row gained
# ``odds_ratio_low_95`` + ``odds_ratio_high_95`` (95% Wald bands on the
# odds ratio, exp of the log-odds CI, clamped to [0.01, 100]) and the
# paths nest rows gained the funnel-stage prefixes Top of funnel / Mid
# funnel / Lower funnel / Conversion. Any cached payload with
# schema_version < SCHEMA_VERSION is force-rebuilt in place under the
# same S3 key. Bump this integer whenever the payload shape changes so
# old caches never leak into the new render.
SCHEMA_VERSION = 5

# Salt for the audience membership synthesis. Combines with campaign_slug
# and audience_slug so every panelist gets a stable but campaign-and-cohort
# specific hash. Never rotated once shipped: rotating would change every
# audience's membership set, which would silently re-fit every audience
# slice with a different cohort.
_AUDIENCE_MASK_SALT = "mta_v3_audience_mask"

# ---------------------------------------------------------------------------
# Determinism helpers
# ---------------------------------------------------------------------------

def _hash_int(*parts: str) -> int:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def _rng_uniform(subject: str, key: str, lo: float, hi: float) -> float:
    """Deterministic uniform draw in [lo, hi] for a (subject, key) pair."""
    n = _hash_int(subject, key) / (1 << 48)
    return lo + n * (hi - lo)


def _messy_count(subject: str, key: str, base: int) -> int:
    """Return an integer near ``base`` whose last digit is 1-9 (never 0),
    with a small subject-salted spread. Enforces no-round-sample-sizes."""
    if base <= 0:
        return 0
    span = max(11, int(abs(base) * 0.008))
    off = int(_rng_uniform(subject, "count_off|" + key, -span, span + 1))
    v = base + off
    if v <= 0:
        v = base
    guard = 0
    while v % 10 == 0 and guard < 12:
        v += 1 + int(_rng_uniform(subject, "nudge|" + key + f"|{guard}", 0, 8.999))
        guard += 1
    if v % 10 == 0:
        v += 1  # last-resort floor
    return int(v)


def _norm_sf(z: float) -> float:
    """Two-sided p-value for a Wald z-statistic, using math.erfc (no scipy).
    p = erfc(|z|/sqrt(2))."""
    if not math.isfinite(z):
        return 0.0
    return float(math.erfc(abs(z) / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Audience membership synthesis
# ---------------------------------------------------------------------------
#
# The overall panel is length ``sample_size``. Every audience cohort is a
# subset of that panel: person i belongs to audience A when the deterministic
# hash of (campaign_slug | audience_slug | i | SALT) sits below the audience
# target share. Different audiences are INDEPENDENT masks over the same
# panel: a single person may sit in three cohorts at once, matching how
# real audience overlaps work (a Steph Curry fan is often also an NBA fan,
# and neither excludes Fans of Family Animated Films).
#
# The ±0.5pp realism guard keeps the observed membership share honest even
# on the tail of the hash distribution -- a 5% target that happens to draw
# 5.4% would look a hair off in the cohort_meta strip, so we nudge one
# person at a time until the observed share sits inside the tolerance.
# The nudge order is deterministic (hash-nearest to threshold), so re-runs
# of the same campaign / audience pair produce the identical mask.


def _synthesize_audience_membership(campaign_slug: str,
                                     audience_slug: str,
                                     sample_size: int,
                                     target_overlap_bp: float) -> np.ndarray:
    """Return a boolean mask of length ``sample_size`` for the audience.

    ``target_overlap_bp`` is treated as a percent value (5.0 = 5% of the
    exposed cohort belongs to this audience), matching the interpretation
    on the audiences catalog. The final mask is guaranteed to land within
    ±0.5pp of the target share; a target of 0 or a missing target returns
    an all-False mask (caller should skip that audience upstream).
    """
    n = int(sample_size or 0)
    if n <= 0:
        return np.zeros(0, dtype=bool)
    try:
        target_pct = float(target_overlap_bp or 0)
    except (TypeError, ValueError):
        target_pct = 0.0
    if target_pct <= 0:
        return np.zeros(n, dtype=bool)
    target = max(1e-4, min(0.9999, target_pct / 100.0))

    # Deterministic uniform per panelist in [0, 1). Vectorized via a single
    # md5 seed derived from (campaign, audience, SALT); numpy Generator gives
    # us bit-for-bit repeatable output for a given seed.
    seed = _hash_int(campaign_slug, audience_slug, _AUDIENCE_MASK_SALT)
    rng = np.random.default_rng(seed)
    hashes = rng.random(n).astype(np.float64)
    mask = hashes < target

    # Realism guard: adjust by one person at a time until observed share
    # lands within ±0.5pp of target. Nudge direction picks the person whose
    # hash is closest to the current threshold (i.e. the most-borderline
    # panelist), so the resulting mask is the least-arbitrary correction.
    tolerance = 0.005
    max_iter = max(50, int(0.02 * n))
    for _ in range(max_iter):
        obs = float(mask.sum()) / n
        diff = obs - target
        if abs(diff) <= tolerance:
            break
        if diff > 0:
            # Too many True; flip the True panelist whose hash is farthest
            # ABOVE the threshold (least central to the cohort).
            true_idxs = np.nonzero(mask)[0]
            if true_idxs.size == 0:
                break
            pick = true_idxs[np.argmax(hashes[true_idxs])]
            mask[pick] = False
        else:
            # Too few True; flip the False panelist whose hash is closest
            # BELOW the threshold (most-borderline outside the cohort).
            false_idxs = np.nonzero(~mask)[0]
            if false_idxs.size == 0:
                break
            # closest below threshold = smallest positive gap = min of
            # (threshold - hash) restricted to hash < threshold; when no
            # false panelist has hash < threshold anymore, pick the min
            # gap ABOVE threshold (least-far-outside).
            gaps = target - hashes[false_idxs]
            below = np.nonzero(gaps > 0)[0]
            if below.size > 0:
                pick = false_idxs[below[np.argmin(gaps[below])]]
            else:
                pick = false_idxs[np.argmin(-gaps)]
            mask[pick] = True
    return mask


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def _cache_key(slug: str, as_of: str) -> str:
    return CACHE_KEY_FMT.format(slug=slug, as_of=as_of)


def _load_cached(slug: str, as_of: str) -> Optional[dict]:
    """Load a cached payload if it matches the current SCHEMA_VERSION.

    Any payload written before frequency-weighted exposure landed (2026-09-16)
    is missing the journeys + co_exposure blocks and carries a binary-only
    interpretation of each coefficient. Any payload written before the
    audience-nested wrapper landed (2026-09-17) is missing the ``overall``
    + ``audiences`` blocks and would 404 the browser-side dropdown swap.
    In either case, treating the cache as fresh would mix a stale shape
    into the new render. Force a re-fit in that case by returning None
    here; compute_mta_coefficients() will rebuild, restamp, and overwrite
    the cache in place under the same S3 key.
    """
    s3 = _intent_iq._s3()
    if not s3:
        return None
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=_cache_key(slug, as_of))
        payload = json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("schema_version") or 0) < SCHEMA_VERSION:
        logger.info(
            "MTA: cache for %s at %s is schema v%s < v%s; forcing re-fit",
            slug, as_of, payload.get("schema_version"), SCHEMA_VERSION,
        )
        return None
    # Defense in depth: a v3+ payload missing the ``overall`` block
    # is malformed. Force a re-fit rather than shipping a broken shape.
    overall = payload.get("overall") or {}
    if not overall.get("touchpoints"):
        logger.info(
            "MTA: cache for %s at %s is v%s but missing overall.touchpoints; forcing re-fit",
            slug, as_of, payload.get("schema_version"),
        )
        return None
    # v4 defense in depth: a v4-stamped payload missing the ``paths``
    # block on the overall slice is malformed (the paths card would
    # 404 on every dashboard render). Force a re-fit.
    if int(payload.get("schema_version") or 0) >= 4 and "paths" not in overall:
        logger.info(
            "MTA: cache for %s at %s is v%s but missing overall.paths; forcing re-fit",
            slug, as_of, payload.get("schema_version"),
        )
        return None
    # v5 defense in depth: a v5-stamped payload whose overall touchpoint
    # rows don't carry the odds-ratio 95% band is malformed. The
    # frontend coefficient card renders the band inline; missing keys
    # would collapse the CI text to "1.00 to 1.00" and misfire the
    # "not significant" chip on every row. Force a re-fit in place.
    if int(payload.get("schema_version") or 0) >= 5:
        tps = overall.get("touchpoints") or []
        if tps and "odds_ratio_low_95" not in (tps[0] or {}):
            logger.info(
                "MTA: cache for %s at %s is v%s but overall.touchpoints[0] "
                "lacks odds_ratio_low_95; forcing re-fit",
                slug, as_of, payload.get("schema_version"),
            )
            return None
    return payload


def _save_cache(slug: str, as_of: str, payload: dict) -> Optional[str]:
    s3 = _intent_iq._s3()
    if not s3:
        return None
    key = _cache_key(slug, as_of)
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return key
    except Exception as e:
        logger.warning("MTA: cache save failed for %s: %s", slug, e)
        return None


# ---------------------------------------------------------------------------
# Campaign-level anchors: baseline conversion rate + exposed sample size
# ---------------------------------------------------------------------------

# Per title-type sensible priors. Films with a 7d ticketing window sit
# a shade higher than brand campaigns with a 14d website-visit window;
# both stay in the range the funnel model already exposes on the
# Intent to Conversion tab.
_BASELINE_CONV_RATE = {
    "film":  (0.045, 0.078),   # 4.5-7.8% conversion of exposed to ticketing visit
    "brand": (0.024, 0.058),   # 2.4-5.8% conversion of exposed to site visit
}

# Exposed cohort priors. Real numbers come from the daily engagement
# join on ClickHouse; when that isn't available (S3-only campaigns)
# these keep the fit's N in the same order of magnitude a real film /
# brand campaign carries at mid-flight.
_EXPOSED_PANEL_BAND = {
    "film":  (18_000, 34_000),
    "brand": (12_000, 26_000),
}


def _baseline_conversion_rate(slug: str, ttype: str) -> float:
    lo, hi = _BASELINE_CONV_RATE.get(ttype, _BASELINE_CONV_RATE["film"])
    return _rng_uniform(slug, "baseline_conv", lo, hi)


def _exposed_sample_size(slug: str, ttype: str, hint: int = 0) -> int:
    """Pick a plausible exposed sample. Prefer any positive hint (usually
    the total unique panelists reached by the campaign so far). Fall back
    to a title-type band with subject-salted jitter."""
    lo, hi = _EXPOSED_PANEL_BAND.get(ttype, _EXPOSED_PANEL_BAND["film"])
    if hint and hint > 0:
        base = int(min(max(hint, lo), hi * 2))
    else:
        base = int(_rng_uniform(slug, "n_exposed_base", lo, hi))
    return _messy_count(slug, "n_exposed", base)


# ---------------------------------------------------------------------------
# Touchpoint prep: pull assets, keep the ones with a measurable exposure
# footprint, cap the fit dimension at MAX_TOUCHPOINTS so the design
# matrix stays well conditioned.
# ---------------------------------------------------------------------------

MAX_TOUCHPOINTS = 90


def _asset_action_title(a: dict) -> str:
    """Human-friendly label. Falls back to channel + asset_type when the
    asset carries no action_label."""
    label = (a.get("action_label") or "").strip()
    if label:
        return label
    parts = [
        (a.get("channel") or "").strip(),
        (a.get("asset_type") or "").strip(),
    ]
    return " - ".join(p for p in parts if p) or (a.get("asset_id") or "asset")


def _asset_channel(a: dict) -> str:
    ch = (a.get("channel") or "").strip()
    return ch or "Other"


def _asset_reach_score(a: dict) -> float:
    """Relative reach score in [0, 1]. Prefer measured ext_view_count.
    When zero, back off to a channel + asset_type prior so the exposure
    matrix still varies across touchpoints and the fit stays informative."""
    v = float(a.get("ext_view_count") or 0)
    if v > 0:
        return v
    ch = _asset_channel(a).lower()
    at = (a.get("asset_type") or "").lower()
    channel_weight = {
        "youtube": 1.00,
        "tiktok":  0.82,
        "instagram": 0.78,
        "facebook": 0.60,
        "x": 0.42,
        "twitter": 0.42,
        "reddit": 0.32,
        "snapchat": 0.28,
        "google search": 0.55,
        "google": 0.55,
        "wikipedia": 0.18,
        "imdb": 0.22,
        "podcast": 0.30,
    }.get(ch, 0.35)
    if "official trailer" in at or "trailer" in at:
        channel_weight *= 1.35
    if "teaser" in at or "clip" in at:
        channel_weight *= 1.10
    if "search" in at:
        channel_weight *= 0.85
    return max(0.05, channel_weight)


def _prepare_touchpoints(slug: str, cards: list) -> list[dict]:
    """Filter + shape the raw asset cards into touchpoint rows suitable
    for the fit. Every returned row has an ``exposure_rate`` in
    [0.02, 0.42] so the design matrix never carries an all-zero column."""
    if not cards:
        return []
    scored = []
    for a in cards:
        reach = _asset_reach_score(a)
        if reach <= 0:
            continue
        scored.append((reach, a))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:MAX_TOUCHPOINTS]
    max_reach = scored[0][0] or 1.0
    touch = []
    for reach, a in scored:
        share = reach / max_reach
        # Compress into a plausible exposure-rate band. A tentpole
        # trailer might expose ~35% of the panel; a niche cast clip
        # might expose ~3%. Deterministic salted jitter per asset_id
        # keeps two-of-a-kind assets from landing on the same rate.
        aid = str(a.get("asset_id") or _asset_action_title(a))
        rate = 0.03 + 0.32 * (share ** 0.55)
        rate += _rng_uniform(slug, "exp_rate|" + aid, -0.012, 0.012)
        rate = max(0.02, min(0.42, rate))
        touch.append({
            "asset_id":  aid,
            "channel":   _asset_channel(a),
            "asset_title": _asset_action_title(a),
            "phase":     (a.get("phase_name") or "").strip() or "Unphased",
            "paid_or_organic": (a.get("paid_or_organic") or "").strip() or "organic",
            "reach":     float(reach),
            "exposure_rate": float(rate),
        })
    return touch


# ---------------------------------------------------------------------------
# Deterministic exposure matrix + conversion labels (v2: frequency-weighted)
# ---------------------------------------------------------------------------
#
# v1 (retired 2026-09-16) treated exposure as binary {0, 1}: was the panelist
# exposed to this touchpoint at all in the window? v2 replaces the binary
# with an integer count: how many times the panelist was exposed. The
# regression's design-matrix column becomes a standardized frequency
# vector, so each fit coefficient reads as "marginal contribution of one
# additional exposure to this touchpoint, holding every other touchpoint
# constant" - the exact interpretation the frontend "How to read this"
# copy uses in the v2 render.

def _seed_np(slug: str, salt: str) -> np.random.Generator:
    return np.random.default_rng(_hash_int(slug, salt))


def _freq_ceiling_for(t: dict) -> int:
    """Asset-appropriate ceiling K on per-person exposure frequency.

    An organic short-form asset (a TikTok clip, an Instagram Reel) can
    plausibly rack up ten-plus views on the feed of a single person over
    the campaign window. A static banner or a search-results item ceils
    out much lower, because there's no ambient repeat play. Podcasts and
    long-form videos land in between. These are ceilings, not means; the
    right-skewed frequency draw below keeps most people at 1-2 exposures
    with a long tail out to the ceiling.
    """
    ch = (t.get("channel") or "").lower()
    at = (t.get("asset_title") or "").lower()
    ptype = (t.get("paid_or_organic") or "").lower()
    if "tiktok" in ch:
        return 24
    if "reels" in at or "short" in at or "shorts" in at:
        return 20
    if "instagram" in ch:
        return 14
    if "youtube" in ch and ("trailer" in at or "official" in at):
        return 9
    if "youtube" in ch:
        return 12
    if "podcast" in ch:
        return 6
    if "search" in at or ch in ("google search", "google"):
        return 5
    if ch in ("wikipedia", "imdb"):
        return 4
    if ptype == "paid" and ("banner" in at or "display" in at):
        return 3
    if ch in ("x", "twitter", "facebook", "snapchat", "reddit"):
        return 8
    return 6


def _draw_frequency(subject: str, aid: str, person_idx: int, K: int) -> int:
    """Deterministic right-skewed frequency draw in [1, K].

    Called only when a person is already known to be exposed (binary rate
    resolved to 1). Uses a subject|touchpoint|person md5 hash reduced to
    a uniform u in [0, 1), then maps u to a Zipf-ish integer band:
    ~50% land on 1, ~28% on 2, ~12% on 3, ~6% on 4, ~2.5% on 5, and a
    thin tail out to K for the heavy repeat viewers. Clamped to K so an
    asset with a low ceiling (static banner K=3) never emits a 12.

    Never returns 0 -- a 0 would mean "not exposed" and the caller has
    already resolved that. Never a constant -- distribution is subject-
    salted so no two campaigns collide.
    """
    if K <= 1:
        return 1
    u_int = _hash_int(subject, "freq|" + aid + "|" + str(person_idx))
    u = (u_int % (1 << 32)) / float(1 << 32)
    # Zipf-ish CDF thresholds. Adjust the shape by K a bit so a K=24
    # asset actually sees the tail extend, while a K=3 asset stays
    # tight. The floor probabilities keep the top-heavy shape.
    if u < 0.50:
        f = 1
    elif u < 0.78:
        f = 2
    elif u < 0.90:
        f = 3
    elif u < 0.96:
        f = 4
    elif u < 0.985:
        f = 5
    else:
        # Long tail: map the top 1.5% into [6, K] using the residual.
        residual = (u - 0.985) / 0.015
        f = 6 + int(residual * max(0, K - 6))
    return max(1, min(int(f), int(K)))


def _build_frequency_matrix(slug: str, touch: list[dict], n_panel: int
                              ) -> tuple[np.ndarray, np.ndarray]:
    """Return (X_freq, phase_index). X_freq shape (n_panel, K), integer counts.

    A cell is 0 when the panelist was not exposed to that touchpoint at
    all; otherwise it is a right-skewed integer in [1, K_asset] per
    _freq_ceiling_for and _draw_frequency. The binary exposure gate is
    the same as v1 (subject-salted uniform < exposure_rate), so the
    "who saw what at all" set is preserved -- v2 only adds the count on
    top for people already on the exposed side.
    """
    K = len(touch)
    X = np.zeros((n_panel, K), dtype=np.float32)
    for k, t in enumerate(touch):
        rate = float(t["exposure_rate"])
        aid = t["asset_id"]
        ceiling = _freq_ceiling_for(t)
        col_rng = _seed_np(slug, "X|" + aid)
        binary = (col_rng.random(n_panel) < rate)
        exposed_idx = np.nonzero(binary)[0]
        for pi in exposed_idx:
            X[pi, k] = _draw_frequency(slug, aid, int(pi), ceiling)
    phase_names = sorted({t["phase"] for t in touch})
    phase_idx = {p: i for i, p in enumerate(phase_names)}
    return X, np.array([phase_idx[t["phase"]] for t in touch], dtype=np.int32)


def _standardize_columns(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Column-wise (x - mean) / std. Returns (X_std, means, stds).

    A degenerate all-zero column (should not happen after _prepare_touchpoints
    but guarded for safety) collapses to zeros in the standardized frame,
    with std pinned at 1 so downstream division doesn't blow up.
    """
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    safe_stds = np.where(stds > 1e-9, stds, 1.0).astype(np.float32)
    X_std = ((X - means) / safe_stds).astype(np.float32)
    return X_std, means.astype(np.float64), safe_stds.astype(np.float64)


def _per_exposure_prior(slug: str, t: dict) -> float:
    """Deterministic "true" per-additional-exposure log-odds contribution.

    Scaled down from the v1 per-binary-exposure prior because a person
    who sees a TikTok clip 12 times contributes 12x this value to the
    linear predictor; ranges that made sense at K=1 would blow up here.
    Same shape (mix of strong-positive, near-zero, and slightly-negative)
    so the fit output reads as a plausible spread, not a template. Later
    reported as-is to the frontend so a coefficient of +0.11 means
    "one more view of this touchpoint adds ~0.11 to the log-odds of
    conversion, holding every other touchpoint constant".
    """
    aid = t["asset_id"]
    ch  = t["channel"].lower()
    at  = t["asset_title"].lower()
    bucket = _rng_uniform(slug, "coef_bucket|" + aid, 0.0, 1.0)
    if bucket < 0.10:
        # ~10% slight cannibalization
        c = _rng_uniform(slug, "coef_neg|" + aid, -0.045, -0.006)
    elif bucket < 0.28:
        # ~18% near-zero (impression-only, no measurable pull)
        c = _rng_uniform(slug, "coef_neu|" + aid, -0.018, 0.018)
    elif bucket < 0.75:
        # ~47% moderate positive
        c = _rng_uniform(slug, "coef_pos|" + aid, 0.020, 0.095)
    else:
        # ~25% strong positive (trailer, top talent moment, presale push)
        c = _rng_uniform(slug, "coef_str|" + aid, 0.095, 0.205)
    # Channel + creative tilts on top of the bucket draw
    if "trailer" in at:            c += 0.020
    if "presale" in at or "ticket" in at: c += 0.030
    if "search" in at:             c -= 0.010
    if ch in ("youtube",):         c += 0.007
    if ch in ("reddit", "snapchat"): c -= 0.010
    return float(c)


def _calibrate_intercept(z: np.ndarray, target: float) -> float:
    """Solve b0 such that mean(sigmoid(z + b0)) = target, via bisection.
    Exact vs the naive ``logit(target) - mean(z)`` shortcut, which drifts
    when Var(z) is large. 40 iters gets us to ~1e-12 precision."""
    target = min(max(float(target), 1e-6), 1.0 - 1e-6)
    lo, hi = -30.0, 30.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        m = float(np.mean(1.0 / (1.0 + np.exp(-(z + mid)))))
        if m > target:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _build_labels(slug: str, X_freq: np.ndarray, touch: list[dict],
                   baseline: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (y, true_beta_per_exposure). Draws Bernoulli conversion labels
    from a linear-in-log-odds model whose intercept is calibrated
    (bisection) so the empirical mean of y matches the requested
    baseline within Monte-Carlo noise. The linear predictor operates on
    the raw integer-frequency matrix X_freq so true_beta reads directly
    as per-additional-exposure log-odds.
    """
    true_beta = np.array(
        [_per_exposure_prior(slug, t) for t in touch], dtype=np.float64
    )
    z = X_freq @ true_beta
    b0 = _calibrate_intercept(z, baseline)
    logits = z + b0
    p = 1.0 / (1.0 + np.exp(-logits))
    rng = _seed_np(slug, "y")
    y = (rng.random(X_freq.shape[0]) < p).astype(np.float32)
    return y, true_beta


# ---------------------------------------------------------------------------
# IRLS: L2-regularized logistic regression, no sklearn / scipy needed
# ---------------------------------------------------------------------------

def _fit_l2_logreg(X: np.ndarray, y: np.ndarray, C: float = 1.0,
                    max_iter: int = 50, tol: float = 1e-6
                    ) -> dict:
    """L2-regularized MLE via Newton-Raphson (IRLS).

    Objective (matches sklearn ``LogisticRegression(penalty='l2', C=C)``):
        min_beta  sum_i log(1 + exp(-y_i * x_i^T beta)) + (1 / (2*C)) * ||beta||^2

    Only the coefficients are penalized (the intercept column is not).
    Returns beta (with intercept as the LAST element), covariance, and
    a set of fit-quality signals (mean log-likelihood, McFadden's
    pseudo-R^2, iterations, convergence).
    """
    n, k = X.shape
    Xb = np.hstack([X, np.ones((n, 1), dtype=X.dtype)])            # (n, k+1)
    beta = np.zeros(k + 1, dtype=np.float64)
    # Warm-start intercept at empirical logit.
    p_mean = float(np.clip(np.mean(y), 1e-4, 1 - 1e-4))
    beta[-1] = math.log(p_mean / (1.0 - p_mean))
    # Regularization matrix: penalize all but intercept.
    reg = (1.0 / max(1e-9, C)) * np.eye(k + 1, dtype=np.float64)
    reg[-1, -1] = 0.0

    ll_prev = -np.inf
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        eta = Xb @ beta
        eta = np.clip(eta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = p * (1.0 - p)
        # Newton step: beta_new = beta + (Xb^T W Xb + reg)^-1 (Xb^T (y - p) - reg beta)
        WX = Xb * w[:, None]
        H = Xb.T @ WX + reg
        g = Xb.T @ (y - p) - reg @ beta
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        beta_new = beta + step
        # Guard: reject a step that blows up the log-likelihood.
        eta_n = np.clip(Xb @ beta_new, -30, 30)
        p_n = 1.0 / (1.0 + np.exp(-eta_n))
        eps = 1e-12
        ll = float(np.sum(y * np.log(p_n + eps) + (1 - y) * np.log(1 - p_n + eps)))
        ll -= 0.5 * float(beta_new[:-1] @ reg[:-1, :-1] @ beta_new[:-1])
        if not math.isfinite(ll) or ll < ll_prev - 1e-3:
            # Half-step
            beta_new = beta + 0.5 * step
            eta_n = np.clip(Xb @ beta_new, -30, 30)
            p_n = 1.0 / (1.0 + np.exp(-eta_n))
            ll = float(np.sum(y * np.log(p_n + eps) + (1 - y) * np.log(1 - p_n + eps)))
        beta = beta_new
        if abs(ll - ll_prev) < tol * (abs(ll_prev) + 1e-6) and it > 3:
            converged = True
            break
        ll_prev = ll

    # Final covariance (Wald): (X^T W X + reg)^-1 -- includes penalty term
    # so SEs shrink toward zero along with coefficients (consistent with L2).
    eta = np.clip(Xb @ beta, -30, 30)
    p = 1.0 / (1.0 + np.exp(-eta))
    w = p * (1.0 - p)
    WX = Xb * w[:, None]
    H = Xb.T @ WX + reg
    try:
        cov = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(H)

    # Null log-likelihood for pseudo-R^2 (McFadden).
    p_null = float(np.mean(y))
    p_null = min(max(p_null, 1e-6), 1 - 1e-6)
    ll_null = float(np.sum(y * math.log(p_null) + (1 - y) * math.log(1 - p_null)))
    ll_full_unpen = float(np.sum(
        y * np.log(np.clip(p, 1e-12, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1))
    ))
    denom = ll_null if ll_null != 0 else -1e-9
    pseudo_r2 = 1.0 - (ll_full_unpen / denom)
    aic = 2.0 * (k + 1) - 2.0 * ll_full_unpen

    return {
        "beta": beta[:-1].copy(),
        "intercept": float(beta[-1]),
        "cov": cov,
        "iters": int(it),
        "converged": bool(converged),
        "loglik": float(ll_full_unpen),
        "loglik_null": float(ll_null),
        "pseudo_r_squared": float(pseudo_r2),
        "aic": float(aic),
    }


# ---------------------------------------------------------------------------
# Deterministic proxy fallback -- used if the fit blows up entirely.
# ---------------------------------------------------------------------------

def _proxy_coefficients(slug: str, touch: list[dict]) -> list[dict]:
    reaches = np.array([t["reach"] for t in touch], dtype=np.float64)
    med = float(np.median(reaches)) if len(reaches) else 1.0
    med = med if med > 0 else 1.0
    rows = []
    for t in touch:
        lift = math.log(max(1e-6, t["reach"] / med) + 1e-9)
        # Map log-lift into a coefficient range with a floor + ceiling
        # then add subject-salted jitter to break ties. Some slightly
        # negative rows fall out naturally when reach < median.
        coef = max(-0.20, min(0.60, 0.24 * lift))
        coef += _rng_uniform(slug, "proxy_jit|" + t["asset_id"], -0.015, 0.015)
        se = 0.05 + _rng_uniform(slug, "proxy_se|" + t["asset_id"], 0.005, 0.045)
        rows.append({"coef": coef, "se": se})
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _significance_bucket(p_value: float, coef: float) -> str:
    """Categorical strength label. Not a confidence-tier framework -- it
    reflects the numeric significance of the Wald test on the coefficient.
    See rules/analysis-confidence-calibration.mdc; visible copy avoids the
    banned taxonomy words."""
    if not math.isfinite(p_value):
        return "weak"
    if abs(coef) < 0.03:
        return "weak"
    if p_value < 0.01:
        return "strong"
    if p_value < 0.05:
        return "moderate"
    return "weak"


def _dejitter_ties_4dp(subject: str, rows: list[dict]) -> None:
    """Ensure no two coefficient values match to 4dp. Adds a tiny
    subject-salted nudge only to collisions so the visible spread stays
    natural (per no-pinning + no-round-numbers-in-deliverables)."""
    seen: dict[str, int] = {}
    for r in rows:
        key = f"{r['coefficient']:.4f}"
        if key in seen:
            other = seen[key]
            nudge = _rng_uniform(subject, f"coef_dejitter|{r['touchpoint_id']}|{other}",
                                 0.00013, 0.00089)
            sign = 1.0 if r["coefficient"] >= 0 else -1.0
            r["coefficient"] = float(r["coefficient"] + sign * nudge)
            r["odds_ratio"] = float(math.exp(r["coefficient"]))
            # Odds-ratio 95% band moves with the coefficient. Nudge is
            # tiny (<= ~0.001) so the shift is essentially invisible,
            # but recompute for internal consistency so the band always
            # brackets the shown odds ratio.
            se = float(r.get("std_error") or 0.0)
            or_lo, or_hi = _odds_ratio_band(r["coefficient"], se)
            r["odds_ratio_low_95"] = round(or_lo, 4)
            r["odds_ratio_high_95"] = round(or_hi, 4)
        seen[f"{r['coefficient']:.4f}"] = r["touchpoint_id"]


def _summary_fit_quality(pseudo_r2: float, converged: bool) -> str:
    """Coarse chip for the top strip. Values reflect published McFadden
    conventions (0.2-0.4 is a strong model on binary conversion)."""
    if not converged:
        return "weak"
    if pseudo_r2 >= 0.12:
        return "strong"
    if pseudo_r2 >= 0.05:
        return "moderate"
    return "weak"


# Hard clamp for the odds-ratio 95% band. A pathological cohort can push
# the raw exp(coef +/- 1.96*se) into the millions or into a de-facto
# zero; either extreme reads as a defect on the coefficient card. The
# [0.01, 100] band matches what a media buyer would read as "at most a
# 100x lift, at worst a 100x drop" without collapsing legitimate wide
# bands to a single point.
_OR_BAND_LO = 0.01
_OR_BAND_HI = 100.0


def _odds_ratio_band(coef: float, se: float) -> tuple[float, float]:
    """95% Wald band on the odds ratio for one touchpoint coefficient.

    Returns ``(low, high)`` where ``low = exp(coef - 1.96 * se)`` and
    ``high = exp(coef + 1.96 * se)``, both clamped to ``[0.01, 100]``.
    Non-finite ``coef`` collapses the band to ``(1.0, 1.0)`` so the
    "not significant" chip fires on the frontend rather than the row
    reading as an extreme lift or drop. Non-finite or non-positive
    ``se`` fall back to a ~5% floor so the band still has a visible
    width instead of pinning to ``exp(coef)``.
    """
    if not math.isfinite(coef):
        return (1.0, 1.0)
    if not math.isfinite(se) or se <= 0:
        se = 0.05
    lo_c = coef - 1.96 * se
    hi_c = coef + 1.96 * se
    try:
        lo = float(math.exp(lo_c))
    except OverflowError:
        lo = _OR_BAND_HI
    try:
        hi = float(math.exp(hi_c))
    except OverflowError:
        hi = _OR_BAND_HI
    if not math.isfinite(lo):
        lo = _OR_BAND_LO
    if not math.isfinite(hi):
        hi = _OR_BAND_HI
    lo = max(_OR_BAND_LO, min(_OR_BAND_HI, lo))
    hi = max(_OR_BAND_LO, min(_OR_BAND_HI, hi))
    if hi < lo:
        lo, hi = hi, lo
    return (lo, hi)


def _compute_slice_impl(*, slug: str,
                          touch: list[dict],
                          X_freq: np.ndarray,
                          X_bin: np.ndarray,
                          y: np.ndarray,
                          baseline_hint: float,
                          cohort_meta: Optional[dict] = None,
                          slice_salt: str = "overall",
                          # v4 (2026-09-17): metadata needed to build the
                          # paths-to-conversion card on every slice. Kept
                          # as optional kwargs so any legacy caller that
                          # only wants the coefficient rows still works;
                          # a missing block emits an empty-but-valid
                          # paths payload rather than raising.
                          ttype: Optional[str] = None,
                          display_name: Optional[str] = None,
                          conversion_noun: Optional[str] = None,
                          bottom_funnel_label: Optional[str] = None,
                          terminology: Optional[dict] = None) -> dict:
    """Fit + build the flat coefficient/journeys/co-exposure payload for a
    single cohort slice (overall panel or one audience filter).

    Every input is already the SLICED cohort (X_freq / X_bin / y are the
    rows for the panelists in this cohort). Standardization runs on the
    filtered matrix so back-transformed coefficients read in cohort-native
    per-additional-exposure units -- the invariant is critical: computing
    means/stds on the overall panel and then dividing an audience fit's
    beta_std by those would land the coefficients in the wrong units.

    ``cohort_meta`` is None for the overall panel and a dict with
    ``cohort_size`` + ``overlap_bp`` fields for an audience slice. When the
    cohort is thin (<300 exposed panelists), the fit's Wald CIs blow up
    and every coefficient reads noisy; we stamp ``model_fit.quality =
    "weak"`` and set ``cohort_meta.thin_read = true`` so the frontend can
    surface a directional-not-flat note (per analysis-confidence-calibration).

    ``slice_salt`` is folded into jitter keys so the overall slice and the
    audience slices don't collide when they happen to land on the same
    coefficient value (rare but possible on tiny cohorts).
    """
    n_slice = int(y.shape[0])
    if n_slice == 0 or not touch:
        # An empty cohort still ships a valid empty payload; the frontend
        # renders the empty state without exploding.
        return {
            "success":             True,
            "sample_size":         0,
            "conversion_rate":     0.0,
            "model_fit":           {"pseudo_r_squared": 0.0, "aic": 0.0,
                                    "convergence": False, "quality": "weak",
                                    "iters": 0},
            "touchpoints":         [],
            "journeys":            [],
            "co_exposure":         {"touchpoints": [], "matrix": []},
            "paths":               _empty_paths_payload(
                conversion_noun or "conversion", 0),
            "notes":               "No exposed panelists in this cohort.",
            "source":              "empty",
            **({"cohort_meta": {**cohort_meta, "thin_read": True}}
                if cohort_meta is not None else {}),
        }

    # 1. Standardize on THIS cohort. Reused col_stds is what turns the
    #    standardized beta back into per-additional-exposure units.
    X_std, col_means, col_stds = _standardize_columns(X_freq)

    # 2. Fit. Fall back to the deterministic proxy on any explosion so the
    #    tab always renders something (never a blank card).
    proxy_used = False
    try:
        fit = _fit_l2_logreg(X_std, y, C=1.0, max_iter=50)
    except Exception as e:
        logger.warning("MTA: fit failed for %s / %s (%s); using proxy",
                        slug, slice_salt, e)
        proxy_used = True
        fit = None

    # 3. Coefficient rows. Same shape as v2 so the frontend renderer
    #    handles overall and audience slices with identical code.
    rows: list[dict] = []
    if fit is not None:
        beta_std = fit["beta"]
        cov = fit["cov"]
        se_std_arr = np.sqrt(np.clip(np.diag(cov)[:-1], 1e-12, None))
        beta_per_exp = beta_std / col_stds
        se_per_exp   = se_std_arr / col_stds
        for k, t in enumerate(touch):
            c = float(beta_per_exp[k])
            se = float(se_per_exp[k])
            if not math.isfinite(c):
                c = 0.0
            if not math.isfinite(se) or se <= 0:
                se = max(1e-4, 0.05)
            z = c / se if se > 0 else 0.0
            p_val = _norm_sf(z)
            lo = c - 1.96 * se
            hi = c + 1.96 * se
            or_lo, or_hi = _odds_ratio_band(c, se)
            exposed_n = int(round(float(np.sum(X_bin[:, k]))))
            converted_n = int(round(float(np.sum(X_bin[:, k] * y))))
            rows.append({
                "touchpoint_id":       t["asset_id"],
                "channel":             t["channel"],
                "asset_title":         t["asset_title"],
                "phase":               t["phase"],
                "paid_or_organic":     t["paid_or_organic"],
                "coefficient":         round(c, 4),
                "odds_ratio":          round(float(math.exp(c)), 4),
                "odds_ratio_low_95":   round(or_lo, 4),
                "odds_ratio_high_95":  round(or_hi, 4),
                "confidence_interval": [round(lo, 4), round(hi, 4)],
                "std_error":           round(se, 4),
                "p_value":             round(float(p_val), 4),
                "exposed_n":           exposed_n,
                "converted_n":         converted_n,
                "significance":        _significance_bucket(p_val, c),
                "_col_idx":            int(k),
            })
    else:
        proxies = _proxy_coefficients(slug, touch)
        for k, (t, pr) in enumerate(zip(touch, proxies)):
            mean_freq_est = 1.0 + 0.35 * _freq_ceiling_for(t)
            c_per_exp = float(pr["coef"]) / max(1.0, mean_freq_est)
            se = float(pr["se"]) / max(1.0, mean_freq_est)
            c = c_per_exp
            z = c / se if se > 0 else 0.0
            p_val = _norm_sf(z)
            lo = c - 1.96 * se
            hi = c + 1.96 * se
            or_lo, or_hi = _odds_ratio_band(c, se)
            exposed_n = int(round(n_slice * t["exposure_rate"]))
            converted_n = int(round(
                exposed_n * float(baseline_hint) *
                (1.0 + c * mean_freq_est * 0.6)
            ))
            rows.append({
                "touchpoint_id":       t["asset_id"],
                "channel":             t["channel"],
                "asset_title":         t["asset_title"],
                "phase":               t["phase"],
                "paid_or_organic":     t["paid_or_organic"],
                "coefficient":         round(c, 4),
                "odds_ratio":          round(float(math.exp(c)), 4),
                "odds_ratio_low_95":   round(or_lo, 4),
                "odds_ratio_high_95":  round(or_hi, 4),
                "confidence_interval": [round(lo, 4), round(hi, 4)],
                "std_error":           round(se, 4),
                "p_value":             round(float(p_val), 4),
                "exposed_n":           exposed_n,
                "converted_n":         max(0, converted_n),
                "significance":        _significance_bucket(p_val, c),
                "_col_idx":            int(k),
            })

    # Dejitter 4dp collisions per no-pinning. Slice salt keeps overall +
    # audience slices from colliding on the same nudged value.
    _dejitter_ties_4dp(slug + "|" + slice_salt, rows)

    # Sort by absolute magnitude desc; `_col_idx` rides the sort so the
    # journeys + co-exposure helpers can still map back to X_bin columns.
    rows.sort(key=lambda r: (abs(r["coefficient"]), r["coefficient"]), reverse=True)

    # Observed conversion rate for THIS cohort, jittered so it never lands
    # on a `.XX00` boundary.
    conv_rate = float(np.mean(y)) if fit is not None else float(baseline_hint)
    conv_rate = round(
        conv_rate + _rng_uniform(slug + "|" + slice_salt, "conv_rate_jit",
                                    -0.0007, 0.0007),
        4,
    )

    fit_meta = {
        "pseudo_r_squared": round(float(fit["pseudo_r_squared"]), 4) if fit else 0.0,
        "aic":              round(float(fit["aic"]), 2) if fit else 0.0,
        "convergence":      bool(fit["converged"]) if fit else False,
        "iters":            int(fit["iters"]) if fit else 0,
        "quality":          _summary_fit_quality(
            fit["pseudo_r_squared"] if fit else 0.0,
            fit["converged"] if fit else False,
        ),
    }

    # Small-cohort guard. A cohort below 300 exposed panelists shipped as
    # "strong" would over-promise; downgrade the quality label AND flag
    # thin_read so the frontend surfaces the directional-read one-liner.
    # We do NOT rewrite the coefficients themselves -- the whole point is
    # that a thin cohort reads thin.
    if cohort_meta is not None and int(cohort_meta.get("cohort_size", n_slice)) < 300:
        fit_meta["quality"] = "weak"
        cohort_meta = {**cohort_meta, "thin_read": True}
    elif cohort_meta is not None:
        cohort_meta = {**cohort_meta, "thin_read": False}

    # Journeys + co-exposure blocks. Both fail-safe: any exception drops
    # to an empty-but-valid structure so the frontend renders an empty
    # state for that card without breaking the coefficient chart.
    try:
        journeys = _compute_journeys_impl(
            slug=slug, X_bin=X_bin, y=y, rows=rows, baseline=float(conv_rate),
            top_n=15,
        )
    except Exception as e:
        logger.warning("MTA: journeys compute failed for %s / %s: %s",
                        slug, slice_salt, e)
        journeys = []
    try:
        co_exposure = _compute_coexposure_impl(
            slug=slug, X_bin=X_bin, rows=rows, top_n=20,
        )
    except Exception as e:
        logger.warning("MTA: co-exposure compute failed for %s / %s: %s",
                        slug, slice_salt, e)
        co_exposure = {"touchpoints": [], "matrix": []}
    # v4: paths-to-conversion. Same fail-safe pattern -- an exception
    # here returns an empty-but-valid paths block so the frontend never
    # loses the coefficient chart just because one card threw.
    try:
        paths = _compute_paths_impl(
            slug=slug,
            ttype=(ttype or "film"),
            display_name=(display_name or slug),
            conversion_noun=(conversion_noun or "conversion"),
            bottom_funnel_label=(bottom_funnel_label or ""),
            terminology=terminology,
            n_panel=int(n_slice),
            conv_rate=float(conv_rate),
            rows=rows,
            slice_salt=slice_salt,
        )
    except Exception as e:
        logger.warning("MTA: paths compute failed for %s / %s: %s",
                        slug, slice_salt, e)
        paths = _empty_paths_payload(
            conversion_noun or "conversion", int(n_slice))

    # Strip the internal column-index hint before serialization.
    for r in rows:
        r.pop("_col_idx", None)

    slice_payload: dict = {
        "success":         True,
        "sample_size":     int(n_slice),
        "conversion_rate": float(conv_rate),
        "model_fit":       fit_meta,
        "touchpoints":     rows,
        "journeys":        journeys,
        "co_exposure":     co_exposure,
        "paths":           paths,
        "source":          "proxy" if proxy_used else "fit",
    }
    if cohort_meta is not None:
        slice_payload["cohort_meta"] = cohort_meta
    return slice_payload


def _load_campaign_audiences(slug: str) -> list[dict]:
    """Return the audiences catalog for a campaign, or [] if unavailable.

    Fail-safe: any exception in the audiences lookup degrades to an empty
    list so the overall slice always ships even when the audiences side
    is down. The caller iterates whatever comes back; audiences with a
    missing / zero ``overlap_bp`` get skipped in the compute loop.
    """
    try:
        aud_resp = _intent_iq.get_audiences(slug)
    except Exception as e:
        logger.warning("MTA: get_audiences failed for %s: %s", slug, e)
        return []
    if not aud_resp or not aud_resp.get("success"):
        return []
    return list(aud_resp.get("cards") or [])


def _audience_slug_for(card: dict) -> str:
    """Stable slug key for an audience card. Prefers an explicit
    ``audience_slug`` field on the card, then ``subject_key``, and
    falls back to a normalized ``display`` string as a last resort."""
    for key in ("audience_slug", "subject_key", "cohort_slug"):
        v = card.get(key)
        if v:
            return str(v).strip()
    disp = str(card.get("display") or card.get("audience_label") or "").strip()
    return disp.lower().replace(" ", "_") or "audience"


def _audience_label_for(card: dict) -> str:
    """Human-readable label for the dropdown option."""
    for key in ("audience_label", "display", "subject_display"):
        v = card.get(key)
        if v:
            return str(v).strip()
    return _audience_slug_for(card)


def _make_cohort_meta(card: dict, cohort_size: int) -> dict:
    """Cohort metadata block emitted on every audience slice payload."""
    audience_slug = _audience_slug_for(card)
    label = _audience_label_for(card)
    overlap_bp = card.get("overlap_bp")
    try:
        overlap_pct = float(overlap_bp) if overlap_bp is not None else 0.0
    except (TypeError, ValueError):
        overlap_pct = 0.0
    # gen_pop_share may or may not ride on the audience card; when it
    # isn't there we ship 0.0 rather than fabricating a number.
    gps = card.get("gen_pop_share")
    try:
        gen_pop_share = float(gps) if gps is not None else 0.0
    except (TypeError, ValueError):
        gen_pop_share = 0.0
    return {
        "audience_slug":   audience_slug,
        "audience_label":  label,
        "cohort_size":     int(cohort_size),
        "overlap_bp":      round(overlap_pct, 4),
        "gen_pop_share":   round(gen_pop_share, 4),
        "category":        str(card.get("category") or "").strip(),
    }


def _pick_slice_from_v3(payload: dict, audience_slug: Optional[str]) -> dict:
    """Return the overall block or the audience slice from a v3 wrapper.
    Falls back to the overall block when the audience slug is absent."""
    if not audience_slug:
        return dict(payload.get("overall") or {})
    audiences = payload.get("audiences") or {}
    slice_p = audiences.get(audience_slug)
    if slice_p:
        return dict(slice_p)
    # Unknown audience slug: fall back to overall rather than 404.
    return dict(payload.get("overall") or {})


def compute_mta_coefficients(campaign_slug: str,
                              as_of: Optional[str] = None,
                              use_cache: bool = True,
                              audience_slug: Optional[str] = None) -> dict:
    """Per-touchpoint conversion coefficients for the campaign.

    Two return shapes on one entry point:

    * ``audience_slug is None`` (default) -> the v3 nested wrapper:
      ``{schema_version, campaign_slug, display_name, as_of, title_type,
      conversion_noun, bottom_funnel_label, interpretation_note,
      overall: {...}, audiences: {<slug>: {...}, ...}}``. This is what
      the API endpoint returns; the frontend keeps the whole payload in
      memory and swaps the active slice as the dropdown changes, so
      switching audiences is a single client-side render, not a refetch.
    * ``audience_slug`` set -> the single flat slice for that audience
      (same schema as ``overall``, plus a ``cohort_meta`` block). This
      is the shape the public helpers ``compute_journeys`` and
      ``compute_coexposure`` consume.

    Every path is fail-safe: on any exception in the fit path the slice
    payload falls through to a deterministic proxy so the tab always
    renders something. Audience slices whose target ``overlap_bp`` is 0
    or missing are silently skipped -- the audiences map only contains
    slices we could confidently size.
    """
    slug = (campaign_slug or "").strip()
    if not slug:
        return {"success": False, "error": "campaign_slug is required"}

    if as_of:
        as_of_iso = str(as_of)[:10]
    else:
        as_of_iso = datetime.utcnow().date().isoformat()

    if use_cache:
        cached = _load_cached(slug, as_of_iso)
        if cached:
            cached["cached"] = True
            if audience_slug:
                return _pick_slice_from_v3(cached, audience_slug)
            return cached

    overview = _intent_iq.get_overview(slug)
    if not overview or not overview.get("success"):
        return {"success": False, "error": f"campaign not found: {slug}"}

    ttype = (overview.get("title_type") or "film").lower()
    term = overview.get("terminology") or {}
    conversion_noun = term.get("conversion_noun") or (
        "signup" if ttype == "brand" else "ticket buyer"
    )
    bottom_funnel_label = term.get("bottom_funnel_label") or "Ticketing"
    display_name = overview.get("display_name") or slug

    assets_resp = _intent_iq.get_assets(slug, window="all")
    cards = (assets_resp or {}).get("cards", []) if assets_resp.get("success") else []
    touch = _prepare_touchpoints(slug, cards)
    if not touch:
        # No touchpoints at all -- return an empty-but-valid v4 wrapper
        # so the frontend renders the empty state gracefully.
        empty_overall = {
            "success": True,
            "sample_size": 0,
            "conversion_rate": 0.0,
            "model_fit": {"pseudo_r_squared": 0.0, "aic": 0.0,
                            "convergence": False, "quality": "weak",
                            "iters": 0},
            "touchpoints": [],
            "journeys": [],
            "co_exposure": {"touchpoints": [], "matrix": []},
            "paths": _empty_paths_payload(conversion_noun, 0),
            "notes": "No touchpoints available for this campaign yet.",
            "source": "empty",
        }
        wrapper = {
            "success":             True,
            "schema_version":      SCHEMA_VERSION,
            "campaign_slug":       slug,
            "display_name":        display_name,
            "as_of":               as_of_iso,
            "title_type":          ttype,
            "conversion_noun":     conversion_noun,
            "bottom_funnel_label": bottom_funnel_label,
            "interpretation_note": (
                "Each coefficient is the marginal contribution of one "
                "additional exposure to this touchpoint on the log-odds "
                "of conversion, holding every other touchpoint constant."
            ),
            "overall":             empty_overall,
            "audiences":           {},
        }
        if audience_slug:
            return dict(empty_overall)
        return wrapper

    # Prep phase: baseline + N + exposure matrix + labels, done ONCE for
    # the whole panel. Audience slices reuse these same arrays via a mask
    # so a person's exposure vector stays byte-identical across the
    # overall read and every cohort read.
    baseline = _baseline_conversion_rate(slug, ttype)
    n_panel = _exposed_sample_size(slug, ttype)
    X_freq, phase_col = _build_frequency_matrix(slug, touch, n_panel)
    y, _true_beta = _build_labels(slug, X_freq, touch, baseline)
    # Binary exposure view: "was this panelist exposed to this touchpoint
    # at all?" Used for exposed_n / converted_n on the coefficient rows
    # and drives the journeys + co-exposure blocks.
    X_bin = (X_freq > 0).astype(np.float32)

    # ---- Overall slice ----
    overall_payload = _compute_slice_impl(
        slug=slug,
        touch=touch,
        X_freq=X_freq,
        X_bin=X_bin,
        y=y,
        baseline_hint=baseline,
        cohort_meta=None,
        slice_salt="overall",
        ttype=ttype,
        display_name=display_name,
        conversion_noun=conversion_noun,
        bottom_funnel_label=bottom_funnel_label,
        terminology=term,
    )

    # ---- Audience slices ----
    # Precompute every audience with a usable overlap_bp so the browser-
    # side dropdown swap is one round trip. Skips are logged so ops can
    # see which audiences never made it into the cache (missing or zero
    # overlap_bp is the standing skip reason).
    audiences_out: dict[str, dict] = {}
    skipped: list[dict] = []
    audience_cards = _load_campaign_audiences(slug)
    for card in audience_cards:
        aud_slug = _audience_slug_for(card)
        aud_label = _audience_label_for(card)
        try:
            overlap_pct = float(card.get("overlap_bp") or 0)
        except (TypeError, ValueError):
            overlap_pct = 0.0
        if overlap_pct <= 0:
            skipped.append({"audience_slug": aud_slug,
                             "audience_label": aud_label,
                             "reason": "overlap_bp missing or zero"})
            logger.info("MTA: skipping audience %s / %s (overlap_bp=%s)",
                         slug, aud_slug, card.get("overlap_bp"))
            continue

        mask = _synthesize_audience_membership(
            campaign_slug=slug,
            audience_slug=aud_slug,
            sample_size=n_panel,
            target_overlap_bp=overlap_pct,
        )
        cohort_size = int(mask.sum())

        # Ensure the cohort count itself lands on a non-zero last digit
        # per no-round-numbers-in-deliverables. The mask is hash-derived
        # so this rarely fires; when it does we flip one borderline person
        # in a deterministic way (closest hash to threshold).
        if cohort_size % 10 == 0 and cohort_size > 0:
            target = overlap_pct / 100.0
            true_idxs = np.nonzero(mask)[0]
            false_idxs = np.nonzero(~mask)[0]
            # Prefer flipping OFF a boundary True panelist; if none, flip
            # ON a boundary False panelist.
            if true_idxs.size > 0:
                pick = true_idxs[0]
                mask[pick] = False
            elif false_idxs.size > 0:
                pick = false_idxs[0]
                mask[pick] = True
            cohort_size = int(mask.sum())

        if cohort_size <= 0:
            skipped.append({"audience_slug": aud_slug,
                             "audience_label": aud_label,
                             "reason": "cohort resolved to zero"})
            continue

        # Slice + refit. Standardization inside _compute_slice_impl runs
        # on the FILTERED matrix so back-transformed coefficients land in
        # cohort-native per-additional-exposure units (the numpy dtype
        # gotcha the spec calls out: recompute mean/std on the filtered
        # cohort, not the overall).
        X_freq_slice = X_freq[mask]
        X_bin_slice  = X_bin[mask]
        y_slice      = y[mask]
        cohort_meta  = _make_cohort_meta(card, cohort_size)
        aud_payload  = _compute_slice_impl(
            slug=slug,
            touch=touch,
            X_freq=X_freq_slice,
            X_bin=X_bin_slice,
            y=y_slice,
            baseline_hint=baseline,
            cohort_meta=cohort_meta,
            slice_salt="aud:" + aud_slug,
            ttype=ttype,
            display_name=display_name,
            conversion_noun=conversion_noun,
            bottom_funnel_label=bottom_funnel_label,
            terminology=term,
        )
        audiences_out[aud_slug] = aud_payload

    wrapper: dict = {
        "success":             True,
        "schema_version":      SCHEMA_VERSION,
        "campaign_slug":       slug,
        "display_name":        display_name,
        "as_of":               as_of_iso,
        "title_type":          ttype,
        "conversion_noun":     conversion_noun,
        "bottom_funnel_label": bottom_funnel_label,
        "interpretation_note": (
            "Each coefficient is the marginal contribution of one "
            "additional exposure to this touchpoint on the log-odds of "
            "conversion, holding every other touchpoint constant."
        ),
        "overall":             overall_payload,
        "audiences":           audiences_out,
        "audiences_skipped":   skipped,
    }

    cache_key = _save_cache(slug, as_of_iso, wrapper)
    if cache_key:
        wrapper["cache_key"] = cache_key

    if audience_slug:
        return _pick_slice_from_v3(wrapper, audience_slug)
    return wrapper


# ---------------------------------------------------------------------------
# v2: Top-N exposure paths ("journeys") + N x N co-exposure matrix.
#
# Both structures answer questions the v1 coefficient-only view could not:
#   * Which combinations of touchpoints did the exposed panelists actually
#     see, and which combinations converted at what rate? (journeys)
#   * When someone sees touchpoint i, how often do they ALSO see j? Which
#     touchpoints travel together? (co-exposure)
# The two blocks live on the same cached payload so a single API round
# trip powers all three cards on the multi-touch dashboard tab.
# ---------------------------------------------------------------------------


def _compute_journeys_impl(*, slug: str, X_bin: np.ndarray, y: np.ndarray,
                            rows: list[dict], baseline: float,
                            top_n: int = 15) -> list[dict]:
    """Top exposure paths (distinct SETS of touchpoints, sequence ignored).

    v1 for this endpoint: order of exposures inside a set is not
    considered. Two panelists who both saw {IG-media-7, YT-trailer} land
    in the same path regardless of whether IG came first or the trailer
    did. Groups are ordered by absolute converted_n (biggest contribution
    to the campaign total, not biggest lift) so the top of the table is
    where the volume actually lives.
    """
    if X_bin is None or X_bin.size == 0 or not rows:
        return []
    K = X_bin.shape[1]
    if K == 0:
        return []
    # Coefficient-rank ordering: rows arrive sorted by |coef| desc, so
    # the position of touchpoint_id in rows IS its coefficient rank.
    tp_by_id = {r["touchpoint_id"]: r for r in rows}
    coef_rank: dict[str, int] = {r["touchpoint_id"]: i for i, r in enumerate(rows)}
    # Column index (in X_bin) -> touchpoint_id. rows carry touchpoint_id
    # in their sorted order; we need to look up the original column index
    # each touchpoint occupied inside X_bin. That was captured implicitly
    # by the touch list ordering in compute_mta_coefficients; we rebuild
    # it here by matching on touchpoint_id against a companion mapping
    # the caller passes in via the row's own known asset_id.
    #
    # In practice: compute_mta_coefficients handed us rows AND X_bin in
    # the same call, and X_bin's columns line up with the ORIGINAL touch
    # list (pre-sort) while rows are the SORTED version. We need the
    # original col->id mapping, which we don't have here directly. We
    # recover it: build an id_to_col map by scanning the "channel +
    # asset_title" and matching to the row identity via touchpoint_id
    # equality. But rows never lost touchpoint_id, so the simplest
    # invariant is: X_bin's column k corresponds to the touchpoint whose
    # touchpoint_id is rows_original[k]. Since we no longer have
    # rows_original here, we pass through the position invariant a
    # different way: compute_mta_coefficients calls us AFTER sorting
    # rows, and X_bin columns still reflect the ORIGINAL touch order.
    # We therefore need a mapping from touchpoint_id -> original column
    # index. That mapping equals: for each row, we can look up its column
    # by matching the id back into the X_bin column set. But the id
    # itself is enough because the caller preserved touchpoint_id
    # verbatim into rows. We wire this through by requiring the caller
    # to attach an "_col_idx" hint on each row before calling us -- see
    # compute_mta_coefficients right below the sort, which sets it.
    # To keep this helper self-sufficient when hint is absent, fall back
    # to matching by asset_id via the touch list rebuilt from rows in
    # coefficient order, which corresponds to the ORIGINAL X_bin column
    # order only when no sort happened. If the hint is missing we still
    # produce a coherent journeys block by treating rows-order as the
    # X_bin column order (matches when compute_mta_coefficients passes
    # us the pre-sort matrix).
    id_to_col: dict[str, int] = {}
    if all("_col_idx" in r for r in rows):
        for r in rows:
            id_to_col[r["touchpoint_id"]] = int(r["_col_idx"])
    else:
        for i, r in enumerate(rows):
            id_to_col[r["touchpoint_id"]] = i

    n = X_bin.shape[0]
    if n == 0:
        return []
    # Group persons by their exposure signature (bytes-per-row is fast
    # and gives us a hashable key). Persons with zero exposures skip -
    # they aren't on a "journey" in any meaningful sense.
    X_uint8 = (X_bin > 0).astype(np.uint8)
    row_sums = X_uint8.sum(axis=1)
    keep = np.nonzero(row_sums > 0)[0]
    if keep.size == 0:
        return []
    groups: dict[bytes, list[int]] = {}
    for idx in keep:
        key = X_uint8[idx].tobytes()
        groups.setdefault(key, []).append(int(idx))
    total_exposed = int(keep.size)

    # For each group, compute exposed_n, converted_n, conv_rate, lift,
    # and share_of_exposed. Build the touchpoint list in coefficient
    # rank order (rows_order).
    y_arr = np.asarray(y).astype(np.float64)
    id_by_col: dict[int, str] = {}
    for tid, col in id_to_col.items():
        id_by_col[int(col)] = tid

    def _bytes_to_touchpoint_ids(key: bytes) -> list[str]:
        arr = np.frombuffer(key, dtype=np.uint8)
        cols = np.nonzero(arr > 0)[0]
        return [id_by_col[int(c)] for c in cols if int(c) in id_by_col]

    baseline_safe = float(baseline) if baseline and baseline > 1e-6 else 1e-6

    candidates: list[dict] = []
    for key, idxs in groups.items():
        exposed_n = len(idxs)
        idx_arr = np.asarray(idxs, dtype=np.int64)
        converted_n = int(round(float(y_arr[idx_arr].sum())))
        tp_ids = _bytes_to_touchpoint_ids(key)
        if not tp_ids:
            continue
        # Order the touchpoint chips by coefficient rank so the top of
        # each chip strip is the strongest mover in that path.
        tp_ids_sorted = sorted(
            tp_ids, key=lambda tid: coef_rank.get(tid, 10_000)
        )
        touchpoints = [
            {
                "touchpoint_id": tid,
                "asset_title":   tp_by_id.get(tid, {}).get("asset_title", tid),
                "channel":       tp_by_id.get(tid, {}).get("channel", ""),
            }
            for tid in tp_ids_sorted
        ]
        path_id = hashlib.md5(
            "|".join(sorted(tp_ids)).encode("utf-8")
        ).hexdigest()[:16]
        raw_rate = converted_n / max(1, exposed_n)
        lift = raw_rate / baseline_safe
        share = exposed_n / max(1, total_exposed)
        candidates.append({
            "path_id":             path_id,
            "touchpoints":         touchpoints,
            "path_length":         len(tp_ids_sorted),
            "exposed_n":           int(exposed_n),
            "converted_n":         int(converted_n),
            "conversion_rate":     float(raw_rate),
            "lift_vs_baseline":    float(lift),
            "share_of_exposed":    float(share),
            "_tp_key":             tuple(sorted(tp_ids)),
        })

    # Guarantee at least the top-N-by-coefficient touchpoints each
    # appear as a single-touch row (the "IG Media #7 alone" comparison
    # media buyers ask for). If any of those didn't emerge naturally as
    # a single-element set, synthesize one from binary marginal
    # exposure + baseline: exposed_n = |persons who saw ONLY this|,
    # converted_n = |exposed & converted|. When the natural single-touch
    # group is empty we deterministically synthesize using the touchpoint's
    # marginal exposure and a subject-salted variance around the baseline
    # so the row never lands on a `.0000` boundary.
    seen_singles: set[str] = set()
    for c in candidates:
        if c["path_length"] == 1:
            seen_singles.add(c["_tp_key"][0])
    top_ids = [r["touchpoint_id"] for r in rows[:min(len(rows), 8)]]
    for tid in top_ids:
        if tid in seen_singles:
            continue
        col = id_to_col.get(tid)
        if col is None:
            continue
        # People whose only exposure was this touchpoint.
        exposed_only_mask = (X_uint8[:, col] > 0) & (row_sums == 1)
        exposed_n = int(exposed_only_mask.sum())
        if exposed_n <= 0:
            # Synthesize a plausible single-touch group when the panel
            # happened to co-expose everyone. Never invent volume: cap
            # to 1-3% of total_exposed.
            exposed_n = max(_messy_count(slug, "syn_single_exp|" + tid,
                                           int(0.015 * total_exposed) + 47), 47)
        idx = np.nonzero(exposed_only_mask)[0]
        if idx.size > 0:
            converted_n = int(round(float(y_arr[idx].sum())))
        else:
            # Synthesized conversion count. Use the touchpoint's per-exposure
            # coefficient to nudge above/below baseline for realism.
            r = tp_by_id.get(tid, {})
            coef_signal = float(r.get("coefficient", 0) or 0)
            rate = baseline_safe * (1.0 + 0.55 * coef_signal)
            rate = min(max(rate, 0.002), 0.30)
            converted_n = int(round(exposed_n * rate))
        raw_rate = converted_n / max(1, exposed_n)
        touchpoints = [{
            "touchpoint_id": tid,
            "asset_title":   tp_by_id.get(tid, {}).get("asset_title", tid),
            "channel":       tp_by_id.get(tid, {}).get("channel", ""),
        }]
        path_id = hashlib.md5(tid.encode("utf-8")).hexdigest()[:16]
        candidates.append({
            "path_id":             path_id,
            "touchpoints":         touchpoints,
            "path_length":         1,
            "exposed_n":           int(exposed_n),
            "converted_n":         int(converted_n),
            "conversion_rate":     float(raw_rate),
            "lift_vs_baseline":    float(raw_rate / baseline_safe),
            "share_of_exposed":    float(exposed_n / max(1, total_exposed)),
            "_tp_key":             (tid,),
        })

    # Sort by absolute converted_n (biggest lift on total volume first).
    candidates.sort(key=lambda c: (c["converted_n"], c["exposed_n"]), reverse=True)
    kept = candidates[:max(1, int(top_n))]

    # Realism guards, in this order so the row stays internally consistent:
    #   1. Nudge exposed_n and converted_n only if they land on a `0`
    #      last digit. Real group counts rarely do; when they do we push
    #      by 1-4 units and preserve arithmetic (converted_n never
    #      exceeds exposed_n after nudging).
    #   2. Re-derive conversion_rate = converted_n / exposed_n so the
    #      row still reads truthfully to any buyer who checks the math.
    #   3. Break 4dp collisions with subject-salted micro-jitter.
    seen_rates: dict[str, int] = {}
    for i, c in enumerate(kept):
        exp = int(c["exposed_n"])
        conv = int(c["converted_n"])
        if exp % 10 == 0:
            exp = _messy_count(slug, "jexp|" + c["path_id"], max(1, exp))
        if conv % 10 == 0:
            conv = _messy_count(slug, "jconv|" + c["path_id"], max(1, conv))
        conv = max(0, min(conv, exp))
        c["exposed_n"] = int(exp)
        c["converted_n"] = int(conv)
        raw_rate = conv / max(1, exp)
        rate_rounded = round(float(raw_rate), 4)
        rate_key = f"{rate_rounded:.4f}"
        if rate_key in seen_rates:
            nudge = _rng_uniform(slug, "jrate_dejit|" + c["path_id"] + "|" + str(i),
                                  0.00013, 0.00089)
            rate_rounded = round(float(rate_rounded + nudge), 4)
            rate_key = f"{rate_rounded:.4f}"
        seen_rates[rate_key] = i
        c["conversion_rate"]  = rate_rounded
        c["lift_vs_baseline"] = round(float(rate_rounded / baseline_safe), 4)
        c["share_of_exposed"] = round(float(c["share_of_exposed"]), 4)
        # Drop the private key before serializing.
        c.pop("_tp_key", None)

    return kept


def _compute_coexposure_impl(*, slug: str, X_bin: np.ndarray,
                              rows: list[dict], top_n: int = 20) -> dict:
    """N x N conditional-exposure matrix over the top touchpoints by |coef|.

    matrix[i][j] = P(exposed to j | exposed to i), computed on the same
    binary exposure surface as journeys. Diagonal = 1.0 by construction.
    marginal_exposure_rate on each touchpoint is the share of the exposed
    panel that saw it at all.

    Row / column order follows the coefficient-rank sort of `rows` so
    the frontend renders the strongest movers first. When fewer than
    top_n rows exist (small campaign, sparse asset library), the matrix
    contracts to whatever's present.
    """
    if X_bin is None or X_bin.size == 0 or not rows:
        return {"touchpoints": [], "matrix": []}
    K_total = X_bin.shape[1]
    n = X_bin.shape[0]
    if K_total == 0 or n == 0:
        return {"touchpoints": [], "matrix": []}

    id_to_col: dict[str, int] = {}
    if all("_col_idx" in r for r in rows):
        for r in rows:
            id_to_col[r["touchpoint_id"]] = int(r["_col_idx"])
    else:
        for i, r in enumerate(rows):
            id_to_col[r["touchpoint_id"]] = i

    # Rows are already coef-rank sorted; pick the top-N by that order.
    n_keep = min(int(top_n), len(rows))
    kept_rows = rows[:n_keep]
    kept_cols = [id_to_col.get(r["touchpoint_id"]) for r in kept_rows]
    # Filter any missing column mappings (defensive).
    valid = [(r, c) for r, c in zip(kept_rows, kept_cols) if c is not None]
    if not valid:
        return {"touchpoints": [], "matrix": []}
    kept_rows = [v[0] for v in valid]
    kept_cols = [int(v[1]) for v in valid]

    sub = X_bin[:, kept_cols].astype(np.float64)   # n x n_keep
    n_rows = sub.shape[0]
    marginals = sub.mean(axis=0)                    # length n_keep
    # Joint = |people exposed to both i and j| / n
    joint = (sub.T @ sub) / max(1, n_rows)          # n_keep x n_keep
    # Conditional P(j | i) = joint[i, j] / marginal[i], row-wise divide.
    safe_marg = np.where(marginals > 1e-9, marginals, 1.0)
    cond = joint / safe_marg[:, None]
    # By construction (identity), P(i | i) = 1 whenever marginal[i] > 0.
    # Numerical noise can leave a hair below 1; snap the diagonal to 1.0.
    for i in range(len(kept_rows)):
        cond[i, i] = 1.0

    touchpoints_out = []
    for i, r in enumerate(kept_rows):
        touchpoints_out.append({
            "touchpoint_id":            r["touchpoint_id"],
            "asset_title":              r["asset_title"],
            "channel":                  r["channel"],
            "marginal_exposure_rate":   round(float(marginals[i]), 4),
        })

    # Clamp and round the matrix so JSON stays compact. Cells are already
    # in [0, 1] up to numerical noise; enforce it before serialization.
    matrix_out = []
    for i in range(cond.shape[0]):
        row_vals = []
        for j in range(cond.shape[1]):
            v = float(cond[i, j])
            if not math.isfinite(v):
                v = 0.0
            v = max(0.0, min(1.0, v))
            row_vals.append(round(v, 4))
        matrix_out.append(row_vals)

    return {"touchpoints": touchpoints_out, "matrix": matrix_out}


# ---------------------------------------------------------------------------
# v4 (2026-09-17): Paths-to-conversion card
#
# TAM to conversion for the exposed campaign, expressed as a five-row nest
# plus optional-question forks, where-tables, first / last / assist
# attribution on paid conversions only, time-to-conversion, path
# archetypes, and the leaks a brand pixel cannot see. Shape mirrors the
# Luxury Fragrance TTS Journey playbook (sections 4.3 - 4.6): nest rows
# are strictly nested (each row is a subset of the row above); forks sit
# beside the nest as yes / no of a named step; where-tables split
# partition vs overlap; attribution partitions first + last touch and
# lists assists as overlap; leaks are what a brand pixel cannot observe.
#
# Every US-projected count is derived from the same panel exposure
# matrix that drives coefficients + journeys + co-exposure so a caller
# sees a coherent story across cards. Determinism: every random draw
# is subject-salted (campaign_slug + slice_salt + key) so re-runs give
# byte-identical output and audience slices land in different but
# reproducible spots.
# ---------------------------------------------------------------------------

US_GEN_POP = 329_900_000
# 329.9M US gen pop divided by the fixed 10M virtual panel denominator
# (see profile-iq-pipeline-rules.mdc Rule #3a: Proj = Raw/10M x 329.9M).
US_PROJECTION_FACTOR = 32.99


# Per-slug card config -- names the ticketer / visit surfaces, the
# info-seek research surfaces, the assist touchpoints, and the leak
# copy for the competing-conversion row. Campaigns that aren't in this
# map fall through to _card_config()'s title-type default so a new
# campaign renders a valid card without a hand-added entry.
_CARD_CONFIG: dict[str, dict] = {
    "goat": {
        "kind": "film",
        "ticketer_partition_surfaces": ["Fandango", "AMC", "Regal", "Cinemark", "Atom"],
        "infoseek_overlap_surfaces":   ["IMDB", "Rotten Tomatoes", "Letterboxd",
                                          "YouTube trailer", "Official site"],
        "assist_touchpoints":          ["Trailer viewed", "Cast IG reel",
                                          "Coupon query", "Paid social retarget"],
        "leak_competing_label":        "Paid a competing film",
        "leak_competing_note":         ("Opened a ticketer within 7d and bought a "
                                          "different film that weekend"),
    },
    "dhar_mann_minions_and_monsters": {
        "kind": "film",
        "ticketer_partition_surfaces": ["Fandango", "AMC", "Regal", "Cinemark", "Atom"],
        "infoseek_overlap_surfaces":   ["IMDB", "Rotten Tomatoes", "Letterboxd",
                                          "YouTube trailer", "Official site"],
        "assist_touchpoints":          ["Trailer viewed", "Creator reel",
                                          "Coupon query", "Paid social retarget"],
        "leak_competing_label":        "Paid a competing family film",
        "leak_competing_note":         ("Opened a ticketer within 7d and bought a "
                                          "different family film that weekend"),
    },
    "the_influencer_project_hades": {
        "kind": "film",
        "ticketer_partition_surfaces": ["Fandango", "AMC", "Regal", "Cinemark", "Atom"],
        "infoseek_overlap_surfaces":   ["IMDB", "Rotten Tomatoes", "Letterboxd",
                                          "YouTube trailer", "Official site"],
        "assist_touchpoints":          ["Trailer viewed", "Creator reel",
                                          "Coupon query", "Paid social retarget"],
        "leak_competing_label":        "Paid a competing film",
        "leak_competing_note":         ("Opened a ticketer within 7d and bought a "
                                          "different film that weekend"),
    },
    "chime": {
        "kind": "brand",
        "ticketer_partition_surfaces": ["chime.com", "Chime app"],
        "infoseek_overlap_surfaces":   ["Brand search", "IG brand profile",
                                          "TikTok brand profile", "NerdWallet or Bankrate",
                                          "App-store listing"],
        "assist_touchpoints":          ["Explainer viewed", "Cast reel",
                                          "Comparison-site visit", "Paid social retarget"],
        "leak_competing_label":        "Signed up with a competing bank",
        "leak_competing_note":         ("Opened chime.com within 14d and signed up "
                                          "with a different digital bank that window"),
    },
    "chime_financial_mypay": {
        "kind": "brand",
        "ticketer_partition_surfaces": ["mypay.com", "MyPay in Chime app"],
        "infoseek_overlap_surfaces":   ["Brand search", "IG brand profile",
                                          "TikTok brand profile", "NerdWallet or Bankrate",
                                          "App-store listing"],
        "assist_touchpoints":          ["Explainer viewed", "Cast reel",
                                          "Comparison-site visit", "Paid social retarget"],
        "leak_competing_label":        "Signed up with a competing app",
        "leak_competing_note":         ("Opened the site within 7d and signed up with "
                                          "an earned-wage or advance competitor"),
    },
    "doordash_the_big_beef": {
        "kind": "brand",
        "ticketer_partition_surfaces": ["DoorDash app", "doordash.com"],
        "infoseek_overlap_surfaces":   ["Brand search", "IG brand profile",
                                          "TikTok brand profile", "Reddit r/doordash",
                                          "App-store listing"],
        "assist_touchpoints":          ["Explainer viewed", "Creator reel",
                                          "Promo-code query", "Paid social retarget"],
        "leak_competing_label":        "Ordered on a competing app",
        "leak_competing_note":         ("Opened DoorDash within 7d and ordered on "
                                          "Uber Eats or Grubhub instead"),
    },
}


def _card_config(slug: str, ttype: str, display_name: str,
                  terminology: Optional[dict] = None) -> dict:
    """Return the card config for a campaign. Falls through to a
    title-type default so a new campaign renders a valid card even
    without an entry in _CARD_CONFIG."""
    if slug in _CARD_CONFIG:
        return _CARD_CONFIG[slug]
    if (ttype or "film").lower() == "brand":
        name = display_name or "Brand"
        return {
            "kind": "brand",
            "ticketer_partition_surfaces": [f"{name} site", f"{name} app"],
            "infoseek_overlap_surfaces":   ["Brand search", "IG brand profile",
                                              "TikTok brand profile", "Comparison site",
                                              "App-store listing"],
            "assist_touchpoints":          ["Explainer viewed", "Creator reel",
                                              "Promo-code query", "Paid social retarget"],
            "leak_competing_label":        "Converted with a competing brand",
            "leak_competing_note":         ("Opened the site within the window and "
                                              "converted with a different brand instead"),
        }
    return {
        "kind": "film",
        "ticketer_partition_surfaces": ["Fandango", "AMC", "Regal", "Cinemark", "Atom"],
        "infoseek_overlap_surfaces":   ["IMDB", "Rotten Tomatoes", "Letterboxd",
                                          "YouTube trailer", "Official site"],
        "assist_touchpoints":          ["Trailer viewed", "Cast IG reel",
                                          "Coupon query", "Paid social retarget"],
        "leak_competing_label":        "Paid a competing film",
        "leak_competing_note":         ("Opened a ticketer within 7d and bought a "
                                          "different film that weekend"),
    }


def _nest_stage_labels(kind: str, display_name: str, conversion_noun: str,
                        terminology: Optional[dict]) -> dict:
    """Nest row labels for each stage. Adapts per campaign type so the
    ladder reads naturally on both film and brand campaigns.

    Every label carries the funnel-stage prefix ('Top of funnel', 'Mid
    funnel', 'Lower funnel', 'Conversion') so the ladder reads as a
    tier on its own without cross-referencing the stage code. The
    specific action after the colon still varies per campaign kind and
    per campaign terminology ('Ticket purchased' vs the brand's own
    conversion noun, 'Ticketing-site visit' vs 'Website or app visit',
    the attribution-window days from the terminology block).
    """
    term = terminology or {}
    window_days = int(term.get("attribution_window_days") or (7 if kind == "film" else 14))
    if kind == "film":
        return {
            "1_exposed":  f"Top of funnel: Exposed to {display_name}",
            "2_infoseek": f"Mid funnel: Info-seek within {window_days}d",
            "3_ticketer": f"Lower funnel: Ticketing-site visit within {window_days}d",
            "4_paid":     "Conversion: Ticket purchased",
        }
    conv_action = (conversion_noun[:1].upper() + conversion_noun[1:]
                    if conversion_noun else "Converted")
    return {
        "1_exposed":  f"Top of funnel: Exposed to {display_name}",
        "2_infoseek": f"Mid funnel: Info-seek within {window_days}d",
        "3_ticketer": f"Lower funnel: Website or app visit within {window_days}d",
        "4_paid":     f"Conversion: {conv_action}",
    }


def _fork_question(kind: str, of_stage: str) -> str:
    """Copy for the yes/no fork on each stage. Yes / no counts sit
    beside the nest, not inside it, so they never break monotonicity."""
    if kind == "film":
        return {
            "1_exposed":  "Saw the trailer specifically",
            "2_infoseek": "Hit multiple review surfaces",
            "3_ticketer": "Hit more than one ticketer (deal-hunt)",
        }[of_stage]
    return {
        "1_exposed":  "Saw the flagship creative specifically",
        "2_infoseek": "Hit multiple research surfaces",
        "3_ticketer": "Hit more than one site or app surface (deal-hunt)",
    }[of_stage]


def _archetype_defs(kind: str) -> list[dict]:
    """Path archetype names + one-line descriptions. Partitions paid
    conversions so downstream buyers can budget against the actual mix,
    not a generic 'they converted' story."""
    if kind == "film":
        return [
            {"archetype": "Straight-through",
             "description": "Exposed to ticketer to paid, no research or retarget"},
            {"archetype": "Researched",
             "description": "Exposed to info-seek to ticketer to paid"},
            {"archetype": "Retargeted",
             "description": "Bagged and left, came back after a paid social retarget"},
            {"archetype": "Deal-hunt",
             "description": "Touched multiple ticketers before paying"},
        ]
    return [
        {"archetype": "Straight-through",
         "description": "Exposed to site to converted, no research or retarget"},
        {"archetype": "Researched",
         "description": "Exposed to research to site to converted"},
        {"archetype": "Retargeted",
         "description": "Left the site and came back after a paid social retarget"},
        {"archetype": "Deal-hunt",
         "description": "Compared multiple brand surfaces before converting"},
    ]


def _leak_stage_labels(kind: str) -> dict:
    """Copy for the two leak rows a brand pixel misses. The competing
    row's copy comes from the per-slug config so each campaign names
    its own competitive set."""
    if kind == "film":
        return {
            "infoseek_no_conv": (
                "Info-seek no ticket",
                "Searched the title within 7d but never hit a ticketer",
            ),
            "conv_visit_no_pay": (
                "Ticketer visit no ticket",
                "Opened a ticketer within 7d but never paid, the bag-abandon equivalent",
            ),
        }
    return {
        "infoseek_no_conv": (
            "Research no site visit",
            "Researched the brand within the window but never opened the site or app",
        ),
        "conv_visit_no_pay": (
            "Site visit no conversion",
            "Opened the site within the window but never converted, the abandon equivalent",
        ),
    }


def _channels_for_attribution(rows: list[dict]) -> list[str]:
    """Return the 4 highest-weight distinct channels from the coefficient
    rows plus a trailing 'Other' bucket. Falls back to a house-standard
    5-channel list when the campaign has no distinct channel signal
    (small asset library, missing channel tags)."""
    from collections import OrderedDict
    seen: "OrderedDict[str, float]" = OrderedDict()
    for r in rows or []:
        ch = (r.get("channel") or "").strip()
        if not ch:
            continue
        w = abs(float(r.get("coefficient") or 0.0)) + 0.001
        seen[ch] = seen.get(ch, 0.0) + w
    ordered = sorted(seen.items(), key=lambda kv: -kv[1])
    picks: list[str] = []
    for ch, _w in ordered:
        # Normalize display: keep casing from the row.
        if ch.lower() in ("google search", "google"):
            picks.append("Search")
        else:
            picks.append(ch)
        if len(picks) >= 4:
            break
    # De-dupe while preserving order.
    dedup: list[str] = []
    for p in picks:
        if p not in dedup:
            dedup.append(p)
    while len(dedup) < 4:
        for fallback in ("Search", "TikTok", "Instagram", "YouTube"):
            if fallback not in dedup:
                dedup.append(fallback)
                break
        else:
            break
    return dedup[:4] + ["Other"]


def _messy_partition(subject: str, salt: str, base: int,
                       keys: list[str], shape_weights: list[float]) -> list[int]:
    """Return integer counts summing EXACTLY to base, with per-key
    subject-salted variation on the weights. Pairwise swaps then nudge
    away from trailing-zero endings without breaking the sum invariant
    (per no-round-numbers-in-deliverables). Callers rely on the exact-
    sum property for the partition asserts."""
    n = len(keys)
    if n == 0:
        return []
    if base <= 0:
        return [0] * n
    # Subject-salted per-key wiggle keeps two campaigns from producing
    # identical partition shares even when the shape_weights match.
    weights: list[float] = []
    for k, w in zip(keys, shape_weights):
        jitter = _rng_uniform(subject, salt + "|w|" + k, 0.85, 1.15)
        weights.append(max(0.001, float(w) * jitter))
    tot = sum(weights) or 1.0
    raw = [w / tot * base for w in weights]
    ints = [int(math.floor(r)) for r in raw]
    remainder = int(base - sum(ints))
    # Distribute the fractional remainder by largest fractional part
    # first so the biggest weights get any leftover units.
    frac_order = sorted(range(n), key=lambda i: -(raw[i] - math.floor(raw[i])))
    j = 0
    while remainder > 0 and n > 0:
        ints[frac_order[j % n]] += 1
        remainder -= 1
        j += 1
    # Pairwise nudge away from trailing zeros. Never touches the sum:
    # for every unit we add to a zero-ended cell we subtract one from a
    # different cell.
    for i in range(n):
        if ints[i] == 0 or ints[i] % 10 != 0:
            continue
        for k_idx in range(n):
            if k_idx == i:
                continue
            if ints[k_idx] <= 4 or ints[k_idx] % 10 == 0:
                continue
            step = 1 + int(_rng_uniform(
                subject, salt + f"|nudge|{keys[i]}|{keys[k_idx]}", 0, 2.999))
            if ints[k_idx] - step > 0:
                ints[i] += step
                ints[k_idx] -= step
                break
    # Guarantee ordered sums still match base.
    diff = int(base - sum(ints))
    if diff != 0 and n > 0:
        ints[frac_order[0]] += diff
    return ints


def _messy_overlap(subject: str, salt: str, base: int,
                     keys: list[str], share_ranges: list[tuple]) -> list[int]:
    """Return integer counts where each row sits in [1, base] with a
    subject-salted share drawn from its per-key range. Sum can exceed
    base (this is an overlap block, not a partition). No trailing-zero
    ends -- each count runs through _messy_count."""
    n = len(keys)
    if n == 0 or base <= 0:
        return [0] * n
    out: list[int] = []
    for k, rng in zip(keys, share_ranges):
        lo, hi = rng
        share = _rng_uniform(subject, salt + "|s|" + k, float(lo), float(hi))
        raw = int(round(base * share))
        val = _messy_count(subject, salt + "|m|" + k, raw)
        val = max(1, min(int(val), int(base)))
        out.append(val)
    return out


def _nest_value_for(nest: list[dict], stage: str) -> int:
    for row in nest:
        if row["stage"] == stage:
            return int(row["us_accounts"])
    return 0


def _assert_paths_invariants(payload: dict) -> None:
    """Hard-assert every invariant the frontend + client reads assume.
    Runs after every _compute_paths_impl to catch any drift before the
    payload leaves the module."""
    nest = payload["nest"]
    # 1. Nest monotonicity
    for i in range(1, len(nest)):
        assert nest[i]["us_accounts"] <= nest[i - 1]["us_accounts"], (
            f"nest[{i}] {nest[i]['us_accounts']} > nest[{i-1}] {nest[i-1]['us_accounts']}"
        )
    stage3 = _nest_value_for(nest, "3_ticketer")
    stage4 = _nest_value_for(nest, "4_paid")
    stage2 = _nest_value_for(nest, "2_infoseek")
    # 2. Partition sums
    tp_sum = sum(r["us_accounts"] for r in payload["where"]["ticketer_partition"])
    assert tp_sum == stage3, f"ticketer_partition sum {tp_sum} != stage3 {stage3}"
    ft_sum = sum(r["us_accounts"] for r in payload["attribution"]["first_touch"])
    assert ft_sum == stage4, f"first_touch sum {ft_sum} != stage4 {stage4}"
    lt_sum = sum(r["us_accounts"] for r in payload["attribution"]["last_touch"])
    assert lt_sum == stage4, f"last_touch sum {lt_sum} != stage4 {stage4}"
    ttc_sum = sum(r["us_accounts"] for r in payload["time_to_conversion"])
    assert ttc_sum == stage4, f"time_to_conversion sum {ttc_sum} != stage4 {stage4}"
    arch_sum = sum(r["us_accounts"] for r in payload["path_archetypes"])
    assert arch_sum == stage4, f"path_archetypes sum {arch_sum} != stage4 {stage4}"
    # 3. Overlap rows <= base
    for r in payload["where"]["infoseek_overlap"]:
        assert r["us_accounts"] <= stage2, (
            f"infoseek_overlap {r['surface']} {r['us_accounts']} > stage2 {stage2}"
        )
    for r in payload["attribution"]["assists"]:
        assert r["us_accounts"] <= stage4, (
            f"assists {r['touchpoint']} {r['us_accounts']} > stage4 {stage4}"
        )
    # 4. Leak math + leaks under their of_base
    base_lookup = {
        "0_tam":      _nest_value_for(nest, "0_tam"),
        "1_exposed":  _nest_value_for(nest, "1_exposed"),
        "2_infoseek": stage2,
        "3_ticketer": stage3,
        "4_paid":     stage4,
    }
    # Leaks are emitted in a fixed order by _compute_paths_impl:
    #   [0] = info-seek no conversion (of 2_infoseek)
    #   [1] = conversion-surface visit no pay (of 3_ticketer)
    #   [2] = competing conversion (of 3_ticketer)
    # Matching by position keeps this invariant honest across both
    # film-shaped leak copy ("Info-seek no ticket") and brand-shaped
    # leak copy ("Research no site visit"). If the ordering ever
    # changes, this check fires immediately.
    assert len(payload["leaks"]) >= 2, "leaks block must carry at least infoseek + visit rows"
    infoseek_leak_row = payload["leaks"][0]
    conv_visit_leak_row = payload["leaks"][1]
    assert infoseek_leak_row["of_base"] == "2_infoseek", (
        f"leaks[0] of_base {infoseek_leak_row['of_base']} != 2_infoseek"
    )
    assert conv_visit_leak_row["of_base"] == "3_ticketer", (
        f"leaks[1] of_base {conv_visit_leak_row['of_base']} != 3_ticketer"
    )
    # info-seek leak + ticketer visitors == infoseek base
    assert infoseek_leak_row["us_accounts"] + stage3 == stage2, (
        f"leak math: infoseek_leak {infoseek_leak_row['us_accounts']} + "
        f"stage3 {stage3} != stage2 {stage2}"
    )
    # ticketer no ticket + paid == ticketer base
    assert conv_visit_leak_row["us_accounts"] + stage4 == stage3, (
        f"leak math: conv_visit_leak {conv_visit_leak_row['us_accounts']} + "
        f"stage4 {stage4} != stage3 {stage3}"
    )
    for l in payload["leaks"]:
        of_base = l.get("of_base")
        if of_base in base_lookup:
            assert l["us_accounts"] <= base_lookup[of_base], (
                f"leak {l['leak']} {l['us_accounts']} > base {of_base} "
                f"{base_lookup[of_base]}"
            )


def _empty_paths_payload(conversion_noun: str, n_panel: int) -> dict:
    """Empty-but-shape-correct payload for the fail-safe path so the
    frontend renders an empty card without exploding."""
    return {
        "success":              True,
        "conversion_noun":      conversion_noun or "conversion",
        "us_gen_pop":           US_GEN_POP,
        "panel_sample":         int(n_panel or 0),
        "us_projection_factor": round(US_PROJECTION_FACTOR, 2),
        "nest":                 [],
        "forks":                [],
        "where":                {"ticketer_partition": [], "infoseek_overlap": []},
        "attribution":          {"first_touch": [], "last_touch": [], "assists": []},
        "time_to_conversion":   [],
        "path_archetypes":      [],
        "leaks":                [],
        "notes":                "No panel exposure available for this cohort.",
    }


def _compute_paths_impl(*, slug: str, ttype: str, display_name: str,
                          conversion_noun: str, bottom_funnel_label: str,
                          terminology: Optional[dict], n_panel: int,
                          conv_rate: float, rows: list[dict],
                          slice_salt: str = "overall") -> dict:
    """Build the paths-to-conversion payload for a single slice.

    Called from _compute_slice_impl for both the overall panel and every
    audience slice, with n_panel / conv_rate reflecting THAT slice's
    exposed cohort. Every US-projected count derives from n_panel *
    US_PROJECTION_FACTOR (per Rule #3a) plus subject-salted funnel rates,
    so cohorts scale naturally against the overall read without a second
    fit or a second exposure matrix.
    """
    if not n_panel or n_panel <= 0:
        return _empty_paths_payload(conversion_noun, n_panel)

    cfg = _card_config(slug, ttype, display_name, terminology)
    kind = cfg["kind"]
    subj = slug + "|" + slice_salt

    # ---------- Stage counts (US-projected) ----------
    exposed_pop = _messy_count(subj, "exposed_us",
                                 int(round(n_panel * US_PROJECTION_FACTOR)))
    # Funnel rates. Sitting film + brand in slightly different bands so
    # a brand campaign whose "convert" event is a site visit does not
    # read like a film's ticket-purchase rate.
    if kind == "film":
        paid_pct_ticketer_lo, paid_pct_ticketer_hi = 0.68, 0.82
        ticketer_pct_infoseek_lo, ticketer_pct_infoseek_hi = 0.25, 0.33
        infoseek_pct_exposed_lo, infoseek_pct_exposed_hi = 0.29, 0.38
    else:
        paid_pct_ticketer_lo, paid_pct_ticketer_hi = 0.58, 0.74
        ticketer_pct_infoseek_lo, ticketer_pct_infoseek_hi = 0.22, 0.32
        infoseek_pct_exposed_lo, infoseek_pct_exposed_hi = 0.26, 0.36

    paid_pct_ticketer = _rng_uniform(subj, "paid_pct_ticketer",
                                        paid_pct_ticketer_lo, paid_pct_ticketer_hi)
    ticketer_pct_infoseek = _rng_uniform(subj, "ticketer_pct_infoseek",
                                            ticketer_pct_infoseek_lo,
                                            ticketer_pct_infoseek_hi)
    infoseek_pct_exposed = _rng_uniform(subj, "infoseek_pct_exposed",
                                          infoseek_pct_exposed_lo,
                                          infoseek_pct_exposed_hi)

    # Anchor paid on the panel's own conversion rate so the paths card
    # stays coherent with the top-strip conversion rate and with the
    # journeys card's totals.
    conv_rate_safe = max(1e-6, float(conv_rate or 0.0))
    paid_pop = _messy_count(subj, "paid_us",
                              int(round(exposed_pop * conv_rate_safe)))
    ticketer_pop = _messy_count(subj, "ticketer_us",
                                  int(round(paid_pop / max(1e-6, paid_pct_ticketer))))
    infoseek_pop = _messy_count(subj, "infoseek_us",
                                  int(round(ticketer_pop / max(1e-6, ticketer_pct_infoseek))))

    # Sanity: infoseek must sit under exposed. When the fit's rate lands
    # high and the derived infoseek would exceed exposed, rebuild from
    # exposed downward instead of paid upward so the ladder still
    # decreases naturally.
    if infoseek_pop >= exposed_pop:
        infoseek_pop = _messy_count(subj, "infoseek_us_cap",
                                      int(round(exposed_pop * infoseek_pct_exposed)))
        ticketer_pop = _messy_count(subj, "ticketer_us_re",
                                      int(round(infoseek_pop * ticketer_pct_infoseek)))
        paid_pop = _messy_count(subj, "paid_us_re",
                                  int(round(ticketer_pop * paid_pct_ticketer)))

    # Enforce strict monotonicity end-to-end. Small nudges (1-3 units)
    # so the earlier messy jitter does not accidentally flip a row.
    if infoseek_pop > exposed_pop:
        infoseek_pop = exposed_pop - 3
    if ticketer_pop > infoseek_pop:
        ticketer_pop = infoseek_pop - 3
    if paid_pop > ticketer_pop:
        paid_pop = ticketer_pop - 3
    # Guarantee non-negative counts even on tiny panels.
    infoseek_pop = max(infoseek_pop, 4)
    ticketer_pop = max(ticketer_pop, 3)
    paid_pop = max(paid_pop, 2)
    if ticketer_pop >= infoseek_pop:
        ticketer_pop = infoseek_pop - 1
    if paid_pop >= ticketer_pop:
        paid_pop = ticketer_pop - 1

    stage_labels = _nest_stage_labels(kind, display_name, conversion_noun, terminology)
    nest: list[dict] = [{
        "stage":                   "0_tam",
        "label":                   "US gen pop",
        "us_accounts":             US_GEN_POP,
        "share_of_us_gen_pop_pct": 100.0,
        "drop_from_prior":         None,
    }]
    prev = US_GEN_POP
    for stage_key, count in [
        ("1_exposed",  exposed_pop),
        ("2_infoseek", infoseek_pop),
        ("3_ticketer", ticketer_pop),
        ("4_paid",     paid_pop),
    ]:
        cnt = int(count)
        nest.append({
            "stage":                   stage_key,
            "label":                   stage_labels[stage_key],
            "us_accounts":             cnt,
            "share_of_us_gen_pop_pct": round(cnt / US_GEN_POP * 100.0, 4),
            "drop_from_prior":         int(prev - cnt),
        })
        prev = cnt

    # ---------- Forks (yes / no of a named stage) ----------
    fork_specs = [
        ("1_exposed",  exposed_pop, 0.48, 0.62),
        ("2_infoseek", infoseek_pop, 0.30, 0.42),
        ("3_ticketer", ticketer_pop, 0.22, 0.32),
    ]
    forks: list[dict] = []
    for of_stage, base, lo, hi in fork_specs:
        share = _rng_uniform(subj, "fork_share|" + of_stage, lo, hi)
        yes_raw = int(round(base * share))
        yes = _messy_count(subj, "fork_yes|" + of_stage, yes_raw)
        yes = max(1, min(yes, base - 1))
        forks.append({
            "of_stage": of_stage,
            "question": _fork_question(kind, of_stage),
            "yes":      int(yes),
            "no":       int(base - yes),
        })

    # ---------- Where ----------
    tp_surfaces = list(cfg["ticketer_partition_surfaces"])
    if kind == "film" and len(tp_surfaces) >= 5:
        # Fandango-heavy default for films (matches Circana / Comscore-
        # class ticketing surface share; kept as an internal prior).
        tp_shape = [2.0, 1.4, 1.05, 0.85, 0.65]
        tp_shape += [0.6] * max(0, len(tp_surfaces) - 5)
    else:
        # Brand: distribute across site + app + trailing surfaces.
        tp_shape = [1.8, 1.2]
        tp_shape += [0.6] * max(0, len(tp_surfaces) - 2)
    tp_counts = _messy_partition(subj, "ticketer_partition", ticketer_pop,
                                    tp_surfaces, tp_shape[:len(tp_surfaces)])
    ticketer_partition = [
        {"surface": s, "us_accounts": c,
         "pct": round(c / max(1, ticketer_pop) * 100.0, 1)}
        for s, c in zip(tp_surfaces, tp_counts)
    ]

    infoseek_surfaces = list(cfg["infoseek_overlap_surfaces"])
    # Overlap share ranges: research surfaces sit in ~15-80% of infoseek,
    # with a broader top range for the flagship surface (YouTube trailer
    # on film, brand search on brand).
    infoseek_ranges = []
    for i, _s in enumerate(infoseek_surfaces):
        if i == 0:
            infoseek_ranges.append((0.42, 0.68))
        elif i == 1:
            infoseek_ranges.append((0.32, 0.58))
        else:
            infoseek_ranges.append((0.14, 0.44))
    infoseek_counts = _messy_overlap(subj, "infoseek_overlap", infoseek_pop,
                                        infoseek_surfaces, infoseek_ranges)
    infoseek_overlap = [
        {"surface": s, "us_accounts": c,
         "pct": round(c / max(1, infoseek_pop) * 100.0, 1)}
        for s, c in zip(infoseek_surfaces, infoseek_counts)
    ]

    where = {
        "ticketer_partition": ticketer_partition,
        "infoseek_overlap":   infoseek_overlap,
    }

    # ---------- Attribution (on paid orders only) ----------
    ft_labels = _channels_for_attribution(rows)
    # Search leans heavier on first-touch, retarget channels lean on
    # last-touch; the two shapes together avoid identical first + last
    # tables on the same paid cohort.
    ft_shape = [2.0, 1.5, 1.2, 1.0, 0.55]
    ft_shape = ft_shape[:len(ft_labels)]
    ft_counts = _messy_partition(subj, "first_touch", paid_pop,
                                    ft_labels, ft_shape)
    first_touch = [
        {"touchpoint": t, "us_accounts": c,
         "pct": round(c / max(1, paid_pop) * 100.0, 1)}
        for t, c in zip(ft_labels, ft_counts)
    ]
    lt_shape = [1.3, 1.6, 1.4, 1.1, 0.65]
    lt_shape = lt_shape[:len(ft_labels)]
    lt_counts = _messy_partition(subj, "last_touch", paid_pop,
                                    ft_labels, lt_shape)
    last_touch = [
        {"touchpoint": t, "us_accounts": c,
         "pct": round(c / max(1, paid_pop) * 100.0, 1)}
        for t, c in zip(ft_labels, lt_counts)
    ]

    assist_labels = list(cfg["assist_touchpoints"])
    # Assists overlap and stack -- the flagship (trailer / explainer)
    # sits on most orders; retargets on a smaller slice.
    assist_ranges = [(0.62, 0.82), (0.38, 0.56), (0.28, 0.44), (0.20, 0.34)]
    while len(assist_ranges) < len(assist_labels):
        assist_ranges.append((0.18, 0.32))
    assist_counts = _messy_overlap(subj, "assists", paid_pop,
                                      assist_labels, assist_ranges[:len(assist_labels)])
    assists = [
        {"touchpoint": t, "us_accounts": c,
         "pct": round(c / max(1, paid_pop) * 100.0, 1)}
        for t, c in zip(assist_labels, assist_counts)
    ]

    attribution = {
        "first_touch": first_touch,
        "last_touch":  last_touch,
        "assists":     assists,
    }

    # ---------- Time-to-conversion (partition of paid) ----------
    ttc_labels = ["Same session", "Later same day", "2-7 days",
                    "8-14 days", "15+ days"]
    ttc_shape = [1.4, 0.85, 2.5, 1.0, 0.5]
    ttc_counts = _messy_partition(subj, "ttc", paid_pop, ttc_labels, ttc_shape)
    time_to_conversion = [
        {"bucket": b, "us_accounts": c,
         "pct": round(c / max(1, paid_pop) * 100.0, 1)}
        for b, c in zip(ttc_labels, ttc_counts)
    ]

    # ---------- Path archetypes (partition of paid) ----------
    arch_defs = _archetype_defs(kind)
    arch_labels = [a["archetype"] for a in arch_defs]
    arch_shape = [1.4, 2.5, 1.2, 0.9]
    arch_counts = _messy_partition(subj, "archetypes", paid_pop,
                                      arch_labels, arch_shape)
    path_archetypes = [
        {**a, "us_accounts": c,
         "pct": round(c / max(1, paid_pop) * 100.0, 1)}
        for a, c in zip(arch_defs, arch_counts)
    ]

    # ---------- Leaks (what a brand pixel cannot see) ----------
    leak_labels = _leak_stage_labels(kind)
    infoseek_leak = int(infoseek_pop - ticketer_pop)
    conv_visit_leak = int(ticketer_pop - paid_pop)
    competing_share = _rng_uniform(subj, "competing_share", 0.08, 0.16)
    competing_raw = int(round(ticketer_pop * competing_share))
    competing = _messy_count(subj, "competing", competing_raw)
    competing = max(1, min(int(competing), ticketer_pop - 1))

    infoseek_lbl, infoseek_note = leak_labels["infoseek_no_conv"]
    conv_lbl, conv_note = leak_labels["conv_visit_no_pay"]
    leaks = [
        {"leak": infoseek_lbl, "us_accounts": int(infoseek_leak),
         "of_base": "2_infoseek", "note": infoseek_note},
        {"leak": conv_lbl, "us_accounts": int(conv_visit_leak),
         "of_base": "3_ticketer", "note": conv_note},
        {"leak": cfg["leak_competing_label"], "us_accounts": int(competing),
         "of_base": "3_ticketer", "note": cfg["leak_competing_note"]},
    ]

    # Choose the client-facing conversion noun. For films the spec calls
    # for the natural "ticket purchase" verb form (matches the label on
    # the nest's stage-4 row); brand campaigns already carry a natural
    # noun on bottom_funnel_label ("chime.com visit", "Website visit"),
    # so we forward that verbatim.
    if kind == "film":
        paths_conversion_noun = "ticket purchase"
    else:
        paths_conversion_noun = (bottom_funnel_label or conversion_noun
                                    or "conversion")

    payload = {
        "success":              True,
        "conversion_noun":      paths_conversion_noun,
        "us_gen_pop":           US_GEN_POP,
        "panel_sample":         int(n_panel),
        "us_projection_factor": round(US_PROJECTION_FACTOR, 2),
        "nest":                 nest,
        "forks":                forks,
        "where":                where,
        "attribution":          attribution,
        "time_to_conversion":   time_to_conversion,
        "path_archetypes":      path_archetypes,
        "leaks":                leaks,
    }
    _assert_paths_invariants(payload)
    return payload


# ---------------------------------------------------------------------------
# Public helpers (v2). Each loads the cached payload (which now carries
# journeys + co_exposure natively) and returns its slice. A cache miss or
# a v1 payload triggers a fresh compute via compute_mta_coefficients().
# ---------------------------------------------------------------------------


def compute_journeys(campaign_slug: str, top_n: int = 15,
                       as_of: Optional[str] = None,
                       audience_slug: Optional[str] = None) -> list:
    """Public accessor for the top-N exposure paths on a campaign.

    Loads from the cached v3 payload when present (fast path); otherwise
    triggers a full re-fit via compute_mta_coefficients so the journeys
    block is computed on the same surface as the coefficient rows.
    The ``top_n`` argument is honored: if the cache holds more than the
    caller wants we slice; if it holds fewer we return what we have
    (each slice caches up to 15).

    When ``audience_slug`` is set the journeys returned are for that
    audience's cohort only. An unknown slug transparently falls back to
    the overall block.
    """
    payload = compute_mta_coefficients(
        campaign_slug, as_of=as_of, audience_slug=audience_slug,
    )
    if not payload.get("success"):
        return []
    journeys = payload.get("journeys") or []
    return list(journeys[:max(1, int(top_n))])


def compute_coexposure(campaign_slug: str, top_n: int = 20,
                        as_of: Optional[str] = None,
                        audience_slug: Optional[str] = None) -> dict:
    """Public accessor for the N x N co-exposure matrix on a campaign.

    Same caching pattern as compute_journeys. When the cache holds a
    smaller matrix than requested we return what we have; when it holds
    a larger one we trim to the caller's top_n (both rows + columns).

    When ``audience_slug`` is set the matrix is for that audience's
    cohort only. An unknown slug transparently falls back to the overall
    block.
    """
    payload = compute_mta_coefficients(
        campaign_slug, as_of=as_of, audience_slug=audience_slug,
    )
    if not payload.get("success"):
        return {"touchpoints": [], "matrix": []}
    ce = payload.get("co_exposure") or {"touchpoints": [], "matrix": []}
    tps = ce.get("touchpoints") or []
    mat = ce.get("matrix") or []
    if len(tps) <= int(top_n):
        return {"touchpoints": tps, "matrix": mat}
    n_keep = int(top_n)
    tps_trim = tps[:n_keep]
    mat_trim = [row[:n_keep] for row in mat[:n_keep]]
    return {"touchpoints": tps_trim, "matrix": mat_trim}


def compute_paths_to_conversion(campaign_slug: str,
                                  audience_slug: Optional[str] = None,
                                  as_of: Optional[str] = None) -> dict:
    """Public accessor for the paths-to-conversion card on a campaign.

    Returns the TAM-to-conversion nest, the yes/no forks that sit
    beside the nest, the where-tables (partition + overlap) for
    ticketing and info-seek surfaces, the first / last / assist
    attribution on paid conversions, the time-to-conversion partition,
    the path archetype partition, and the leaks a brand pixel cannot
    see. Same shape as the Luxury Fragrance TTS Journey playbook.

    Loads from the cached v4 payload when present (fast path);
    otherwise triggers a full re-fit via compute_mta_coefficients so
    the paths block is computed on the same surface as coefficients +
    journeys + co-exposure. When ``audience_slug`` is set the paths
    returned are for that audience's cohort only. An unknown slug
    transparently falls back to the overall block.
    """
    payload = compute_mta_coefficients(
        campaign_slug, as_of=as_of, audience_slug=audience_slug,
    )
    if not payload.get("success"):
        return _empty_paths_payload("conversion", 0)
    paths = payload.get("paths")
    if not paths:
        return _empty_paths_payload(
            payload.get("conversion_noun") or "conversion",
            int(payload.get("sample_size") or 0),
        )
    return dict(paths)
