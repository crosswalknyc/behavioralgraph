"""
journey_iq_synthesize.py — Claude-driven synthetic-data + box-office scaling
for Digital Journey IQ runs on movies.

Two responsibilities:

1. **Box-office scaling.** Given the panel cohort size (real converters
   observed in the clickstream) and the movie's US box-office gross, scale
   COUNT fields (users, reach, converters, ...) up to the implied total
   audience. Percentages are NEVER scaled — only counts. The scaling factor
   is `implied_audience / panel_converters`.

   implied_audience = (box_office_usd / avg_ticket_price) * audience_factor
   where audience_factor defaults to 0.775 — the midpoint of the 70-85%
   single-ticket-per-buyer band the user specified (matinee/family
   double-counts vs solo).

2. **LLM synthesis.** When the cohort is too small to draw conclusions,
   ask Claude to estimate a plausible path-to-purchase + touchpoint mix
   for a movie of this scale + talent profile. Returns the same JSON shape
   as `_aggregate_path_to_purchase` / `_aggregate_touchpoints` so the
   dashboard renders it without conditional logic.

Dashboard mode toggle (real / modeled / blended) lives in the frontend —
this module just produces the synthetic data and the scaling factors; the
adapter at the bottom builds a "blended" view by combining real and
modeled at field level (real where N >= 25, modeled where sparse).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

try:
    from migration.claude_client import claude_messages, is_hybrid_enabled, get_claude_client
except ImportError:
    try:
        from claude_client import claude_messages, is_hybrid_enabled, get_claude_client  # type: ignore
    except ImportError:
        claude_messages = None       # type: ignore
        is_hybrid_enabled = None     # type: ignore
        get_claude_client = None     # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_TICKET_PRICE      = 15.00
DEFAULT_AUDIENCE_FRACTION = 0.775   # midpoint of 70-85% single-ticket-per-buyer
SPARSE_COHORT_THRESHOLD   = 25      # < this many real converters → blend in modeled

# Canonical movie touchpoint mix used as fallback when Claude is offline.
# Numbers chosen to roughly match a tentpole release's path-to-purchase
# (Marvel-tier movies have ~85% trailer reach, ~70% search, etc.).
_FALLBACK_TOUCHPOINTS = [
    {'label': 'TRAILER',           'reach_pct': 78.0, 'avg_days_to_conversion': 21.0,  'avg_touches_per_user': 2.4, 'lift_pct': 65.0},
    {'label': 'ORGANIC_SEARCH',    'reach_pct': 72.0, 'avg_days_to_conversion': 7.0,   'avg_touches_per_user': 3.1, 'lift_pct': 120.0},
    {'label': 'SOCIAL_YOUTUBE',    'reach_pct': 58.0, 'avg_days_to_conversion': 18.0,  'avg_touches_per_user': 2.0, 'lift_pct': 45.0},
    {'label': 'SOCIAL_TIKTOK',     'reach_pct': 41.0, 'avg_days_to_conversion': 12.0,  'avg_touches_per_user': 3.8, 'lift_pct': 38.0},
    {'label': 'SOCIAL_INSTAGRAM',  'reach_pct': 37.0, 'avg_days_to_conversion': 14.0,  'avg_touches_per_user': 2.2, 'lift_pct': 32.0},
    {'label': 'PRESS',             'reach_pct': 28.0, 'avg_days_to_conversion': 22.0,  'avg_touches_per_user': 1.6, 'lift_pct': 18.0},
    {'label': 'REVIEW',            'reach_pct': 24.0, 'avg_days_to_conversion': 4.0,   'avg_touches_per_user': 2.1, 'lift_pct': 96.0},
    {'label': 'SHOWTIME_LOOKUP',   'reach_pct': 86.0, 'avg_days_to_conversion': 1.5,   'avg_touches_per_user': 1.8, 'lift_pct': 310.0},
    {'label': 'GOOGLE_REVIEW',     'reach_pct': 18.0, 'avg_days_to_conversion': 3.0,   'avg_touches_per_user': 1.4, 'lift_pct': 72.0},
    {'label': 'TICKETING',         'reach_pct': 92.0, 'avg_days_to_conversion': 0.5,   'avg_touches_per_user': 1.2, 'lift_pct': None},
    {'label': 'PAID_AD',           'reach_pct': 33.0, 'avg_days_to_conversion': 15.0,  'avg_touches_per_user': 4.6, 'lift_pct': 26.0},
    {'label': 'TALENT_MENTION',    'reach_pct': 22.0, 'avg_days_to_conversion': 19.0,  'avg_touches_per_user': 1.7, 'lift_pct': 41.0},
    {'label': 'CREATOR_INFLUENCER','reach_pct': 19.0, 'avg_days_to_conversion': 11.0,  'avg_touches_per_user': 1.9, 'lift_pct': 58.0},
    {'label': 'BRAND_PARTNERSHIP', 'reach_pct': 11.0, 'avg_days_to_conversion': 25.0,  'avg_touches_per_user': 1.4, 'lift_pct': 22.0},
    {'label': 'SOUNDTRACK',        'reach_pct':  9.0, 'avg_days_to_conversion': 17.0,  'avg_touches_per_user': 1.5, 'lift_pct': 14.0},
]

_FALLBACK_PATH_COLUMN_MIX = [
    # Step -10 ... Step -1: rough "what fraction of converters had which
    # channel at each step before purchase" — calibrated against the
    # Bridgestone/BSFS deck observation that 60-70% of converters enter via
    # search, then funnel through trailer → review → ticket lookup → purchase.
    {-10: {'ORGANIC_SEARCH': 0.55, 'TRAILER': 0.20, 'SOCIAL_YOUTUBE': 0.15, 'PRESS': 0.10}},
    {-9:  {'ORGANIC_SEARCH': 0.45, 'TRAILER': 0.25, 'SOCIAL_YOUTUBE': 0.18, 'SOCIAL_TIKTOK': 0.12}},
    {-8:  {'TRAILER': 0.35, 'ORGANIC_SEARCH': 0.30, 'SOCIAL_TIKTOK': 0.18, 'SOCIAL_INSTAGRAM': 0.17}},
    {-7:  {'TRAILER': 0.30, 'SOCIAL_YOUTUBE': 0.25, 'SOCIAL_TIKTOK': 0.20, 'PRESS': 0.15, 'PAID_AD': 0.10}},
    {-6:  {'TALENT_MENTION': 0.25, 'PRESS': 0.22, 'REVIEW': 0.20, 'TRAILER': 0.18, 'CREATOR_INFLUENCER': 0.15}},
    {-5:  {'CREATOR_INFLUENCER': 0.28, 'TALENT_MENTION': 0.22, 'BRAND_PARTNERSHIP': 0.15, 'TRAILER': 0.20, 'REVIEW': 0.15}},
    {-4:  {'REVIEW': 0.32, 'PRESS': 0.25, 'GOOGLE_REVIEW': 0.18, 'TRAILER': 0.13, 'SOCIAL_INSTAGRAM': 0.12}},
    {-3:  {'GOOGLE_REVIEW': 0.30, 'REVIEW': 0.28, 'ORGANIC_SEARCH': 0.22, 'PRESS': 0.20}},
    {-2:  {'SHOWTIME_LOOKUP': 0.55, 'GOOGLE_REVIEW': 0.20, 'ORGANIC_SEARCH': 0.15, 'REVIEW': 0.10}},
    {-1:  {'TICKETING': 0.78, 'SHOWTIME_LOOKUP': 0.15, 'ORGANIC_SEARCH': 0.07}},
]

_FALLBACK_TOP_PATHS = [
    {'path': ['ORGANIC_SEARCH', 'TRAILER', 'REVIEW', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],         'pct': 18.0},
    {'path': ['TRAILER', 'SOCIAL_TIKTOK', 'REVIEW', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],          'pct': 13.0},
    {'path': ['ORGANIC_SEARCH', 'TRAILER', 'CREATOR_INFLUENCER', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'], 'pct': 9.5},
    {'path': ['ORGANIC_SEARCH', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],                              'pct': 8.0},
    {'path': ['SOCIAL_YOUTUBE', 'TRAILER', 'REVIEW', 'TICKETING', 'CONVERSION'],                            'pct': 7.0},
    {'path': ['TALENT_MENTION', 'TRAILER', 'PRESS', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],          'pct': 6.5},
    {'path': ['PRESS', 'REVIEW', 'GOOGLE_REVIEW', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],            'pct': 5.0},
    {'path': ['SOCIAL_TIKTOK', 'TRAILER', 'TICKETING', 'CONVERSION'],                                       'pct': 4.5},
    {'path': ['BRAND_PARTNERSHIP', 'TRAILER', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],                'pct': 3.5},
    {'path': ['CREATOR_INFLUENCER', 'SOCIAL_TIKTOK', 'TICKETING', 'CONVERSION'],                            'pct': 3.0},
]


# ── Public: scaling math ─────────────────────────────────────────────────────

def compute_implied_audience(
    *,
    box_office_millions: float,
    ticket_price: float = DEFAULT_TICKET_PRICE,
    audience_fraction: float = DEFAULT_AUDIENCE_FRACTION,
) -> int:
    """Box office $ → implied # of distinct ticket-buyers (panel-scale target).

    box_office_usd / ticket_price gives total tickets sold; multiplying by
    audience_fraction (≈0.77) discounts for repeat viewers / family-of-4
    purchases-by-one-person. Returns an int. Returns 0 on bad input.
    """
    try:
        bo = float(box_office_millions or 0) * 1_000_000.0
        tp = max(float(ticket_price or DEFAULT_TICKET_PRICE), 1.0)
        af = max(0.05, min(float(audience_fraction or DEFAULT_AUDIENCE_FRACTION), 1.0))
        return int(round((bo / tp) * af))
    except Exception:
        return 0


def compute_scaling_factor(
    *,
    implied_audience: int,
    panel_converters: int,
) -> float:
    """How much we need to multiply panel counts by to match implied audience.

    Returns 1.0 when there's nothing to scale (no panel converters or no
    implied audience) so the caller can pass it through unconditionally.
    """
    if implied_audience <= 0 or panel_converters <= 0:
        return 1.0
    return float(implied_audience) / float(panel_converters)


def scale_summary_counts(summary: dict, scaling_factor: float) -> dict:
    """Return a deep-ish copy of the summary with count fields scaled up.

    Percentages are LEFT ALONE — they're rates, not counts. We only multiply
    fields that represent absolute user/event counts. The dashboard renders
    this as the "Scaled to BO" view alongside the raw panel observation.
    """
    if scaling_factor is None or scaling_factor <= 1.0 + 1e-9:
        return summary  # nothing to scale; pass-through

    def _scale_int(v):
        if isinstance(v, (int, float)) and v >= 0:
            return int(round(v * scaling_factor))
        return v

    out = dict(summary)
    out['meta'] = dict(summary.get('meta') or {})
    out['meta']['scaled_to_box_office'] = True
    out['meta']['scaling_factor'] = round(scaling_factor, 2)

    # KPIs: scale the count-shaped ones, leave rate-shaped ones.
    kpis = dict(summary.get('kpis') or {})
    for k in ('total_users', 'converted_users'):
        if k in kpis:
            kpis[k] = _scale_int(kpis[k])
    out['kpis'] = kpis

    # Touchpoints: scale reach + converters_reached on each row, plus the
    # cohort_size / converters summary fields and the touch_distribution
    # bucket counts. reach_pct / lift_pct / cadence stay as-is.
    tp = dict(summary.get('touchpoints') or {})
    if 'cohort_size' in tp: tp['cohort_size'] = _scale_int(tp['cohort_size'])
    if 'converters'  in tp: tp['converters']  = _scale_int(tp['converters'])
    new_rows = []
    for r in (tp.get('rows') or []):
        rr = dict(r)
        for k in ('reach', 'converters_reached'):
            if k in rr: rr[k] = _scale_int(rr[k])
        new_rows.append(rr)
    tp['rows'] = new_rows
    new_overlap = []
    for p in (tp.get('overlap') or []):
        pp = dict(p)
        for k in ('users', 'converters'):
            if k in pp: pp[k] = _scale_int(pp[k])
        new_overlap.append(pp)
    tp['overlap'] = new_overlap
    new_dist = []
    for b in (tp.get('touch_distribution') or []):
        bb = dict(b)
        for k in ('converters', 'non_converters', 'total'):
            if k in bb: bb[k] = _scale_int(bb[k])
        new_dist.append(bb)
    tp['touch_distribution'] = new_dist
    out['touchpoints'] = tp

    # Path to purchase: scale column user counts.
    ptp = dict(summary.get('path_to_purchase') or {})
    if 'cohort_size' in ptp: ptp['cohort_size'] = _scale_int(ptp['cohort_size'])
    new_cols = []
    for col in (ptp.get('columns') or []):
        cc = dict(col)
        cc['users'] = _scale_int(cc.get('users', 0))
        new_top_labels = []
        for tl in (cc.get('top_labels') or []):
            ttl = dict(tl)
            ttl['users'] = _scale_int(ttl.get('users', 0))
            new_top_labels.append(ttl)
        cc['top_labels'] = new_top_labels
        new_top_hosts = []
        for th in (cc.get('top_hosts') or []):
            tth = dict(th)
            tth['users'] = _scale_int(tth.get('users', 0))
            new_top_hosts.append(tth)
        cc['top_hosts'] = new_top_hosts
        new_cols.append(cc)
    ptp['columns'] = new_cols
    new_paths = []
    for p in (ptp.get('top_paths') or []):
        pp = dict(p)
        pp['users'] = _scale_int(pp.get('users', 0))
        new_paths.append(pp)
    ptp['top_paths'] = new_paths
    out['path_to_purchase'] = ptp

    # Cuts: scale users + converted in each bucket.
    new_cuts: dict[str, list] = {}
    for axis, buckets in (summary.get('cuts') or {}).items():
        new_buckets = []
        for b in (buckets or []):
            bb = dict(b)
            for k in ('users', 'converted'):
                if k in bb: bb[k] = _scale_int(bb[k])
            new_buckets.append(bb)
        new_cuts[axis] = new_buckets
    out['cuts'] = new_cuts

    # Clusters: same shape as cuts buckets.
    new_clusters = []
    for c in (summary.get('clusters') or []):
        cc = dict(c)
        for k in ('users', 'converted'):
            if k in cc: cc[k] = _scale_int(cc[k])
        new_clusters.append(cc)
    out['clusters'] = new_clusters

    # Keywords + post_hosts: scale users
    out['keywords'] = [
        dict(k, users=_scale_int(k.get('users', 0)))
        for k in (summary.get('keywords') or [])
    ]
    out['post_hosts'] = [
        dict(h, users=_scale_int(h.get('users', 0)))
        for h in (summary.get('post_hosts') or [])
    ]

    return out


# ── Public: Claude synthesis ─────────────────────────────────────────────────

_SYSTEM_SYNTH = """\
You are a senior box-office attribution analyst. You will be given:
  * A movie title, US box office gross, ticket price, and release window.
  * A list of marketing surfaces the campaign actually used (talent
    mentions, brand partnerships, social channels) — parsed from the
    user's "Extra Touchpoint Keywords" rules.
  * What we ALREADY observed in the panel: # converters, # cohort,
    touchpoint reach %, top paths. This may be very sparse or zero.

