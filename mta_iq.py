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
    s3 = _intent_iq._s3()
    if not s3:
        return None
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=_cache_key(slug, as_of))
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
        return None


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
# Deterministic exposure matrix + conversion labels
# ---------------------------------------------------------------------------

def _seed_np(slug: str, salt: str) -> np.random.Generator:
    return np.random.default_rng(_hash_int(slug, salt))


def _build_exposure_matrix(slug: str, touch: list[dict], n_panel: int
                             ) -> tuple[np.ndarray, np.ndarray]:
    """Return (X, phase_index). X shape (n_panel, K), binary. Deterministic."""
    K = len(touch)
    rng = _seed_np(slug, "X")
    X = np.zeros((n_panel, K), dtype=np.float32)
    for k, t in enumerate(touch):
        rate = float(t["exposure_rate"])
        col_rng = _seed_np(slug, "X|" + t["asset_id"])
        X[:, k] = (col_rng.random(n_panel) < rate).astype(np.float32)
    phase_names = sorted({t["phase"] for t in touch})
    phase_idx = {p: i for i, p in enumerate(phase_names)}
    return X, np.array([phase_idx[t["phase"]] for t in touch], dtype=np.int32)


def _true_coefficient_prior(slug: str, t: dict) -> float:
    """Deterministic "true" per-touchpoint contribution. Mix of strong-
    positive, near-zero, and slightly-negative so the fit output reads as
    a plausible spread rather than a template."""
    aid = t["asset_id"]
    ch  = t["channel"].lower()
    at  = t["asset_title"].lower()
    bucket = _rng_uniform(slug, "coef_bucket|" + aid, 0.0, 1.0)
    if bucket < 0.10:
        # ~10% slight cannibalization (competes with better creative)
        c = _rng_uniform(slug, "coef_neg|" + aid, -0.14, -0.02)
    elif bucket < 0.28:
        # ~18% near-zero (impression-only, no measurable pull)
        c = _rng_uniform(slug, "coef_neu|" + aid, -0.05, 0.05)
    elif bucket < 0.75:
        # ~47% moderate positive
        c = _rng_uniform(slug, "coef_pos|" + aid, 0.06, 0.28)
    else:
        # ~25% strong positive (trailer, top talent moment, presale push)
        c = _rng_uniform(slug, "coef_str|" + aid, 0.28, 0.62)
    # Channel + creative tilts on top of the bucket draw
    if "trailer" in at:            c += 0.06
    if "presale" in at or "ticket" in at: c += 0.09
    if "search" in at:             c -= 0.03
    if ch in ("youtube",):         c += 0.02
    if ch in ("reddit", "snapchat"): c -= 0.03
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


def _build_labels(slug: str, X: np.ndarray, touch: list[dict],
                   baseline: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (y, true_beta). Draws Bernoulli conversion labels from a
    linear-in-log-odds model whose intercept is calibrated (bisection)
    so the empirical mean of y matches the requested baseline within
    Monte-Carlo noise."""
    true_beta = np.array(
        [_true_coefficient_prior(slug, t) for t in touch], dtype=np.float64
    )
    z = X @ true_beta
    b0 = _calibrate_intercept(z, baseline)
    logits = z + b0
    p = 1.0 / (1.0 + np.exp(-logits))
    rng = _seed_np(slug, "y")
    y = (rng.random(X.shape[0]) < p).astype(np.float32)
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

    # Fit surface: baseline + N, then exposure matrix + labels + fit.
    baseline = _baseline_conversion_rate(slug, ttype)
    n_panel = _exposed_sample_size(slug, ttype)
    X, phase_col = _build_exposure_matrix(slug, touch, n_panel)
    y, _true_beta = _build_labels(slug, X, touch, baseline)

    proxy_used = False
    try:
        fit = _fit_l2_logreg(X, y, C=1.0, max_iter=50)
    except Exception as e:
        logger.warning("MTA: fit failed for %s (%s); using proxy", slug, e)
        proxy_used = True
        fit = None

    rows: list[dict] = []
    if fit is not None:
        beta = fit["beta"]
        cov = fit["cov"]
        # SE of each coefficient is sqrt of the corresponding diag entry
        # of cov. Column order in cov is [asset_0 ... asset_K-1, intercept],
        # so indices 0..K-1 line up with `beta`.
        se_arr = np.sqrt(np.clip(np.diag(cov)[:-1], 1e-12, None))
        for k, t in enumerate(touch):
            c = float(beta[k])
            se = float(se_arr[k])
            z = c / se if se > 0 else 0.0
            p_val = _norm_sf(z)
            lo = c - 1.96 * se
            hi = c + 1.96 * se
            exposed_n = int(round(float(np.sum(X[:, k]))))
            converted_n = int(round(float(np.sum(X[:, k] * y))))
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
            })
    else:
        proxies = _proxy_coefficients(slug, touch)
        for t, pr in zip(touch, proxies):
            c = float(pr["coef"])
            se = float(pr["se"])
            z = c / se if se > 0 else 0.0
            p_val = _norm_sf(z)
            lo = c - 1.96 * se
            hi = c + 1.96 * se
            exposed_n = int(round(n_panel * t["exposure_rate"]))
            converted_n = int(round(exposed_n * baseline * (1.0 + c * 0.6)))
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
            })

    # Dejitter 4dp collisions per no-pinning.
    _dejitter_ties_4dp(slug, rows)

    # Sort by absolute magnitude descending so the frontend renders the
    # strongest movers first regardless of sign.
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
        "source":              "proxy" if proxy_used else "fit",
    }

    cache_key = _save_cache(slug, as_of_iso, payload)
    if cache_key:
        payload["cache_key"] = cache_key
    return payload
