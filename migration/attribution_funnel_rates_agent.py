"""Per-asset info-seek + website-visit rate reasoning for Attribution IQ.

Problem this solves
-------------------
The legacy `iiqAssetFunnelProjection()` model in index.html applies
film-industry base rates (info-seek 4.5-14%, ticket 2-9.5%) to every
asset, then modulates with paid/organic + channel + phase multipliers.
For a MOVIE campaign that's roughly right (a trailer viewer really
does search IMDB 10-15% of the time). For a BRAND campaign that's ~10x
too high vs. real digital-marketing benchmarks:

  * Chime IG reel with 24k real views under the old model:
      info-seek 12% -> ~2,900 branded searches
      website   3%  -> ~740 chime.com visits
  * Reality (Meta/Google brand-lift + CTR benchmarks, digital-banking):
      info-seek  0.5-1.5% (organic) / 1-3% (paid)
      website    0.1-0.4% (organic) / 0.5-1.5% (paid)

This module produces per-asset `ext_info_seek_pct` and
`ext_website_visit_pct` values grounded in real benchmarks, honoring
these hard rules:

  1. NEVER inflate above what the platform + industry data supports.
     Values are capped by paid/organic + channel + brand-category
     ceilings (see BENCHMARKS below).
  2. Talent presence, explicit CTA, and phase intent are the ONLY
     legitimate levers that lift a rate above the channel baseline.
  3. Every rate is jittered per-asset (subject-salted) so cards don't
     collide at identical values.
  4. Every value carries provenance in `ext_funnel_rates_source` so
     the frontend can render it and ops can retry a stale run.

Public API
----------
  build_campaign_funnel_rates(snapshot, *, claude_fn=None,
                                progress=None, batch_size=8) -> dict
  save_to_snapshot(snapshot, result) -> None
"""

# =====================================================================
# CANONICAL LOCATIONS (both copies MUST be byte-identical):
#   1. /root/finished_codes/migration/attribution_funnel_rates_agent.py
#      (used by scripts/build_intent_*.py + Hetzner runs)
#   2. /root/finished_codes/bg-webapp/migration/attribution_funnel_rates_agent.py
#      (used by the Render Flask worker for dashboard-triggered
#       Attribution IQ ingests, so the reasoning + numbers match
#       whether Jenna hand-runs it or a user submits the Analysis
#       IQ form.)
# Edit BOTH copies + run scripts/verify_attribution_agents_parity.py
# =====================================================================
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Industry benchmarks (digital-banking / lifestyle brand social)
# ---------------------------------------------------------------------------
#
# Sources (each rate below is anchored to at least two of these; the
# per-brand-category tilts capture material deviations):
#   * Meta Business `Brand Lift` benchmarks 2024-2025 (branded search
#     lift after paid-video exposure by vertical): finance 0.8-1.8%
#     incremental branded search lift for a 15-30s video ad.
#   * Google Search-Lift Studies (YouTube TrueView -> branded search
#     within 7 days): 1.2-2.4% for finance verticals.
#   * TikTok for Business Ad Performance Benchmarks 2024 (in-feed
#     video CTR to link, finance): 0.9-1.6% paid, 0.10-0.25% organic.
#   * Meta Feed Video CTR to link (finance vertical): 0.6-1.0% paid,
#     0.05-0.15% organic (Instagram feed post).
#   * YouTube description-link CTR (organic long-form): 0.3-1.5%.
#   * Nielsen Digital Ad Ratings (cross-platform site-visit
#     attribution vs impression): 20-35% of clickers land on the site.
#
# These are conservative point estimates that produce realistic
# ABSOLUTE numbers when multiplied by real (scraped) view counts. If
# you feel a specific vertical warrants different anchors, add a
# BRAND_CATEGORY_TILT entry rather than raising these globally.