Your job is to ESTIMATE a plausible attribution mix for a movie of this
scale and talent profile, calibrated against:
  * Tentpole movies (>$200M domestic): 75-90% trailer reach,
    85-95% search reach in the 14 days pre-release.
  * Mid-budget movies ($30-200M domestic): 50-70% trailer reach,
    60-80% search reach.
  * Limited release (<$30M domestic): 30-50% trailer reach, 40-60% search.

Output JSON EXACTLY in this shape (no markdown, no code fences):
{
  "touchpoints": [
    {"label": "TRAILER",         "reach_pct": 74.5, "share_of_converters_pct": 82.0,
     "avg_days_to_conversion": 18.0, "avg_touches_per_user": 2.3, "lift_pct": 55.0},
    {"label": "ORGANIC_SEARCH",  ...},
    {"label": "SOCIAL_YOUTUBE",  ...},
    {"label": "SOCIAL_TIKTOK",   ...},
    {"label": "SOCIAL_INSTAGRAM",...},
    {"label": "PRESS",           ...},
    {"label": "REVIEW",          ...},
    {"label": "SHOWTIME_LOOKUP", ...},
    {"label": "GOOGLE_REVIEW",   ...},
    {"label": "TICKETING",       "reach_pct": 90+, "lift_pct": null},
    {"label": "PAID_AD",         ...},
    {"label": "TALENT_MENTION",  ...},
    {"label": "CREATOR_INFLUENCER",...},
    {"label": "BRAND_PARTNERSHIP",...},
    {"label": "SOUNDTRACK",      ...}
  ],
  "path_columns": [
    {"index": -10, "mix": {"ORGANIC_SEARCH": 0.50, "TRAILER": 0.25, ...}},
    {"index": -9,  "mix": {...}},
    ...
    {"index": -1,  "mix": {"TICKETING": 0.78, ...}}
  ],
  "top_paths": [
    {"path": ["ORGANIC_SEARCH","TRAILER","REVIEW","SHOWTIME_LOOKUP","TICKETING","CONVERSION"], "pct": 17.5},
    ...
  ],
  "avg_touches_before_purchase": 5.8,
  "avg_days_to_purchase": 14.2,
  "conversion_pct_of_cohort": 100.0,
  "notes": "1-2 sentences on why this mix; cite a comparable movie."
}

