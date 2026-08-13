"""Per-asset -> per-campaign demographic reasoning for Attribution IQ.

Given a campaign snapshot (assets + phases + brand context), this
module returns a canonical-schema demographic distribution for every
phase in the campaign, plus a rolled-up "all campaigns" cut.

The reasoning is:
  1. For each asset, ask Claude to estimate the demographic mix of
     the audience that would ACTUALLY have watched / engaged with
     that specific post. Inputs given to the LLM:
       - asset URL + channel + asset_type
       - action_label (the OG-scraped title of the post)
       - phase_name (which campaign it belongs to)
       - talent_tags (Lindsay Lohan, etc.)
       - paid_or_organic
       - brand + brand_category + brand notes
       - real ext_view_count + ext_engagement_count (calibrated)
     Output is a JSON block covering every canonical category from
     `attribution_demographics_schema.DEMO_SCHEMA`.
  2. Assets are batched by phase, chunked at 6-per-request, and cached
     by (subject + campaign + asset_id) so we don't re-hit Claude for
     unchanged assets on subsequent runs.
  3. Per-asset distributions are aggregated to per-phase by
     view-weighted mean (asset i's contribution = ext_view_count_i /
     phase_total_views).
  4. Every phase output is snapped back to the canonical schema via
     `normalize_distribution`, guaranteeing sum-100 and no zero buckets.

If Claude is unavailable (no API key / offline), we fall back to a
deterministic heuristic that reasons about the same signals (channel,
talent, phase name, paid_vs_organic, brand category) via a rules
table + subject-salted jitter. The fallback produces MEANINGFULLY
different distributions per campaign, per channel — not the US
baseline for every phase — so the demographic tab still tells a story
even without an API key.

Public API:
    build_campaign_demographics(snapshot, *, claude_fn=None,
                                 progress=None) -> dict
    save_to_snapshot(snapshot, demographics) -> None
"""

# =====================================================================
# CANONICAL LOCATIONS (both copies MUST be byte-identical):
#   1. /root/finished_codes/migration/attribution_demographics_agent.py
#      (used by scripts/build_intent_*.py + Hetzner runs)
#   2. /root/finished_codes/bg-webapp/migration/attribution_demographics_agent.py
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
from collections import defaultdict
from typing import Callable, Dict, List, Optional

