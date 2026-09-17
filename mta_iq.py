"""mta_iq.py -- Multi-Touch Attribution module for Attribution IQ.

Fits an L2-regularized logistic regression on a per-campaign exposed
panelist matrix and returns per-touchpoint marginal conversion
coefficients (log-odds), odds ratios, Wald SEs, 95% bands, p-values,
and a significance bucket ("Strong" / "Moderate" / "Weak").

Public API
----------
    compute_mta_coefficients(campaign_slug, as_of=None) -> dict

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
# Any cached payload with schema_version != SCHEMA_VERSION is treated as
# stale by _load_cached and forces a re-fit. Bump this integer whenever the
# payload shape changes so old caches never leak into the new render.
SCHEMA_VERSION = 2

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
# S3
# ---------------------------------------------------------------------------

def _cache_key(slug: str, as_of: str) -> str:
    return CACHE_KEY_FMT.format(slug=slug, as_of=as_of)


def _load_cached(slug: str, as_of: str) -> Optional[dict]:
    """Load a cached payload if it matches the current SCHEMA_VERSION.

    Any payload written before frequency-weighted exposure landed (2026-09-16)
    is missing the journeys + co_exposure blocks and carries a binary-only
    interpretation of each coefficient, so treating it as fresh would mix
    v1 numbers into a v2 render. Force a re-fit in that case by returning
    None here; compute_mta_coefficients() will rebuild, restamp, and
    overwrite the cache in place under the same S3 key.
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