_CH_BASELINES = {
    #    (info_pct_paid, info_pct_org, web_pct_paid, web_pct_org)
    "youtube":    (1.9,  0.6,   0.9,  0.5),
    "tiktok":     (1.6,  0.4,   0.8,  0.15),
    "instagram":  (1.4,  0.35,  0.7,  0.10),
    "facebook":   (1.1,  0.30,  0.7,  0.15),
    "twitter":    (0.9,  0.30,  0.5,  0.20),
    "x":          (0.9,  0.30,  0.5,  0.20),
    "reddit":     (0.7,  0.25,  0.3,  0.15),
    "snapchat":   (1.0,  0.30,  0.5,  0.10),
    "linkedin":   (0.6,  0.25,  0.4,  0.15),
    # Unknown channel -> use IG shape (most common brand-social channel).
    "unknown":    (1.2,  0.35,  0.6,  0.15),
}

# Per brand-category tilt applied AFTER channel baseline.
# 1.0 = no change; keys are matched substring-insensitive.
_BRAND_CATEGORY_TILT = {
    # Digital banking / fintech: high-intent audience actively evaluating
    # apps; branded search and site-visit lift are ABOVE lifestyle norms.
    "digital_banking":       {"info": 1.15, "web": 1.35},
    "banking":               {"info": 1.10, "web": 1.30},
    "fintech":               {"info": 1.15, "web": 1.35},
    # Insurance: similar shopping behavior.
    "insurance":             {"info": 1.05, "web": 1.25},
    # DTC / e-commerce: high website-visit intent, moderate info-seek.
    "dtc":                   {"info": 1.00, "web": 1.60},
    "ecommerce":             {"info": 1.00, "web": 1.50},
    # QSR: high info-seek (menu lookups) but low website-visit
    # (people just go to the location).
    "qsr":                   {"info": 1.30, "web": 0.60},
    # Streaming / entertainment: high info-seek (title/talent search),
    # low website (they open the app, not the marketing site).
    "streaming":             {"info": 1.35, "web": 0.55},
    "media":                 {"info": 1.25, "web": 0.65},
    # Beauty / apparel: high site-visit, moderate info-seek.
    "beauty":                {"info": 0.95, "web": 1.55},
    "apparel":               {"info": 0.90, "web": 1.50},
    # Auto: high info-seek, moderate site-visit (dealership funnel).
    "automobile":            {"info": 1.20, "web": 0.95},
    # Default (unknown category) is no tilt.
}

# Multipliers applied on top of channel + brand tilts.
_TALENT_LIFT = {"info": 1.85, "web": 1.35}       # celebrity in asset
_ACTIVATION_LIFT = {"info": 0.95, "web": 1.50}   # sign-up / offer / promo phase
_AWARENESS_TILT = {"info": 1.05, "web": 0.75}    # early-funnel/awareness phase

# Hard ceiling per channel — even a viral celeb-driven CTA post cannot
# realistically drive branded search / site-visit above these caps.
# Ceilings are 3x the paid-channel baseline (roughly the 95th
# percentile of real cross-vertical brand-lift studies).
_CEILING = {
    "youtube":    {"info":  6.0, "web": 3.0},
    "tiktok":     {"info":  5.0, "web": 2.5},
    "instagram":  {"info":  4.5, "web": 2.2},
    "facebook":   {"info":  3.5, "web": 2.2},
    "twitter":    {"info":  3.0, "web": 1.8},
    "x":          {"info":  3.0, "web": 1.8},
    "reddit":     {"info":  2.5, "web": 1.2},
    "snapchat":   {"info":  3.5, "web": 1.8},
    "linkedin":   {"info":  2.0, "web": 1.4},
    "unknown":    {"info":  4.0, "web": 2.0},
}

# Hard floor so no asset renders as literally 0%.
_FLOOR_PCT = 0.02


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_channel(ch: str) -> str:
    ch = (ch or "").lower()
    for k in _CH_BASELINES:
        if k != "unknown" and k in ch:
            return k
    return "unknown"


def _match_brand_category(bc: str) -> str:
    if not bc:
        return ""
    bcl = bc.lower().replace(" ", "_").replace("/", "_")
    for k in _BRAND_CATEGORY_TILT:
        if k in bcl:
            return k
    return ""


def _phase_signal(phase_name: str) -> Optional[str]:
    p = (phase_name or "").lower()
    if re.search(r"activation|conversion|promo|offer|sign.?up|install|drive|retarget", p):
        return "activation"
    if re.search(r"awareness|announce|teaser|reveal|kickoff", p):
        return "awareness"
    return None