Hard rules:
  * Every column "mix" object's values must SUM to roughly 1.0 (i.e.
    column percentages, not user counts).
  * Provide at least 8 top_paths.
  * TICKETING must dominate column -1 (the step right before purchase).
  * CONVERSION must end every top_path.
  * lift_pct may be null (e.g. for TICKETING which is the conversion itself).
  * Numbers must reflect the box-office scale you were given. A $50M movie
    should NOT have a $400M's trailer reach.
  * If a marketing surface was named in the input but is irrelevant to
    movies (e.g. /reviews keyword for a B2B brand), still include the
    standard movie touchpoints — just down-weight the irrelevant ones.

Output JSON ONLY."""


def synthesize_movie_journey(
    *,
    target: str,
    project_name: str,
    start_date: str,
    end_date: str,
    box_office_millions: float,
    ticket_price: float,
    extra_touchpoint_keywords: dict,
    panel_converters: int,
    panel_observed_touchpoints: Optional[list[dict]] = None,
    panel_top_paths: Optional[list[dict]] = None,
    steps: int = 10,
    max_tokens: int = 3000,
    temperature: float = 0.3,
) -> Optional[dict]:
    """Ask Claude for a plausible movie journey; fall back to canonical
    fixtures when Claude is offline. Returns a dict with keys:
        touchpoints (list), path_columns (list), top_paths (list),
        avg_touches_before_purchase, avg_days_to_purchase,
        conversion_pct_of_cohort, notes, source ('claude'|'fallback').
    """
    # When Claude is offline, return the canonical fixture immediately so
    # the dashboard always has SOMETHING to render. Scaled later by the
    # caller via scale_modeled_to_audience().
    if claude_messages is None or is_hybrid_enabled is None or get_claude_client is None:
        return _fallback_synthesis(steps=steps)
    try:
        if not is_hybrid_enabled() or get_claude_client() is None:
            return _fallback_synthesis(steps=steps)
    except Exception:
        return _fallback_synthesis(steps=steps)

    # Build a focused prompt: keep the input data small so Claude
    # spends its tokens reasoning about the estimate, not parsing inputs.
    payload = {
        'movie_title':                target,
        'project_name':               project_name,
        'release_window':             {'start': start_date, 'end': end_date},
        'us_box_office_millions':     box_office_millions,
        'avg_ticket_price_usd':       ticket_price,
        'implied_audience':           compute_implied_audience(
            box_office_millions=box_office_millions,
            ticket_price=ticket_price,
        ),
        'campaign_surfaces':          extra_touchpoint_keywords or {},
        'steps_to_synthesize':        steps,
        'panel_observed': {
            'converters':             panel_converters,
            'touchpoints': [
                {'label': r.get('label'), 'reach_pct': r.get('reach_pct')}
                for r in (panel_observed_touchpoints or [])[:15]
            ],
            'top_paths':              (panel_top_paths or [])[:5],
        },
    }
    user_msg = (
        "Estimate the path-to-purchase + touchpoint mix for the movie "
        "below. Output JSON only matching the schema in the system prompt.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        raw = claude_messages(
            system=_SYSTEM_SYNTH,
            user=user_msg,
            max_tokens=max_tokens,
            temperature=temperature,
        ) or ''
    except Exception as e:
        print(f"[Journey IQ synthesize] claude_messages failed: {e}")
        return _fallback_synthesis(steps=steps)

    parsed = _parse_synth_json(raw)
    if not parsed:
        return _fallback_synthesis(steps=steps)
    parsed['source'] = 'claude'
    return parsed


def _fallback_synthesis(*, steps: int = 10) -> dict:
    """Static canonical fixture — used when Claude is unavailable."""
    return {
        'touchpoints':                  [dict(r) for r in _FALLBACK_TOUCHPOINTS],
        'path_columns':                 _expand_fallback_path_columns(steps=steps),
        'top_paths':                    [dict(p) for p in _FALLBACK_TOP_PATHS],
        'avg_touches_before_purchase':  5.5,
        'avg_days_to_purchase':         14.0,
        'conversion_pct_of_cohort':     100.0,
        'notes':                        ('Canonical movie attribution mix '
                                         '(Claude offline — used fallback fixture).'),
        'source':                       'fallback',
    }


def _expand_fallback_path_columns(*, steps: int) -> list[dict]:
    """Return exactly `steps` columns of the canonical mix, right-aligned.

    When steps <= 10 we keep the LAST `steps` columns of the canonical
    fixture (those are closest to purchase and most stable). When
    steps > 10 we pad with copies of the oldest column on the left.
    Indexes are always renumbered cleanly so the rightmost is -1.
    """
    full = [list(d.items())[0] for d in _FALLBACK_PATH_COLUMN_MIX]  # [(-10, {...}), ...]
    if steps <= len(full):
        slice_ = full[-steps:]
    else:
        pad = [full[0]] * (steps - len(full))
        slice_ = pad + full
    n = len(slice_)
    return [{'index': -(n - i), 'mix': mix} for i, (_, mix) in enumerate(slice_)]


def _parse_synth_json(raw: str) -> Optional[dict]:
    """Tolerate code fences / preamble. Returns dict or None."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?', '', text, count=1).strip()
        if text.endswith('```'):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    if '{' in text and '}' in text:
        snippet = text[text.find('{'): text.rfind('}') + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


# ── Public: adapter from synth → dashboard JSON ──────────────────────────────

def synth_to_dashboard_payload(
    synth: dict,
    *,
    target_audience: int,
) -> dict:
    """Convert Claude's structured estimate into the JSON shape the
    dashboard already knows how to render. Returns:
        {'touchpoints': {...}, 'path_to_purchase': {...},
         'kpis': {...}, 'cohort_size': N}
    Everything is sized to `target_audience` (the implied # of buyers).
    """
    n = max(1, int(target_audience or 0))
    # ── Touchpoints ────────────────────────────────────────────────────
    tp_rows = []
    for r in (synth.get('touchpoints') or []):
        reach_pct = float(r.get('reach_pct') or 0.0)
        reach = int(round(n * reach_pct / 100.0))
        sh_conv_pct = float(r.get('share_of_converters_pct') or reach_pct)
        converters_reached = int(round(n * sh_conv_pct / 100.0))
        tp_rows.append({
            'label':                  r.get('label'),
            'reach':                  reach,
            'reach_pct':              round(reach_pct, 1),
            'converters_reached':     converters_reached,
            'share_of_converters':    round(sh_conv_pct, 1),
            'conv_rate_when_reached': 100.0,  # we're synthesizing converters only
            'conv_rate_when_not':     0.0,
            'baseline_conv_rate':     100.0,
            'lift_pct':               r.get('lift_pct'),
            'avg_days_to_conversion': r.get('avg_days_to_conversion'),
            'avg_touches_per_user':   r.get('avg_touches_per_user'),
        })

    touchpoints = {
        'baseline_conv_rate': 100.0,
        'cohort_size':        n,
        'converters':         n,
        'rows':               tp_rows,
        'overlap':            _synth_overlap(tp_rows, n),
        'touch_distribution': _synth_touch_distribution(
            n, avg_touches=synth.get('avg_touches_before_purchase') or 5.0),
    }

    # ── Path to purchase ─────────────────────────────────────────────
    columns = []
    for c in (synth.get('path_columns') or []):
        idx = int(c.get('index', 0))
        mix = c.get('mix') or {}
        # Column "users" = N (every converter contributes); we just allocate
        # the per-label split.
        top_labels = []
        for lbl, frac in sorted(mix.items(), key=lambda kv: -float(kv[1])):
            users = int(round(n * float(frac)))
            top_labels.append({
                'label': lbl, 'users': users,
                'pct':   round(100.0 * float(frac), 1),
            })
        columns.append({
            'index':      idx,
            'label':      f'Step {idx}',
            'users':      n,
            'users_pct':  100.0,
            'top_labels': top_labels[:6],
            'top_hosts':  [],  # synth doesn't know specific hosts
        })
    # CONVERSION column at index 0
    columns.append({
        'index': 0, 'label': 'CONVERSION',
        'users': n, 'users_pct': 100.0,
        'top_labels': [{'label': 'CONVERSION', 'users': n, 'pct': 100.0}],
        'top_hosts': [],
    })

    top_paths = []
    for p in (synth.get('top_paths') or []):
        pct = float(p.get('pct') or p.get('users_pct') or 0.0)
        users = int(round(n * pct / 100.0))
        top_paths.append({
            'path': list(p.get('path') or []),
            'users': users,
            'users_pct': round(pct, 1),
        })

    path_to_purchase = {
        'mode':         'modeled',
        'cohort_size':  n,
        'steps':        max(1, len(columns) - 1),
        'columns':      columns,
        'top_paths':    top_paths,
    }

    avg_days = float(synth.get('avg_days_to_purchase') or 14.0)
    avg_touches = float(synth.get('avg_touches_before_purchase') or 5.0)
    kpis = {
        'total_users':                n,
        'converted_users':            n,
        'conversion_pct':             100.0,
        'avg_journey_duration_days':  round(avg_days, 1),
        'avg_sessions_to_convert':    round(max(1.0, avg_touches / 2.0), 1),
        'avg_events_per_user':        round(avg_touches + 1, 1),
    }

    return {
        'touchpoints':      touchpoints,
        'path_to_purchase': path_to_purchase,
        'kpis':             kpis,
        'cohort_size':      n,
    }


def _synth_overlap(tp_rows: list[dict], n: int) -> list[dict]:
    """Estimate top co-occurrence pairs from individual reach %s assuming
    independence (lower bound — real overlap usually higher). Used purely
    for the dashboard overlap card in synthetic runs."""
    out = []
    for i in range(len(tp_rows)):
        for j in range(i + 1, len(tp_rows)):
            a, b = tp_rows[i], tp_rows[j]
            pa = (a.get('reach_pct') or 0) / 100.0
            pb = (b.get('reach_pct') or 0) / 100.0
            if pa <= 0 or pb <= 0:
                continue
            users = int(round(n * pa * pb))
            out.append({
                'a':           a['label'],
                'b':           b['label'],
                'users':       users,
                'users_pct':   round(100.0 * pa * pb, 1),
                'converters':  users,
                'conv_rate':   100.0,
            })
    out.sort(key=lambda x: -x['users'])
    return out[:12]


def _synth_touch_distribution(n: int, *, avg_touches: float) -> list[dict]:
    """Lognormal-shaped bucket distribution centred on avg_touches, with
    a small tail of 0-touch and 11+ buckets for realism."""
    # Hand-tuned weights that roughly track real-world touch distributions:
    # most converters get 4-6 touches, a meaningful tail at 7-10 and 11+.
    weights_by_avg = {
        3.0:  [0.02, 0.10, 0.35, 0.30, 0.15, 0.08],
        5.0:  [0.01, 0.05, 0.20, 0.35, 0.25, 0.14],
        7.0:  [0.01, 0.03, 0.10, 0.27, 0.34, 0.25],
        10.0: [0.00, 0.02, 0.05, 0.18, 0.30, 0.45],
    }
    # Pick the nearest preset
    best = min(weights_by_avg.keys(), key=lambda k: abs(k - avg_touches))
    weights = weights_by_avg[best]
    buckets = ['0', '1', '2-3', '4-6', '7-10', '11+']
    out = []
    for b, w in zip(buckets, weights):
        cnt = int(round(n * w))
        out.append({
            'bucket':         b,
            'converters':     cnt,
            'non_converters': 0,
            'total':          cnt,
            'conv_pct':       100.0,
        })
    return out


# ── Blend real + modeled into the canonical dashboard payload ────────────────

def blend_real_and_modeled(
    *,
    real_summary: dict,
    modeled_summary: dict,
    real_converters: int,
    threshold: int = SPARSE_COHORT_THRESHOLD,
) -> dict:
    """Pick the modeled fields whenever the real cohort is sparse for that
    field, otherwise keep real. The dashboard already supports a view-mode
    toggle; this is the default "blended" payload it shows.

    Currently: if real_converters < threshold, use modeled wholesale (still
    keeping real meta so the user knows what the panel actually saw).
    Otherwise use real wholesale. This avoids field-by-field stitching
    that can produce inconsistent percentages.
    """
    if real_converters >= threshold:
        return real_summary

    out = dict(modeled_summary)
    # Preserve the real meta for transparency.
    out['meta'] = dict(real_summary.get('meta') or {})
    out['meta']['blend_mode'] = 'modeled'
    out['meta']['panel_real_converters'] = real_converters
    out['meta']['blend_threshold'] = threshold
    return out


__all__ = [
    'DEFAULT_TICKET_PRICE',
    'DEFAULT_AUDIENCE_FRACTION',
    'SPARSE_COHORT_THRESHOLD',
    'compute_implied_audience',
    'compute_scaling_factor',
    'scale_summary_counts',
    'synthesize_movie_journey',
    'synth_to_dashboard_payload',
    'blend_real_and_modeled',
]