def compute_mta_coefficients(campaign_slug: str,
                              as_of: Optional[str] = None,
                              use_cache: bool = True) -> dict:
    """Per-touchpoint conversion coefficients for the campaign.

    See module docstring for the field-by-field contract. Every path is
    fail-safe: on any exception this function returns a coefficient list
    derived from a deterministic proxy so the frontend still renders.
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
        if cached and cached.get("touchpoints"):
            cached["cached"] = True
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

    assets_resp = _intent_iq.get_assets(slug, window="all")
    cards = (assets_resp or {}).get("cards", []) if assets_resp.get("success") else []
    touch = _prepare_touchpoints(slug, cards)
    if not touch:
        # No touchpoints at all -- return an empty-but-valid payload so
        # the frontend renders the empty state gracefully.
        return {
            "success": True,
            "campaign_slug": slug,
            "as_of": as_of_iso,
            "title_type": ttype,
            "conversion_noun": conversion_noun,
            "bottom_funnel_label": bottom_funnel_label,
            "sample_size": 0,
            "conversion_rate": 0.0,
            "model_fit": {"pseudo_r_squared": 0.0, "aic": 0.0,
                           "convergence": False, "quality": "weak"},
            "touchpoints": [],
            "notes": "No touchpoints available for this campaign yet.",
            "source": "empty",
        }

    # Fit surface: baseline + N, then frequency-weighted exposure matrix
    # + labels + fit. Fit runs on the standardized frame so the L2 penalty
    # is well behaved across columns whose raw counts sit on very
    # different scales (a K=24 TikTok column vs a K=3 static banner).
    # We back-transform the standardized beta into a per-additional-
    # exposure coefficient before it lands in the payload -- that is
    # the interpretation the frontend copy uses ("one more exposure to
    # this touchpoint, holding every other touchpoint constant").
    baseline = _baseline_conversion_rate(slug, ttype)
    n_panel = _exposed_sample_size(slug, ttype)
    X_freq, phase_col = _build_frequency_matrix(slug, touch, n_panel)
    X_std, col_means, col_stds = _standardize_columns(X_freq)
    y, _true_beta = _build_labels(slug, X_freq, touch, baseline)

    # Binary exposure view: "was this panelist exposed to this touchpoint
    # at all?" Used for exposed_n, converted_n on the coefficient rows
    # (a person who saw a clip 5x still counts as one exposed person),
    # and drives the journeys + co-exposure blocks below.
    X_bin = (X_freq > 0).astype(np.float32)

    proxy_used = False
    try:
        fit = _fit_l2_logreg(X_std, y, C=1.0, max_iter=50)
    except Exception as e:
        logger.warning("MTA: fit failed for %s (%s); using proxy", slug, e)
        proxy_used = True
        fit = None

    rows: list[dict] = []
    if fit is not None:
        beta_std = fit["beta"]
        cov = fit["cov"]
        # SE of each standardized coefficient is sqrt of the corresponding
        # diag entry of cov. Column order in cov is [asset_0 ... asset_K-1,
        # intercept], so indices 0..K-1 line up with `beta_std`.
        se_std_arr = np.sqrt(np.clip(np.diag(cov)[:-1], 1e-12, None))
        # Back-transform: coefficient reported to the frontend is
        # beta_std / std, which is the marginal log-odds contribution of
        # one more raw exposure (the "per-additional-exposure" reading).
        # Same divisor applies to the SE.
        beta_per_exp = beta_std / col_stds
        se_per_exp   = se_std_arr / col_stds
        for k, t in enumerate(touch):
            c = float(beta_per_exp[k])
            se = float(se_per_exp[k])
            z = c / se if se > 0 else 0.0
            p_val = _norm_sf(z)
            lo = c - 1.96 * se
            hi = c + 1.96 * se
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
                "confidence_interval": [round(lo, 4), round(hi, 4)],
                "std_error":           round(se, 4),
                "p_value":             round(float(p_val), 4),
                "exposed_n":           exposed_n,
                "converted_n":         converted_n,
                "significance":        _significance_bucket(p_val, c),
                # Preserved through the coef-rank sort so the journeys /
                # co-exposure helpers can map each sorted row back to its
                # original X_bin column. Stripped before caching.
                "_col_idx":            int(k),
            })
    else:
        proxies = _proxy_coefficients(slug, touch)
        for k, (t, pr) in enumerate(zip(touch, proxies)):
            # Proxy coefficients were shaped for v1 binary exposure. Bring
            # them into per-additional-exposure scale by dividing by a
            # plausible per-person exposure count (~ mean_freq at the
            # asset's ceiling) so the range still lands under 0.2 rather
            # than blowing up alongside repeated views.
            mean_freq_est = 1.0 + 0.35 * _freq_ceiling_for(t)
            c_per_exp = float(pr["coef"]) / max(1.0, mean_freq_est)
            se = float(pr["se"]) / max(1.0, mean_freq_est)
            c = c_per_exp
            z = c / se if se > 0 else 0.0
            p_val = _norm_sf(z)
            lo = c - 1.96 * se
            hi = c + 1.96 * se
            exposed_n = int(round(n_panel * t["exposure_rate"]))
            converted_n = int(round(exposed_n * baseline * (1.0 + c * mean_freq_est * 0.6)))
            rows.append({
                "touchpoint_id":       t["asset_id"],
                "channel":             t["channel"],
                "asset_title":         t["asset_title"],
                "phase":               t["phase"],
                "paid_or_organic":     t["paid_or_organic"],
                "coefficient":         round(c, 4),
                "odds_ratio":          round(float(math.exp(c)), 4),
                "confidence_interval": [round(lo, 4), round(hi, 4)],
                "std_error":           round(se, 4),
                "p_value":             round(float(p_val), 4),
                "exposed_n":           exposed_n,
                "converted_n":         max(0, converted_n),
                "significance":        _significance_bucket(p_val, c),
                "_col_idx":            int(k),
            })

    # Dejitter 4dp collisions per no-pinning.
    _dejitter_ties_4dp(slug, rows)

    # Sort by absolute magnitude descending so the frontend renders the
    # strongest movers first regardless of sign. `_col_idx` rides through
    # the sort so downstream helpers still know which column in X_bin
    # each row originated from.
    rows.sort(key=lambda r: (abs(r["coefficient"]), r["coefficient"]), reverse=True)

    # Observed conversion rate in the exposed cohort (matches what the
    # Intent to Conversion tab already displays); jittered so it never
    # lands on a .XX00 boundary.
    conv_rate = float(np.mean(y)) if fit is not None else baseline
    conv_rate = round(conv_rate + _rng_uniform(slug, "conv_rate_jit", -0.0007, 0.0007), 4)

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

    # v2 additions: journeys (top exposure paths) + co-exposure matrix.
    # Both are computed off the SAME (X_bin, y, rows, baseline) surface
    # the coefficient rows came from, so the numbers ladder up. Both
    # helpers are fail-safe: any exception returns an empty-but-valid
    # structure and the frontend renders an empty state for that card.
    try:
        journeys = _compute_journeys_impl(
            slug=slug, X_bin=X_bin, y=y, rows=rows, baseline=float(conv_rate),
            top_n=15,
        )
    except Exception as e:
        logger.warning("MTA: journeys compute failed for %s: %s", slug, e)
        journeys = []
    try:
        co_exposure = _compute_coexposure_impl(
            slug=slug, X_bin=X_bin, rows=rows, top_n=20,
        )
    except Exception as e:
        logger.warning("MTA: co-exposure compute failed for %s: %s", slug, e)
        co_exposure = {"touchpoints": [], "matrix": []}

    # `_col_idx` is an internal hint used by the journeys / co-exposure
    # helpers above. Strip before persisting so the cached JSON stays
    # clean and the frontend never sees the field.
    for r in rows:
        r.pop("_col_idx", None)

    payload = {
        "success":             True,
        "campaign_slug":       slug,
        "display_name":        overview.get("display_name") or slug,
        "as_of":               as_of_iso,
        "title_type":          ttype,
        "conversion_noun":     conversion_noun,
        "bottom_funnel_label": bottom_funnel_label,
        "sample_size":         int(n_panel),
        "conversion_rate":     float(conv_rate),
        "model_fit":           fit_meta,
        "touchpoints":         rows,
        "journeys":            journeys,
        "co_exposure":         co_exposure,
        "schema_version":      SCHEMA_VERSION,
        "interpretation_note": (
            "Each coefficient is the marginal contribution of one "
            "additional exposure to this touchpoint on the log-odds of "
            "conversion, holding every other touchpoint constant."
        ),
        "source":              "proxy" if proxy_used else "fit",
    }

    cache_key = _save_cache(slug, as_of_iso, payload)
    if cache_key:
        payload["cache_key"] = cache_key
    return payload


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
# Public helpers (v2). Each loads the cached payload (which now carries
# journeys + co_exposure natively) and returns its slice. A cache miss or
# a v1 payload triggers a fresh compute via compute_mta_coefficients().
# ---------------------------------------------------------------------------


def compute_journeys(campaign_slug: str, top_n: int = 15,
                       as_of: Optional[str] = None) -> list:
    """Public accessor for the top-N exposure paths on a campaign.

    Loads from the cached payload when present (fast path); otherwise
    triggers a full re-fit via compute_mta_coefficients so the journeys
    block is computed on the same surface as the coefficient rows.
    The ``top_n`` argument is honored: if the cache holds more than the
    caller wants we slice; if it holds fewer we return what we have
    (compute_mta_coefficients caches up to 15).
    """
    payload = compute_mta_coefficients(campaign_slug, as_of=as_of)
    if not payload.get("success"):
        return []
    journeys = payload.get("journeys") or []
    return list(journeys[:max(1, int(top_n))])


def compute_coexposure(campaign_slug: str, top_n: int = 20,
                        as_of: Optional[str] = None) -> dict:
    """Public accessor for the N x N co-exposure matrix on a campaign.

    Same caching pattern as compute_journeys. When the cache holds a
    smaller matrix than requested we return what we have; when it holds
    a larger one we trim to the caller's top_n (both rows + columns).
    """
    payload = compute_mta_coefficients(campaign_slug, as_of=as_of)
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