def _seeded_jitter(seed: str, salt: str, half_width: float) -> float:
    h = hashlib.sha1(f"{seed}|{salt}".encode("utf-8")).hexdigest()
    v = int(h[:8], 16) / 0xFFFFFFFF
    return (v * 2.0 - 1.0) * half_width


def _detect_talent(asset: dict) -> bool:
    tags = asset.get("talent_tags") or []
    if tags:
        return True
    label = (asset.get("action_label") or "").lower()
    # Simple heuristic: if the label mentions a specific person by
    # name (contains "with X" / "feat. X"), assume talent-led.
    return bool(re.search(r"\b(feat\.?|with|starring|ft\.?)\s+\w+", label))


def _has_explicit_cta(asset: dict) -> bool:
    text = ((asset.get("action_label") or "") + " " +
             (asset.get("asset_type") or "")).lower()
    return bool(re.search(
        r"sign.?up|download|get\s+chime|link\s+in\s+bio|open.?account|"
        r"click|swipe.?up|apply\s+now|shop|buy|order",
        text))


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------

def _fallback_asset_rates(asset: dict, brand_category: str,
                            subject_salt: str) -> Dict[str, float]:
    ch = _match_channel(asset.get("channel"))
    baseline = _CH_BASELINES[ch]
    po = (asset.get("paid_or_organic") or "").lower()
    if po == "paid":
        info_base, web_base = baseline[0], baseline[2]
    else:   # organic / natural / unknown all use the organic pole
        info_base, web_base = baseline[1], baseline[3]

    # Brand-category tilt
    bck = _match_brand_category(brand_category)
    if bck:
        tilt = _BRAND_CATEGORY_TILT[bck]
        info_base *= tilt["info"]
        web_base  *= tilt["web"]

    # Talent lift
    if _detect_talent(asset):
        info_base *= _TALENT_LIFT["info"]
        web_base  *= _TALENT_LIFT["web"]

    # Explicit CTA (a "sign up" / "link in bio" line lifts web CTR).
    if _has_explicit_cta(asset):
        web_base *= 1.35

    # Phase intent
    ps = _phase_signal(asset.get("phase_name") or "")
    if ps == "activation":
        info_base *= _ACTIVATION_LIFT["info"]
        web_base  *= _ACTIVATION_LIFT["web"]
    elif ps == "awareness":
        info_base *= _AWARENESS_TILT["info"]
        web_base  *= _AWARENESS_TILT["web"]

    # Subject-salted jitter (+/- 20%) so cards spread.
    key = subject_salt + "|" + (asset.get("asset_id") or asset.get("url") or "")
    info_val = max(_FLOOR_PCT, info_base * (1.0 + _seeded_jitter(key, "info", 0.20)))
    web_val  = max(_FLOOR_PCT, web_base  * (1.0 + _seeded_jitter(key, "web",  0.20)))

    # Clamp to per-channel ceiling — no asset may exceed real-world caps.
    cap = _CEILING[ch]
    info_val = min(info_val, cap["info"])
    web_val  = min(web_val,  cap["web"])

    # Web rate cannot exceed info rate (someone can't visit the site
    # without first learning about the brand).
    web_val = min(web_val, info_val * 0.85)

    return {"info_seek_pct":    round(info_val, 3),
            "website_visit_pct": round(web_val, 3),
            "source":            "fallback_deterministic_2026-08"}


# ---------------------------------------------------------------------------
# Claude reasoning
# ---------------------------------------------------------------------------

_CLAUDE_SYSTEM = (
    "You are a digital-marketing measurement analyst estimating funnel "
    "conversion rates for individual social-media marketing assets. "
    "Your outputs go straight to a client-facing dashboard so they must "
    "reflect REAL industry benchmarks, not hopeful estimates. Ground "
    "every reply in the benchmark table provided; only lift above the "
    "channel baseline when a specific asset attribute (talent, CTA, "
    "phase intent) justifies it. Return ONLY the requested JSON array."
)


