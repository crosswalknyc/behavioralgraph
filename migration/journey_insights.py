"""
journey_insights.py — Claude prose pass for Digital Journey IQ.

Given the aggregated journey JSON, ask Claude to mine 6-10 SHORT, concrete
"interesting facts" the BSFS analyst would normally call out in the deck —
e.g. "68% of converters used a discount code", "Discount Tire held the
first organic search result 62% of the time."

Hard rules in the prompt:
  - Each fact must cite a number from the data we passed in. No invented
    numbers, no qualitative-only statements.
  - 1-2 sentences each. No bullet preambles ("Interestingly,"...).
  - Surface drop-off cliffs, conversion gaps between clusters, dominant
    detour destinations, and standout keywords.

Gracefully no-ops (returns []) when hybrid reasoning is disabled or the
Anthropic client can't be initialized — the dashboard just hides the
"Interesting Facts" panel in that case. Mirrors the pattern used in
migration/hybrid_reasoning.audience_composition_prepass.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional


# We accept either the `bg-webapp.claude_client` (used by SVOD attribution)
# or `migration.claude_client` (used by BG.py hybrid reasoning) — whichever
# import succeeds first. The two modules expose identical surfaces.
try:
    from migration.claude_client import claude_messages, get_claude_client, is_hybrid_enabled
except ImportError:
    try:
        from claude_client import claude_messages, get_claude_client, is_hybrid_enabled  # type: ignore
    except ImportError:
        claude_messages = None       # type: ignore
        get_claude_client = None     # type: ignore
        is_hybrid_enabled = None     # type: ignore


_SYSTEM = """\
You are a senior consumer-insights analyst writing the "Interesting Facts"
sidebar for a digital-journey dashboard, in the voice of the Crosswalk
BSFS deck.

You will be given a JSON blob with:
  - target / project / date range
  - top-line KPIs (total users, conversion %, avg journey duration in days,
    avg sessions to convert)
  - inception clusters (Search / Direct / Social / Ad / Referral / AI agent
    / Other / ALL) each with funnel-step active-user counts and drop-off %,
    plus detour destinations
  - cuts: per-cohort clusters by INTEREST (Sports / Entertainment / Family
    / News / Finance / ...), GENDER, AGE bracket, ETHNICITY, INCOME,
    EDUCATION, MARITAL status, and CHILDREN (family vs non-family).
    Each cut bucket has its own conversion rate + top touchpoints.
  - touchpoints: per-channel reach %, conversion-rate-when-reached,
    conversion-rate-when-not-reached, lift %, avg days from first touch
    to conversion (cadence), avg touches per reached user, plus a
    co-occurrence list (overlap) of the top touchpoint pairs and a
    touch-count distribution (how many touches purchasers received).
  - top inception search keywords (with user counts)
  - top hosts visited by non-converters AFTER the last target mention