from .attribution_demographics_schema import (
    DEMO_SCHEMA, DEMO_US_BASELINE, blank_distribution,
    normalize_distribution,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic per-asset reasoning fallback (used when Claude is unavailable)
# ---------------------------------------------------------------------------
#
# Every rule here is a signal-based tilt applied to the US baseline.
# The tilts are chosen to be plausibly directional (TikTok skews
# younger + female; YouTube long-form skews slightly older + more
# male; talent tags like "Lindsay Lohan" shift female + Gen X). The
# magnitudes are conservative — no rule multiplies by more than ~2.0
# on any bucket — so the aggregate stays realistic even when several
# tilts stack.


def _clone_baseline() -> Dict[str, Dict[str, float]]:
    """Fresh copy of the US baseline (float) we can mutate safely."""
    return {cat: {b: float(v) for b, v in DEMO_US_BASELINE.get(cat, {}).items()}
            for cat in DEMO_SCHEMA}


def _apply_tilt(dist: Dict[str, Dict[str, float]],
                cat: str, bucket: str, mult: float) -> None:
    """Multiply one bucket in `dist[cat]` by `mult` (bounded [0.1, 4.0])."""
    if cat not in dist or bucket not in dist[cat]:
        return
    mult = max(0.1, min(4.0, mult))
    dist[cat][bucket] = dist[cat][bucket] * mult


def _seeded_jitter(seed: str, salt: str, half_width: float = 0.15) -> float:
    h = hashlib.sha1(f"{seed}|{salt}".encode("utf-8")).hexdigest()
    v = int(h[:8], 16) / 0xFFFFFFFF
    return (v * 2.0 - 1.0) * half_width


# ---------------------------------------------------------------------------
# AGE -> ETHNICITY coherence (per Jenna 2026-08-13: "make sure the assets
# that are targeted at genz and millenials are over indexing with black
# and hispanic")
#
# US Census reality: audiences skewing 18-34 skew MORE multicultural than
# the general population.
#   Total US adults:  ~60% White, ~19% Hispanic, ~13% Black, ~6% Asian
#   US 25-34:         ~55% White, ~22% Hispanic, ~14% Black, ~7% Asian
#   US 18-24 (Gen Z): ~52% White, ~26% Hispanic, ~14% Black, ~6% Asian
# On IG / TikTok this tilt is even stronger (both platforms over-index
# on Black + Hispanic users vs same-age general pop).
#
# The BG panel baseline (`DEMO_US_BASELINE`) is more White-skewed than
# true US Census because the panel itself over-represents White adults
# (~64% panel vs ~60% Census). That's fine as the OG baseline, but it
# means Gen Z / Millennial-targeted content needs an EXPLICIT lift on
# top of the panel baseline to reach the demographically-realistic
# audience shape.
#
# This helper enforces the shift AFTER Claude reasoning per asset, so
# the per-phase and campaign rollups (view-weighted averages downstream)
# inherit the corrected shape automatically without breaking any
# sum-to-100 invariant (normalize_distribution handles final rounding).
# ---------------------------------------------------------------------------

# Baseline share of US audience in 18-24 + 25-34 (per DEMO_US_BASELINE
# AGE section). Any asset with young_share above this gets a
# proportional multicultural lift.
_ETH_BASELINE_18_34_SHARE = 17.09 + 28.44   # = 45.53

# Per-pp shift when young_share exceeds baseline. Sums to 0 so the
# category still totals 100 before renormalization. Ratios are grounded
# in the Census 18-34 vs total US ethnicity delta (i.e. Hispanic sees
# the biggest lift because it's the biggest multicultural bloc in Gen
# Z; Asian gets a smaller lift because Asian share is roughly flat
# across age).
_YOUNG_ETH_SHIFT_PER_PP = {
    "White":                     -0.50,
    "Hispanic or Latino":         0.30,
    "Black or African American":  0.15,
    "Asian":                      0.03,
    "Another Race/Ethnicity":     0.02,
}

# Hard caps so no single lift can push a bucket out of realistic
# bounds even for extremely young-skewed batches.
_ETH_CAPS = {
    "White":                     (35.0, 88.0),
    "Hispanic or Latino":         (2.0, 38.0),
    "Black or African American":  (2.0, 30.0),
    "Asian":                      (1.0, 16.0),
    "Another Race/Ethnicity":     (1.0, 25.0),
}

# HARD FLOORS activated when the asset skews majority-young (>=50% of
# audience in 18-34). Guarantees over-index vs the panel baseline for
# Black + Hispanic per Jenna's directive.
_ETH_YOUNG_OVERINDEX_MULT = 1.15   # 15% over baseline minimum


def apply_age_ethnicity_coherence(dist: Dict[str, Dict[str, float]]) -> None:
    """Adjust ETHNICITY in place so it reflects the AGE skew of the same
    asset. Called AFTER Claude reasoning per asset, BEFORE view-weighted
    aggregation into phase and campaign rollups.

    - No change when the asset is age-baseline or older-skewed
    - Progressive Census-grounded lift on Hispanic + Black (drop White)
      when young_share > baseline
    - Hard-enforced Black + Hispanic over-index whenever young_share
      >= 50% of the audience -- guarantees Jenna's rule that Gen Z /
      Millennial-targeted assets always over-index on multicultural
    - Renormalizes ETHNICITY to 100 so downstream aggregation math
      remains a straight view-weighted average
    """
    age = dist.get("AGE") or {}
    eth = dist.get("ETHNICITY") or {}
    if not age or not eth:
        return

    young = float(age.get("18-24", 0) or 0) + float(age.get("25-34", 0) or 0)
    excess = young - _ETH_BASELINE_18_34_SHARE

    # Step 1: proportional Census-grounded shift.
    if excess > 0:
        for bucket, per_pp in _YOUNG_ETH_SHIFT_PER_PP.items():
            if bucket in eth:
                eth[bucket] = float(eth[bucket]) + per_pp * excess

    # Step 2: apply hard caps.
    for bucket, (lo, hi) in _ETH_CAPS.items():
        if bucket in eth:
            eth[bucket] = max(lo, min(hi, float(eth[bucket])))

    # Step 3: hard over-index floor for majority-young audiences.
    if young >= 50.0:
        base_h = DEMO_US_BASELINE["ETHNICITY"].get("Hispanic or Latino", 10.61)
        base_b = DEMO_US_BASELINE["ETHNICITY"].get("Black or African American", 7.76)
        min_h = base_h * _ETH_YOUNG_OVERINDEX_MULT
        min_b = base_b * _ETH_YOUNG_OVERINDEX_MULT
        if eth.get("Hispanic or Latino", 0) < min_h:
            eth["Hispanic or Latino"] = min_h
        if eth.get("Black or African American", 0) < min_b:
            eth["Black or African American"] = min_b

    # Step 4: renormalize ETHNICITY to 100 (steps 1-3 don't
    # preserve total). Downstream `normalize_distribution` will
    # apply floors + final 4dp rounding; we just need the shape.
    total = sum(float(v) for v in eth.values())
    if total > 0:
        for k in list(eth.keys()):
            eth[k] = float(eth[k]) * 100.0 / total
    dist["ETHNICITY"] = eth


_CHANNEL_TILTS = {
    "youtube": [
        ("AGE",    "25-34",  1.15),
        ("AGE",    "35-44",  1.20),
        ("AGE",    "45-54",  1.15),
        ("AGE",    "17 and Under", 0.75),
        ("GENDER", "Male",   1.12),
        ("EDUCATION", "Bachelors Degree", 1.05),
    ],
    "tiktok": [
        ("AGE",    "17 and Under", 1.60),
        ("AGE",    "18-24",  1.55),
        ("AGE",    "25-34",  1.10),
        ("AGE",    "45-54",  0.60),
        ("AGE",    "55-64",  0.35),
        ("AGE",    "65 or Older", 0.20),
        ("GENDER", "Female", 1.25),
        ("GENDER", "Non-Binary", 1.40),
    ],
    "instagram": [
        ("AGE",    "18-24",  1.30),
        ("AGE",    "25-34",  1.30),
        ("AGE",    "45-54",  0.80),
        ("AGE",    "65 or Older", 0.55),
        ("GENDER", "Female", 1.30),
        ("INCOME", "$100,000 - $149,999", 1.10),
        ("INCOME", "$150,000 - $249,999", 1.15),
    ],
    "facebook": [
        ("AGE",    "45-54",  1.35),
        ("AGE",    "55-64",  1.55),
        ("AGE",    "65 or Older", 1.70),
        ("AGE",    "18-24",  0.55),
        ("PARENTAL_STATUS", "Has Children", 1.20),
    ],
    "twitter": [
        ("AGE",    "25-34",  1.20),
        ("AGE",    "35-44",  1.15),
        ("GENDER", "Male",   1.25),
        ("EDUCATION", "Bachelors Degree", 1.15),
        ("EDUCATION", "Graduate or Professional Degree", 1.30),
    ],
}


# Named-talent tilts. Order matters: earlier tilts apply first, later
# ones stack. Regex-matched on the talent tag (case-insensitive).
_TALENT_TILTS = [
    (r"lindsay\s*lohan", [
        ("AGE",       "25-34", 1.35),
        ("AGE",       "35-44", 1.40),
        ("AGE",       "17 and Under", 0.65),
        ("GENDER",    "Female", 1.35),
        ("ETHNICITY", "White", 1.10),
        ("SEXUAL_ORIENTATION", "Gay or Lesbian", 1.35),
        ("SEXUAL_ORIENTATION", "LGBTQ+", 1.40),
    ]),
    (r"jelly\s*roll", [
        ("AGE",       "35-44", 1.30),
        ("AGE",       "45-54", 1.25),
        ("ETHNICITY", "White", 1.15),
        ("PARENTAL_STATUS", "Has Children", 1.20),
        ("OCCUPATION", "Skilled Trades/Construction or Maintenance", 1.35),
        ("OCCUPATION", "Transportation & Logistics", 1.25),
    ]),
    (r"steph\s*curry", [
        ("AGE",       "18-24", 1.25),
        ("AGE",       "25-34", 1.30),
        ("GENDER",    "Male",  1.20),
        ("ETHNICITY", "Black or African American", 1.30),
    ]),
]


# Brand-category tilts. Chime is `digital_banking` → skew younger,
# lower-income (relative to the panel's over-indexed $50K+), and more
# diverse than a legacy-bank user.
_BRAND_CAT_TILTS = {
    "digital_banking": [
        ("AGE",       "18-24", 1.30),
        ("AGE",       "25-34", 1.35),
        ("AGE",       "35-44", 1.15),
        ("AGE",       "55-64", 0.65),
        ("AGE",       "65 or Older", 0.50),
        ("INCOME",    "$25,000 - $49,999", 1.60),
        ("INCOME",    "$50,000 - $74,999", 1.15),
        ("INCOME",    "$150,000 - $249,999", 0.75),
        ("INCOME",    "$250,000 or More", 0.55),
        ("ETHNICITY", "Hispanic or Latino", 1.30),
        ("ETHNICITY", "Black or African American", 1.35),
    ],
    "streaming":  [
        ("AGE", "18-24", 1.20), ("AGE", "25-34", 1.15),
    ],
    # fill in more brand categories as we ingest them
}


# Paid ads on any channel tend to be more skewed to the buyer persona
# than organic (which is the follower base). For Chime, paid = more
# converters -> older + higher-income relative to the organic post.
_PAID_TILTS = [
    ("AGE",    "25-34", 1.15),
    ("AGE",    "35-44", 1.15),
    ("INCOME", "$50,000 - $74,999", 1.10),
    ("INCOME", "$75,000 - $99,999", 1.10),
]

_ORGANIC_TILTS = [
    ("AGE",    "18-24", 1.10),
    ("AGE",    "25-34", 1.05),
]


def _fallback_asset_distribution(asset: dict, brand: str, brand_category: str) -> Dict[str, Dict[str, float]]:
    """Deterministic per-asset demographic distribution when Claude is
    unavailable. Returns raw (unnormalized) numbers; caller normalizes."""
    dist = _clone_baseline()

    channel = (asset.get("channel") or "").lower()
    for ch_key, tilts in _CHANNEL_TILTS.items():
        if ch_key in channel:
            for cat, bucket, mult in tilts:
                _apply_tilt(dist, cat, bucket, mult)
            break

    for tag in (asset.get("talent_tags") or []):
        for rx, tilts in _TALENT_TILTS:
            if re.search(rx, str(tag), re.IGNORECASE):
                for cat, bucket, mult in tilts:
                    _apply_tilt(dist, cat, bucket, mult)

    bcat_key = (brand_category or "").lower().replace(" ", "_").replace("/", "_")
    for k, tilts in _BRAND_CAT_TILTS.items():
        if k in bcat_key:
            for cat, bucket, mult in tilts:
                _apply_tilt(dist, cat, bucket, mult)
            break

    po = (asset.get("paid_or_organic") or "").lower()
    tilts = _PAID_TILTS if po == "paid" else _ORGANIC_TILTS
    for cat, bucket, mult in tilts:
        _apply_tilt(dist, cat, bucket, mult)

    # Small salt-based jitter per asset so cards don't collide at
    # identical values inside the same phase.
    salt = f"{brand}|{asset.get('asset_id') or asset.get('url') or ''}"
    for cat in dist:
        for b in list(dist[cat].keys()):
            j = _seeded_jitter(salt, f"{cat}|{b}", half_width=0.08)
            dist[cat][b] *= (1.0 + j)

    return dist


# ---------------------------------------------------------------------------
# Claude reasoning
# ---------------------------------------------------------------------------

def _build_claude_prompt(assets_batch: List[dict], brand: str, campaign: str,
                          brand_category: str, notes: str,
                          phase_name: str) -> str:
    """One prompt covers up to N assets from a single phase."""
    schema_lines = []
    for cat, buckets in DEMO_SCHEMA.items():
        schema_lines.append(f'  "{cat}": {json.dumps(buckets)}')
    schema_block = "{\n" + ",\n".join(schema_lines) + "\n}"

    asset_lines = []
    for i, a in enumerate(assets_batch):
        ttl = a.get("action_label") or a.get("asset_type") or "(untitled)"
        chan = a.get("channel") or ""
        typ = a.get("asset_type") or ""
        po = a.get("paid_or_organic") or "unknown"
        tal = a.get("talent_tags") or []
        views = a.get("ext_view_count") or 0
        eng = a.get("ext_engagement_count") or 0
        asset_lines.append(
            f'ASSET_{i}: title={ttl!r}  channel={chan!r}  type={typ!r}  '
            f'paid_or_organic={po!r}  talent={tal!r}  '
            f'real_views={views}  real_engagement={eng}'
        )

    return f"""You are an audience analyst modeling the ACTUAL viewers of specific
marketing assets. For every asset below, estimate the demographic
composition of the people who saw + engaged with THAT specific post
(not the brand's total audience, not the US population).

Brand:            {brand}
Campaign:         {campaign}
Brand category:   {brand_category}
Phase:            {phase_name}
Brand notes:      {notes[:600] if notes else '(none)'}

Assets in this batch ({len(assets_batch)}):
{chr(10).join(asset_lines)}

Reason carefully about each asset:
  * The CHANNEL constrains the age/gender skew heavily (TikTok users
    are 55%+ under 25; YouTube long-form skews 25-44; Facebook 45+).
  * PAID assets reach the brand's buyer persona; ORGANIC posts reach
    the existing follower base. These differ.
  * TALENT names shift the audience toward that talent's fan base
    (Lindsay Lohan -> female-skewed 30-45; Jelly Roll -> country/
    Southern rural leaning).
  * BRAND CATEGORY matters. {brand_category} campaigns skew a specific
    way (e.g. digital banking = 18-44, lower/mid income, more
    diverse than legacy banks).
  * The REAL_VIEWS number tells you how big the audience is; use
    that when weighing edge-cases (viral posts reach broader
    audiences than their follower base).

Return one JSON object per asset, following this exact category +
bucket schema (extra keys are ignored):
{schema_block}

Every category value is a JSON object mapping bucket -> percent (0-100
float). Each category MUST sum to ~100 (within 1 point). If you're
unsure about a niche cut (e.g. SEXUAL_ORIENTATION for a bank ad), lean
close to the US baseline. Only return the JSON array — no prose.

Output format:
[
  {{"asset_id": "ASSET_0", "AGE": {{...}}, "GENDER": {{...}}, ...}},
  {{"asset_id": "ASSET_1", ...}},
  ...
]
"""


_JSON_ARRAY_RX = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def _parse_claude_response(text: str, batch_size: int) -> List[Dict[str, Dict[str, float]]]:
    """Extract a list of per-asset distributions from Claude's response.
    Returns [] on parse failure so caller can fall back."""
    if not text:
        return []
    m = _JSON_ARRAY_RX.search(text)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
        return []

    out: List[Dict[str, Dict[str, float]]] = []
    for i in range(batch_size):
        # Find the entry for ASSET_i (by asset_id) or fall back to the
        # i-th entry if id-matching fails.
        target_id = f"ASSET_{i}"
        entry = next((e for e in arr if isinstance(e, dict)
                       and e.get("asset_id") == target_id), None)
        if entry is None and i < len(arr):
            entry = arr[i] if isinstance(arr[i], dict) else None
        if entry is None:
            out.append({})
            continue
        raw = {cat: (entry.get(cat) or {}) for cat in DEMO_SCHEMA}
        out.append(raw)
    return out


_CLAUDE_SYSTEM = (
    "You are an audience analyst modeling ACTUAL viewers of specific "
    "marketing assets. Your job is to reason from real signals "
    "(channel, talent, paid_vs_organic, brand category, real view "
    "count) to a demographic distribution that reflects who actually "
    "watched each post — not the US population, not the brand's total "
    "reach. Return ONLY the requested JSON array, no prose."
)


def _asset_distribution_via_claude(assets_batch: List[dict], brand: str,
                                    campaign: str, brand_category: str,
                                    notes: str, phase_name: str,
                                    claude_fn: Callable) -> List[Dict[str, Dict[str, float]]]:
    prompt = _build_claude_prompt(assets_batch, brand, campaign,
                                    brand_category, notes, phase_name)
    text = ""
    # The canonical signature is `claude_messages(*, system=..., user=..., ...)`.
    try:
        text = claude_fn(system=_CLAUDE_SYSTEM, user=prompt,
                          max_tokens=4096, temperature=0.35)
    except TypeError:
        # Older / positional-arg signature fallback.
        try:
            text = claude_fn(prompt)
        except Exception as e:
            logger.warning("attribution_demographics_agent: claude call failed (positional): %s", e)
            return []
    except Exception as e:
        logger.warning("attribution_demographics_agent: claude call failed: %s", e)
        return []
    return _parse_claude_response(text, len(assets_batch))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _view_weighted_merge(dists: List[Dict[str, Dict[str, float]]],
                          weights: List[float]) -> Dict[str, Dict[str, float]]:
    """Compute per-category, per-bucket weighted mean across a batch of
    per-asset distributions. Both lists must be the same length."""
    out = blank_distribution()
    if not dists:
        return out
    total_w = sum(w for w in weights if w > 0) or 1.0
    for d, w in zip(dists, weights):
        if not d or w <= 0:
            continue
        # Each asset's category may not sum to 100 yet (Claude might
        # be off by a couple points); normalize per-asset before
        # weighting so a wonky Claude reply doesn't dominate.
        w_share = w / total_w
        for cat, buckets in DEMO_SCHEMA.items():
            per_cat = d.get(cat) or {}
            cat_sum = sum(float(per_cat.get(b, 0.0) or 0.0) for b in buckets) or 1.0
            for b in buckets:
                out[cat][b] += (float(per_cat.get(b, 0.0) or 0.0) / cat_sum * 100.0) * w_share
    return out


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def build_campaign_demographics(snapshot: dict, *,
                                  claude_fn: Optional[Callable] = None,
                                  progress: Optional[Callable] = None,
                                  batch_size: int = 6) -> dict:
    """Return a demographics dict of the form:
      {
        "generated_at_utc":  "...",
        "method":            "claude" | "fallback_deterministic" | "mixed",
        "phases": [
          {
            "phase_name":   "Welcome to 30",
            "asset_count":  47,
            "view_count":   3812901,
            "demographics": {
              "AGE": {"17 and Under": 4.2, "18-24": 22.1, ...},
              "GENDER": {...},
              ...
            }
          },
          ...
        ],
        "all_campaigns": {           # view-weighted rollup across every phase
          "asset_count":  146,
          "view_count":   38784770,
          "demographics": {...}
        }
      }
    """
    title = snapshot.get("title") or {}
    brand = title.get("subject") or title.get("brand") or ""
    campaign = title.get("campaign") or title.get("title") or ""
    brand_category = title.get("brand_category") or ""
    notes = title.get("notes") or ""

    assets = snapshot.get("assets") or []
    assets_with_views = [a for a in assets if int(a.get("ext_view_count") or 0) > 0]
    if not assets_with_views:
        # Every asset has 0 real views (fresh unsculpted snapshot). Fall
        # back to using every asset with equal weight so the tab still
        # renders.
        assets_with_views = list(assets)
        for a in assets_with_views:
            a.setdefault("ext_view_count", 1)

    # Bucket by phase.
    by_phase: Dict[str, List[dict]] = defaultdict(list)
    for a in assets_with_views:
        by_phase[a.get("phase_name") or "(Uncategorized)"].append(a)

    methods_used = set()
    phase_records = []
    all_asset_dists: List[Dict[str, Dict[str, float]]] = []
    all_asset_weights: List[float] = []

    total_batches = sum(math.ceil(len(v) / batch_size) for v in by_phase.values())
    batch_i = 0

    for phase_name, ph_assets in by_phase.items():
        # Chunk assets in this phase into batches for Claude.
        phase_asset_dists: List[Dict[str, Dict[str, float]]] = []
        phase_asset_weights: List[float] = []

        for start in range(0, len(ph_assets), batch_size):
            chunk = ph_assets[start:start + batch_size]
            claude_dists: List[Dict[str, Dict[str, float]]] = []
            if claude_fn is not None:
                claude_dists = _asset_distribution_via_claude(
                    chunk, brand, campaign, brand_category, notes,
                    phase_name, claude_fn)

            for i, a in enumerate(chunk):
                w = float(a.get("ext_view_count") or 0)
                d = claude_dists[i] if i < len(claude_dists) else None
                if d and any(d.values()):
                    methods_used.add("claude")
                else:
                    d = _fallback_asset_distribution(a, brand, brand_category)
                    methods_used.add("fallback_deterministic")
                # Enforce age -> ethnicity coherence per Jenna 2026-08-13:
                # any asset whose audience skews Gen Z / Millennial MUST
                # over-index on Black + Hispanic (see helper for the
                # Census-grounded shift + hard over-index floor).
                apply_age_ethnicity_coherence(d)
                phase_asset_dists.append(d)
                phase_asset_weights.append(w)
                all_asset_dists.append(d)
                all_asset_weights.append(w)

            batch_i += 1
            if progress:
                progress(batch_i, total_batches, phase_name, len(chunk))

        raw_phase_dist = _view_weighted_merge(phase_asset_dists, phase_asset_weights)
        normalized = {
            cat: normalize_distribution(cat, raw_phase_dist.get(cat) or {})
            for cat in DEMO_SCHEMA
        }
        phase_records.append({
            "phase_name":   phase_name,
            "asset_count":  len(ph_assets),
            "view_count":   int(sum(int(a.get("ext_view_count") or 0) for a in ph_assets)),
            "demographics": normalized,
        })

    # All-campaigns rollup
    all_raw = _view_weighted_merge(all_asset_dists, all_asset_weights)
    all_normalized = {
        cat: normalize_distribution(cat, all_raw.get(cat) or {})
        for cat in DEMO_SCHEMA
    }
    all_record = {
        "asset_count":  len(assets_with_views),
        "view_count":   int(sum(int(a.get("ext_view_count") or 0) for a in assets_with_views)),
        "demographics": all_normalized,
    }

    method = "mixed" if len(methods_used) > 1 else (next(iter(methods_used)) if methods_used else "fallback_deterministic")

    from datetime import datetime, timezone
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method":           method,
        "phases":           phase_records,
        "all_campaigns":    all_record,
    }


def save_to_snapshot(snapshot: dict, demographics: dict) -> None:
    """Attach the demographics block to the snapshot in place. The
    frontend + backend read from `snapshot['demographics']`."""
    snapshot["demographics"] = demographics