def _build_claude_prompt(assets_batch: List[dict], brand: str, campaign: str,
                          brand_category: str, notes: str) -> str:
    asset_lines = []
    for i, a in enumerate(assets_batch):
        views = int(a.get("ext_view_count") or 0)
        eng   = int(a.get("ext_engagement_count") or 0)
        er    = f"{100 * eng / views:.2f}%" if views > 0 else "n/a"
        asset_lines.append(
            f'ASSET_{i}: '
            f'channel={(a.get("channel") or "").lower()!r}  '
            f'asset_type={(a.get("asset_type") or "")!r}  '
            f'paid_or_organic={(a.get("paid_or_organic") or "unknown")!r}  '
            f'phase={(a.get("phase_name") or "")!r}  '
            f'talent={a.get("talent_tags") or []!r}  '
            f'title={(a.get("action_label") or "")[:70]!r}  '
            f'real_views={views}  eng_rate={er}'
        )

    return f"""Estimate two per-asset funnel-conversion rates for a {brand_category}
brand campaign. Every rate must be within realistic digital-marketing
benchmarks (below); the numbers you return will be multiplied by the
REAL view count for that asset to produce the absolute conversion
numbers shown on the client dashboard.

Brand:            {brand}
Campaign:         {campaign}
Brand category:   {brand_category}
Brand notes:      {notes[:600] if notes else '(none)'}

Rate 1: info_seek_pct  = % of viewers who performed a branded search
   (Google/social search for the brand name) within 7 days of viewing.

Rate 2: website_visit_pct = % of viewers who visited the brand's
   website / product page within 7 days of viewing. Must be <= info_seek_pct
   (you have to learn about the brand before you can visit its site).

INDUSTRY BENCHMARK TABLE (digital-banking / lifestyle brand social)
Sources: Meta Brand-Lift 2024-25, Google Search-Lift Studies, TikTok
for Business 2024, Nielsen Digital Ad Ratings.

 Channel   | PAID info% | PAID web% | ORGANIC info% | ORGANIC web%
 ----------|-----------|-----------|---------------|--------------
 YouTube   |   1.9     |    0.9    |     0.60      |     0.50
 TikTok    |   1.6     |    0.8    |     0.40      |     0.15
 Instagram |   1.4     |    0.7    |     0.35      |     0.10
 Facebook  |   1.1     |    0.7    |     0.30      |     0.15
 X/Twitter |   0.9     |    0.5    |     0.30      |     0.20

MODIFIERS you may apply:
 * Talent-led (celebrity in asset)  -> info x1.85, web x1.35
 * Explicit CTA ("sign up", "link in bio", "download") -> web x1.35
 * Activation/conversion phase  -> web x1.50
 * Awareness/announcement phase  -> web x0.75
 * Very high engagement rate (>8%) -> info x1.2, web x1.15
 * Long-tail asset (very low views <500) -> no modifier

HARD CEILINGS (no asset may exceed these no matter what):
 YT: info<=6.0 web<=3.0 | TT: info<=5.0 web<=2.5 | IG: info<=4.5 web<=2.2
 FB: info<=3.5 web<=2.2 | X/TW: info<=3.0 web<=1.8

Assets in this batch ({len(assets_batch)}):
{chr(10).join(asset_lines)}

For each asset, THINK about:
  1. Baseline channel + paid/organic rate.
  2. Does the brand category tilt it? ({brand_category} is a
     high-intent decision category - be careful not to bake in movie /
     entertainment rates.)
  3. Is talent present? Explicit CTA? Activation phase?
  4. Would the resulting ABSOLUTE numbers (rate * real_views) make
     sense to a marketer looking at this asset?

Return JSON array only, one entry per asset:
[
  {{"asset_id": "ASSET_0", "info_seek_pct": 0.55, "website_visit_pct": 0.18, "reasoning": "IG organic, talent-led -> baseline 0.35 * 1.85 = 0.65 info; web 0.10 * 1.35 = 0.14"}},
  ...
]
"""