Your job:
  - Return EXACTLY a JSON object: {"facts": ["...", "...", ...]}
  - 8-12 facts. Each fact 1-2 sentences. No preamble, no markdown, no
    bullets — plain prose strings.
  - Every fact MUST cite a number that appears in the input JSON. No
    invented percentages, no projections beyond what is provided.
  - Prefer facts that compare two cohorts (Sports fans vs Movie fans,
    family vs non-family, Trailer-seers vs non-seers) over single-stat
    restatements.
  - Surface drop-off cliffs (a single step where >40% of users abandon),
    dominant detour destinations (>20% of cohort visits one host),
    standout inception keywords, the touchpoints with the highest
    conversion lift, the cadence sweet-spot ("converters who saw a
    trailer averaged X days to purchase"), and the strongest overlap
    pairs ("trailer + creator-influencer drove the highest lift").
  - At least 2 facts should be demographic / interest cuts.
  - At least 2 facts should be touchpoint-level (reach, lift, cadence,
    or overlap).
  - If a number is exactly 0 or N/A, do not invent context to fill it in.
  - Do not name brands the data does not name.
  - Refer to the target by its actual name (as given in the JSON), not
    "the target" or "this brand".

Output JSON ONLY. No code fences, no commentary."""


def generate_interesting_facts(
    *,
    target: str,
    project_name: str,
    start_date: str,
    end_date: str,
    kpis: dict,
    clusters: list[dict],
    keywords: list[dict],
    post_hosts: list[dict],
    cuts: Optional[dict] = None,
    touchpoints: Optional[dict] = None,
    max_tokens: int = 1800,
    temperature: float = 0.4,
) -> list[str]:
    """Return 6-10 prose facts, or [] if Claude is unavailable / fails."""
    if claude_messages is None or is_hybrid_enabled is None:
        return []
    try:
        if not is_hybrid_enabled():
            return []
        if get_claude_client is not None and get_claude_client() is None:
            return []
    except Exception:
        return []

    # Trim clusters to the most useful summary fields so the prompt stays
    # small and Claude focuses on the numbers we care about.
    trimmed_clusters = []
    for c in clusters or []:
        trimmed_clusters.append({
            'inception':       c.get('inception'),
            'users':           c.get('users'),
            'users_pct':       c.get('users_pct'),
            'converted':       c.get('converted'),
            'conversion_pct':  c.get('conversion_pct'),
            'funnel': [
                {
                    'step':         f.get('step'),
                    'active_users': f.get('active_users'),
                    'drop_off_pct': f.get('drop_off_pct'),
                }
                for f in (c.get('funnel') or [])
            ],
            'detours': [
                {
                    'host':      d.get('host'),
                    'users':     d.get('users'),
                    'users_pct': d.get('users_pct'),
                }
                for d in (c.get('detours') or [])[:5]
            ],
        })

    # Trim cuts: pass only the top 4 buckets per axis (plus ALL) so the
    # prompt stays well under the context budget. We keep label, users,
    # conversion %, and the top-3 touchpoints per bucket — the comparison
    # bait Claude needs.
    trimmed_cuts: dict[str, list[dict]] = {}
    for axis, buckets in (cuts or {}).items():
        trimmed_cuts[axis] = [
            {
                'label':            b.get('label') or b.get('inception'),
                'users':            b.get('users'),
                'users_pct':        b.get('users_pct'),
                'conversion_pct':   b.get('conversion_pct'),
                'top_touchpoints': [
                    {'label': t.get('label'),
                     'reach_pct': t.get('reach_pct')}
                    for t in (b.get('top_touchpoints') or [])[:3]
                ],
            }
            for b in (buckets or [])[:5]
        ]

    # Trim touchpoints: drop low-signal rows (no lift data, sub-1% reach).
    tp_rows = [
        {
            'label':                  r.get('label'),
            'reach_pct':              r.get('reach_pct'),
            'share_of_converters':    r.get('share_of_converters'),
            'conv_rate_when_reached': r.get('conv_rate_when_reached'),
            'conv_rate_when_not':     r.get('conv_rate_when_not'),
            'lift_pct':               r.get('lift_pct'),
            'avg_days_to_conversion': r.get('avg_days_to_conversion'),
            'avg_touches_per_user':   r.get('avg_touches_per_user'),
        }
        for r in ((touchpoints or {}).get('rows') or [])
        if (r.get('reach_pct') or 0) >= 1.0
    ][:20]
    tp_overlap = [
        {'a': p.get('a'), 'b': p.get('b'),
         'users_pct': p.get('users_pct'), 'conv_rate': p.get('conv_rate')}
        for p in ((touchpoints or {}).get('overlap') or [])
    ][:12]
    tp_distribution = (touchpoints or {}).get('touch_distribution') or []

    payload = {
        'target':         target,
        'project_name':   project_name,
        'start_date':     start_date,
        'end_date':       end_date,
        'kpis':           kpis or {},
        'clusters':       trimmed_clusters,
        'cuts':           trimmed_cuts,
        'touchpoints': {
            'baseline_conv_rate': (touchpoints or {}).get('baseline_conv_rate'),
            'rows':               tp_rows,
            'overlap':            tp_overlap,
            'touch_distribution': tp_distribution,
        },
        'top_inception_keywords': keywords or [],
        'top_post_non_conversion_hosts': post_hosts or [],
    }
    user_msg = (
        "Mine the most interesting, deck-ready facts from the data below. "
        "Output JSON only: {\"facts\": [\"...\", ...]}.\n\nDATA:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        raw = claude_messages(
            system=_SYSTEM,
            user=user_msg,
            max_tokens=max_tokens,
            temperature=temperature,
        ) or ''
    except Exception as e:
        print(f"[Journey IQ insights] claude_messages failed: {e}")
        return []

    return _parse_facts(raw)


def _parse_facts(raw: str) -> list[str]:
    """Tolerate models that wrap JSON in code fences or add a preamble.

    Tries (in order):
      1. json.loads on the whole string
      2. json.loads on the substring between the first '{' and the matching
         '}' (handles "Here you go: { ... }" cases)
      3. regex extraction of a top-level "facts": [ ... ] array
    """
    if not raw:
        return []
    text = raw.strip()
    # Strip markdown code fences if present.
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?', '', text, count=1).strip()
        if text.endswith('```'):
            text = text[: -3].strip()

    # Attempt 1: full JSON.
    try:
        obj = json.loads(text)
        facts = obj.get('facts') if isinstance(obj, dict) else None
        if isinstance(facts, list):
            return _clean_facts(facts)
    except Exception:
        pass

    # Attempt 2: substring between first '{' and last '}'.
    if '{' in text and '}' in text:
        snippet = text[text.find('{'): text.rfind('}') + 1]
        try:
            obj = json.loads(snippet)
            facts = obj.get('facts') if isinstance(obj, dict) else None
            if isinstance(facts, list):
                return _clean_facts(facts)
        except Exception:
            pass

    # Attempt 3: regex extract a "facts": [...] array.
    m = re.search(r'"facts"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if m:
        inner = m.group(1)
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', inner)
        return _clean_facts(items)

    return []


def _clean_facts(items: list) -> list[str]:
    out: list[str] = []
    for it in items:
        if not isinstance(it, str):
            continue
        s = it.strip()
        if not s:
            continue
        # Strip leading bullet/numbering noise.
        s = re.sub(r'^[\-\*\u2022\u00b7]\s*', '', s)
        s = re.sub(r'^\d+[\.\)]\s*', '', s)
        if len(s) < 8:
            continue
        out.append(s)
        if len(out) >= 12:
            break
    return out


__all__ = ['generate_interesting_facts']