_JSON_ARRAY_RX = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def _parse_claude_response(text: str, batch_size: int) -> List[Optional[Dict[str, float]]]:
    if not text:
        return [None] * batch_size
    m = _JSON_ARRAY_RX.search(text)
    if not m:
        return [None] * batch_size
    try:
        arr = json.loads(m.group(0))
    except (ValueError, TypeError):
        return [None] * batch_size
    if not isinstance(arr, list):
        return [None] * batch_size

    out: List[Optional[Dict[str, float]]] = []
    for i in range(batch_size):
        target = f"ASSET_{i}"
        entry = next((e for e in arr if isinstance(e, dict)
                       and e.get("asset_id") == target), None)
        if entry is None and i < len(arr) and isinstance(arr[i], dict):
            entry = arr[i]
        if entry is None:
            out.append(None)
            continue
        try:
            info = float(entry.get("info_seek_pct"))
            web  = float(entry.get("website_visit_pct"))
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append({"info_seek_pct":    info,
                     "website_visit_pct": web,
                     "reasoning":        str(entry.get("reasoning") or "")[:400]})
    return out


def _asset_rates_via_claude(assets_batch: List[dict], brand: str,
                             campaign: str, brand_category: str,
                             notes: str, claude_fn: Callable
                             ) -> List[Optional[Dict[str, float]]]:
    prompt = _build_claude_prompt(assets_batch, brand, campaign,
                                    brand_category, notes)
    try:
        text = claude_fn(system=_CLAUDE_SYSTEM, user=prompt,
                          max_tokens=2048, temperature=0.25)
    except TypeError:
        try:
            text = claude_fn(prompt)
        except Exception as e:
            logger.warning("attribution_funnel_rates: claude failed (positional): %s", e)
            return [None] * len(assets_batch)
    except Exception as e:
        logger.warning("attribution_funnel_rates: claude failed: %s", e)
        return [None] * len(assets_batch)
    return _parse_claude_response(text, len(assets_batch))


# ---------------------------------------------------------------------------
# Per-asset variance (fights the "Claude batches -> identical rates" pattern)
# ---------------------------------------------------------------------------
#
# When Claude reasons over a batch of similar-looking assets (e.g. 8 IG
# posts starring the same talent in the same phase) it tends to return
# tidy rounded numbers that repeat across the batch -- 50+ assets all
# read 0.65% info / 0.14% web because from Claude's POV they're "the
# same asset shape".
#
# In reality each asset has a unique engagement rate + reach profile
# that predicts a distinct downstream funnel. A 3.5%-eng-rate 129k-view
# reel converts differently from a 0.6%-eng-rate 2.4M-view reel even
# though both are "IG organic Lindsay Lohan" posts.
#
# This helper takes Claude's base rate and perturbs it deterministically
# using three per-asset signals:
#   1. Engagement-rate z-score vs the campaign mean  -> higher-eng
#      assets get proportionally higher info-seek + web rates. Bounded
#      +/-25%.
#   2. View-volume tier (log-scaled) -> smaller viral pieces get a small
#      lift (+3%), mass reach pieces get a small drag (-3%). Reflects
#      the reality that mass audiences dilute the "high-intent viewer"
#      share.
#   3. Asset-id-salted jitter (+/-6%) -> guarantees no two assets share
#      an identical rate even when signals 1 and 2 net to zero.
#
# All three combine multiplicatively, so the aggregate campaign rate
# stays roughly on Claude's baseline (perturbations mean-reverting)
# while individual assets each get a genuinely-unique rate that reflects
# THEIR data, not a template.

def _campaign_engagement_stats(assets: List[dict]) -> Dict[str, float]:
    """Compute per-channel engagement-rate means so the variance step
    can z-score each asset against its own channel peers."""
    from collections import defaultdict
    by_ch: Dict[str, List[float]] = defaultdict(list)
    for a in assets:
        v = int(a.get("ext_view_count") or 0)
        e = int(a.get("ext_engagement_count") or 0)
        if v <= 0:
            continue
        ch = _match_channel(a.get("channel"))
        by_ch[ch].append(e / v)
    stats: Dict[str, float] = {}
    for ch, rates in by_ch.items():
        if len(rates) >= 3:
            m = sum(rates) / len(rates)
            var = sum((r - m) ** 2 for r in rates) / len(rates)
            stats[ch + ":mean"] = m
            stats[ch + ":stdev"] = max(var ** 0.5, 1e-6)
        elif rates:
            stats[ch + ":mean"] = rates[0]
            stats[ch + ":stdev"] = max(rates[0] * 0.5, 1e-6)
    return stats


def _apply_per_asset_variance(base_info: float, base_web: float,
                                asset: dict, subject_salt: str,
                                ch_stats: Dict[str, float]) -> tuple:
    """Perturb (base_info, base_web) using real per-asset signals so
    each asset gets a unique rate rather than a Claude "template" value.
    Returns (info, web) both perturbed. All bounds are relative to the
    input so channel ceilings still hold in _clamp_rate."""
    v = int(asset.get("ext_view_count") or 0)
    e = int(asset.get("ext_engagement_count") or 0)
    ch = _match_channel(asset.get("channel"))

    # ---- (1) engagement-rate z-score against channel peers ----
    eng_mult_info = 1.0
    eng_mult_web  = 1.0
    m = ch_stats.get(ch + ":mean")
    s = ch_stats.get(ch + ":stdev")
    if v > 0 and m is not None and s is not None:
        er = e / v
        z = (er - m) / s
        z = max(-2.5, min(2.5, z))
        # Info-seek is more sensitive to engagement than website-visit
        # (people who ENGAGE with a piece are much more likely to
        # search the brand, only somewhat more likely to actually
        # click through to the site).
        eng_mult_info = 1.0 + 0.10 * z    # +/-25% at z=+/-2.5
        eng_mult_web  = 1.0 + 0.07 * z    # +/-17.5% at z=+/-2.5

    # ---- (2) view-volume tier tilt ----
    #   <1k views:       +8% (long-tail, more engaged viewers)
    #   1k-50k:          +3%
    #   50k-500k:         0%
    #   500k-2M:         -2%
    #   >2M (viral):     -4% (mass audience dilutes intent share)
    if v < 1000:      vol_mult = 1.08
    elif v < 50_000:  vol_mult = 1.03
    elif v < 500_000: vol_mult = 1.00
    elif v < 2_000_000: vol_mult = 0.98
    else:             vol_mult = 0.96

    # ---- (3) asset-salted jitter guarantees uniqueness ----
    key = subject_salt + "|" + str(asset.get("asset_id") or asset.get("url") or "")
    jit_info = 1.0 + _seeded_jitter(key, "info_var", 0.06)   # +/-6%
    jit_web  = 1.0 + _seeded_jitter(key, "web_var",  0.06)

    info = base_info * eng_mult_info * vol_mult * jit_info
    web  = base_web  * eng_mult_web  * vol_mult * jit_web
    return info, web


# ---------------------------------------------------------------------------
# Sanity clamp (applied to every rate, Claude or fallback)
# ---------------------------------------------------------------------------

def _clamp_rate(rates: Dict[str, float], asset: dict) -> Dict[str, float]:
    """Enforce channel-ceiling and info>=web invariants on any rate
    (Claude or fallback). This is the last line of defense against
    inflated numbers reaching the dashboard. Returns 4-decimal
    precision so identical Claude "template" rates get de-duplicated
    by the variance step upstream."""
    ch = _match_channel(asset.get("channel"))
    cap = _CEILING[ch]
    info = max(_FLOOR_PCT, min(float(rates.get("info_seek_pct", 0) or 0),    cap["info"]))
    web  = max(_FLOOR_PCT, min(float(rates.get("website_visit_pct", 0) or 0), cap["web"]))
    web = min(web, info * 0.85)   # web cannot exceed 85% of info
    out = dict(rates)
    out["info_seek_pct"]    = round(info, 4)
    out["website_visit_pct"] = round(web, 4)
    return out


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def build_campaign_funnel_rates(snapshot: dict, *,
                                  claude_fn: Optional[Callable] = None,
                                  progress: Optional[Callable] = None,
                                  batch_size: int = 8) -> dict:
    """Compute per-asset info-seek + website-visit rates for every
    asset in the snapshot. Mutates each asset in place to add:

      ext_info_seek_pct       - float 0-100
      ext_website_visit_pct   - float 0-100
      ext_funnel_rates_source - str provenance tag
      ext_funnel_rates_note   - str Claude one-liner reasoning (optional)

    Returns a summary dict with counts + roll-up totals for auditing.
    """
    title = snapshot.get("title") or {}
    brand = title.get("subject") or title.get("distributor") or title.get("brand") or ""
    campaign = title.get("display_name") or title.get("campaign") or title.get("title") or ""
    brand_category = (title.get("brand_category")
                       or (title.get("brand_config") or {}).get("brand_category")
                       or title.get("genre") or "")
    notes = title.get("notes") or ""

    assets = snapshot.get("assets") or []
    subject_salt = (title.get("title_slug") or brand or "chime")

    # Pre-compute per-channel engagement stats so the variance step
    # (applied per-asset below) has a stable baseline to z-score
    # against. Computed once here, not per batch, so all assets share
    # the same reference distribution.
    ch_stats = _campaign_engagement_stats(assets)

    n = len(assets)
    total_batches = math.ceil(n / batch_size) if n else 0
    claude_hits = 0
    fallback_hits = 0

    for batch_i, start in enumerate(range(0, n, batch_size), 1):
        chunk = assets[start:start + batch_size]
        claude_results: List[Optional[Dict[str, float]]] = ([None] * len(chunk))
        if claude_fn is not None:
            claude_results = _asset_rates_via_claude(
                chunk, brand, campaign, brand_category, notes, claude_fn)

        for i, a in enumerate(chunk):
            claude_r = claude_results[i]
            if claude_r is not None:
                base_info = float(claude_r.get("info_seek_pct") or 0)
                base_web  = float(claude_r.get("website_visit_pct") or 0)
                source_tag = "claude_reasoning_variance_2026-08"
                claude_hits += 1
            else:
                fb = _fallback_asset_rates(a, brand_category, subject_salt)
                base_info = float(fb.get("info_seek_pct") or 0)
                base_web  = float(fb.get("website_visit_pct") or 0)
                source_tag = fb["source"] + "_variance"
                fallback_hits += 1
                claude_r = None

            # Perturb per asset so no two share the same rate. The
            # perturbation uses REAL asset signals (engagement rate
            # vs channel mean, view-volume tier, asset-id salt) so
            # each rate reflects that asset's own data, not a Claude
            # "template" for its pattern.
            info_var, web_var = _apply_per_asset_variance(
                base_info, base_web, a, subject_salt, ch_stats)

            rates = _clamp_rate({"info_seek_pct":    info_var,
                                   "website_visit_pct": web_var}, a)
            rates["source"] = source_tag
            if claude_r is not None and claude_r.get("reasoning"):
                rates["reasoning"] = claude_r["reasoning"]

            a["ext_info_seek_pct"]        = rates["info_seek_pct"]
            a["ext_website_visit_pct"]    = rates["website_visit_pct"]
            a["ext_funnel_rates_source"]  = rates["source"]
            if rates.get("reasoning"):
                a["ext_funnel_rates_note"] = rates["reasoning"]

        if progress:
            progress(batch_i, total_batches, len(chunk))

    # Roll-up totals for auditability.
    tot_views = sum(int(a.get("ext_view_count") or 0) for a in assets)
    tot_info  = sum(int((a.get("ext_view_count") or 0)
                          * (a.get("ext_info_seek_pct") or 0) / 100) for a in assets)
    tot_web   = sum(int((a.get("ext_view_count") or 0)
                          * (a.get("ext_website_visit_pct") or 0) / 100) for a in assets)
    method = ("claude" if fallback_hits == 0
              else ("fallback_deterministic" if claude_hits == 0 else "mixed"))

    return {
        "generated_at_utc":  datetime.now(timezone.utc).isoformat(),
        "method":            method,
        "claude_hits":       claude_hits,
        "fallback_hits":     fallback_hits,
        "asset_count":       n,
        "total_views":       tot_views,
        "total_info_seek":   tot_info,
        "total_website_visit": tot_web,
        "aggregate_info_pct":    (100 * tot_info / tot_views) if tot_views else 0,
        "aggregate_website_pct": (100 * tot_web  / tot_views) if tot_views else 0,
    }


def save_to_snapshot(snapshot: dict, summary: dict) -> None:
    """Attach the funnel-rates summary to the snapshot for provenance.
    The per-asset numbers themselves already live on each asset (mutated
    in place by build_campaign_funnel_rates)."""
    snapshot["funnel_rates_summary"] = summary
