"""Blue IQ synthetic-fill agents (2026-07-27).

When the panel-side query for a Blue IQ card returns zero (or very
few) rows for a live-computed district cell — typically because a
category name doesn't line up with `reference.host_mapping` today,
because the cube is stale, or because the district's panel simply
doesn't have enough activity in the category — the card would render
"No data for this slice." Jenna's directive (2026-07-27):

    "when I try a district cut I get this. they should never be blank.
     it should always be populated. an agent can do it synthetically
     if needed."

So this module exposes two agent-driven synthesizers that produce a
reasonable market-share breakdown for a given US geography by asking
an OpenAI web-search agent for the current published-data reach
figures (StatCounter, Similarweb, Statista, Pew, eMarketer). The
result is scaled to the caller's panel size so the frontend's
`{name, panelists, share}` shape renders correctly. Every synthetic
row is stamped `synthetic: True` so the payload is auditable and the
UI can (optionally) show a "modeled from external research" note.

Fail-open contract: every entrypoint returns `[]` on any error. The
caller keeps whatever it had (an empty list, or its own fallback).
We never raise.

Cache: results are cached in S3 under
`blue_iq/synth/v1/{card}__{geo_slug}.json` with a 7-day TTL. Market
share moves slowly and re-agenting for every district request would
burn OpenAI budget for zero gain.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

S3_BUCKET   = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX   = 'blue_iq/synth/v1/'
CACHE_TTL_S = 7 * 24 * 3600    # 7 days — market share moves slowly

AGENT_TIMEOUT_S = float(os.environ.get('BLUE_IQ_SYNTH_TIMEOUT', '45'))
AGENT_MODEL     = os.environ.get('BLUE_IQ_SYNTH_MODEL', 'gpt-4o')


# ── S3 cache ──────────────────────────────────────────────────────────────

def _s3():
    try:
        from app import s3_client                                # type: ignore
        if s3_client is not None:
            return s3_client
    except Exception:
        pass
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', (s or '').lower()).strip('_') or 'none'


def _cache_key(card: str, geo_label: str) -> str:
    return f"{S3_PREFIX}{_slug(card)}__{_slug(geo_label)}.json"


def _cache_get(card: str, geo_label: str) -> Optional[list[dict]]:
    key = _cache_key(card, geo_label)
    try:
        resp = _s3().get_object(Bucket=S3_BUCKET, Key=key)
        last_mod = resp.get('LastModified')
        if last_mod and (datetime.now(timezone.utc) - last_mod).total_seconds() > CACHE_TTL_S:
            return None
        payload = json.loads(resp['Body'].read().decode('utf-8'))
        rows = payload.get('rows') if isinstance(payload, dict) else payload
        return rows if isinstance(rows, list) else None
    except Exception as e:
        msg = str(e)
        if 'NoSuchKey' not in msg and '404' not in msg:
            logger.debug("synth cache miss for %s|%s: %s", card, geo_label, msg)
        return None


def _cache_put(card: str, geo_label: str, rows: list[dict]) -> None:
    key = _cache_key(card, geo_label)
    try:
        _s3().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps({
                'card':         card,
                'geo_label':    geo_label,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'rows':         rows,
            }, separators=(',', ':')).encode('utf-8'),
            ContentType='application/json',
            CacheControl=f'max-age={CACHE_TTL_S}',
        )
    except Exception as e:
        logger.warning("synth cache write failed for %s|%s: %s", card, geo_label, e)


# ── OpenAI ────────────────────────────────────────────────────────────────

def _openai_client():
    try:
        from blue_iq import _openai_client as _shared             # type: ignore
        return _shared()
    except Exception:
        pass
    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            return None
        return OpenAI(api_key=api_key, timeout=AGENT_TIMEOUT_S)
    except Exception as e:
        logger.warning("synth: OpenAI client init failed: %s", e)
        return None


_SYSTEM_PROMPT = """You are a market-share researcher for a US political
audience-intelligence dashboard. When asked for the current share of
search engines or social platforms among adult US internet users in a
specific geography, produce a JSON object with a single `rows` array.
Each row is `{name, share_pct, note}` where:

    name       string — the platform brand (e.g. "Google", "TikTok").
                        Use the widely-recognized brand name, not a
                        parent company (say "Instagram" not "Meta").
    share_pct  number — 0..100, percent of adult internet users in that
                        geography who actively use the platform in the
                        trailing 30 days. Should sum to LESS than 100
                        for social (people use multiple platforms) but
                        approximately 100 for search (people usually
                        have one dominant search engine).
    note       string — 1 short phrase citing the source you leaned on
                        (e.g. "StatCounter Jul 2026", "Pew 2025",
                        "Similarweb US"). No URLs.

Ground rules:

    - Include the majors first. Never omit Google from search. Never
      omit YouTube / Facebook / Instagram / TikTok / X / Reddit from
      social (they're all >5%).
    - Return between 5 and 10 rows.
    - Numbers must be plausible: Google search is 85-92%, Bing 3-6%,
      Yahoo 1-3%, DuckDuckGo 1-3%. Facebook reach is 60-70%, YouTube
      85-90%, Instagram 40-55%, TikTok 35-50%, X 20-30%, Reddit 25-35%.
      LinkedIn 20-30%. Snapchat 20-30%.
    - If the geography is a specific US congressional district, city, or
      DMA, adjust modestly for demographic skew (e.g. a young urban
      district skews TikTok/Instagram/Reddit up, LinkedIn slightly up;
      an older rural district skews Facebook up, TikTok down). Never
      deviate more than +/- 8 points from the national baseline.
    - Output ONLY a JSON object. No prose, no markdown fences.
"""


def _parse_agent_response(text: str) -> Optional[dict]:
    if not text:
        return None
    raw = text.strip()
    raw = re.sub(r'^```(?:json|JSON)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)
    raw = re.sub(r'\u3010[^\u3011]*\u3011', '', raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find('{')
    if start < 0:
        return None
    try:
        return json.loads(raw[start:])
    except json.JSONDecodeError:
        return None


def _agent_shares(card: str, geo_label: str) -> list[dict]:
    """Call the agent and return normalized [{name, share_pct, note}]."""
    client = _openai_client()
    if client is None:
        logger.info("synth[%s]: no OpenAI client; skipping", card)
        return []
    category = 'search engines' if card == 'search_engines' else 'social platforms'
    user_prompt = (
        f"Geography: {geo_label}\n"
        f"Category: {category}\n"
        "Return the JSON object per the system instructions."
    )
    t0 = time.time()
    text = ''
    try:
        resp = client.responses.create(
            model=AGENT_MODEL,
            tools=[{'type': 'web_search_preview'}],
            input=[
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user',   'content': user_prompt},
            ],
            max_output_tokens=4000,
        )
        text = getattr(resp, 'output_text', '') or ''
        logger.info("synth[%s|ws] %s -> %d chars (%.1fs)",
                    card, geo_label, len(text), time.time() - t0)
    except Exception as e:
        logger.info("synth[%s] web_search failed (%s); trying chat",
                    card, str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _SYSTEM_PROMPT},
                    {'role': 'user',   'content': user_prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.2,
                max_tokens=2000,
            )
            text = chat.choices[0].message.content or ''
        except Exception as e2:
            logger.warning("synth[%s] BOTH paths failed for %s: %s",
                            card, geo_label, e2)
            return []
    obj = _parse_agent_response(text)
    if not isinstance(obj, dict):
        return []
    rows = obj.get('rows') or []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get('name') or '').strip()
        try:
            share = float(r.get('share_pct'))
        except Exception:
            continue
        if not name or share <= 0:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        note = str(r.get('note') or '').strip()
        # Clamp any wildly-out-of-range values into a defensible band.
        share = max(0.1, min(share, 99.5))
        out.append({'name': name, 'share_pct': round(share, 2), 'note': note})
    if not out:
        return []
    # Sort by descending share; for search-engine cards renormalize so
    # the shares sum to <=100 (agent occasionally emits a set like
    # Google 90 + Bing 5 + Yahoo 3 + DDG 2 + others 5 = 105). Social
    # platforms deliberately don't sum to 100 (people use multiple).
    out.sort(key=lambda r: -r['share_pct'])
    if card == 'search_engines':
        total = sum(r['share_pct'] for r in out)
        if total > 100.1:
            scale = 100.0 / total
            for r in out:
                r['share_pct'] = round(r['share_pct'] * scale, 2)
    return out


# ── Public entrypoints ────────────────────────────────────────────────────

def synthesize_shares(card: str, geo_label: str, panel_size: int) -> list[dict]:
    """Return synthetic `{name, panelists, share, synthetic}` rows.

    `card` must be one of `'search_engines'` or `'social_media'`.
    `geo_label` is a human-readable label like `"California 12th
    Congressional District (San Francisco)"` that gives the agent
    enough context to skew slightly (rural vs urban, region).
    `panel_size` is the caller's panel-cell UID count; synthetic
    `panelists` values are `round(share * panel_size)` so the
    existing bar UI (which computes width from `panelists/max`)
    renders sanely alongside cube-derived panel numbers.

    Returns `[]` on any failure — caller keeps whatever placeholder it
    already had.
    """
    if card not in ('search_engines', 'social_media'):
        return []
    if not geo_label:
        geo_label = 'United States'
    panel_size = max(1, int(panel_size or 1))

    cached = _cache_get(card, geo_label)
    if cached:
        return _scale_to_panel(cached, panel_size)

    rows = _agent_shares(card, geo_label)
    if not rows:
        return []
    _cache_put(card, geo_label, rows)
    return _scale_to_panel(rows, panel_size)


def _scale_to_panel(share_rows: list[dict], panel_size: int) -> list[dict]:
    """Turn agent `[{name, share_pct, note}]` into the frontend shape
    `[{name, panelists, share, synthetic:true, source_note}]`.

    `share` is expressed as 0..1 to match the panel-side output.
    `panelists` is the share applied to `panel_size` and rounded so the
    frontend's max-scaled bar renders proportionally.
    """
    out: list[dict] = []
    for r in share_rows:
        try:
            share_pct = float(r.get('share_pct') or 0.0)
        except Exception:
            continue
        if share_pct <= 0:
            continue
        share = round(share_pct / 100.0, 4)
        panelists = int(round(share * panel_size))
        out.append({
            'name':        str(r.get('name') or '').strip(),
            'panelists':   panelists,
            'share':       share,
            'synthetic':   True,
            'source_note': str(r.get('note') or '').strip(),
        })
    return out


# ── Top-searches synthesizer ─────────────────────────────────────────
#
# The `top_searches` card shows the raw ~30 search queries the panel
# has been typing. For most districts the panel returns plenty of data
# (CA-41 returns 50 rows in ~68s). But some cuts are thin — small
# districts, sparse-panel states, or the "Live" (yesterday-only)
# lookback — and the card renders empty. Same rule as
# `synthesize_shares` above: never empty, agent-fill instead.
#
# The synthesized rows are labeled `synthetic:true` and marked
# political via `_flag_political_term`-equivalent logic on the client
# side; each row also carries an `agent_grounded:true` flag so the UI
# can show a "modeled from external research" pill.

_TOP_SEARCHES_SYSTEM = """You are a US political-audience researcher.
For a given US geography (state, congressional district, DMA, or the
whole country) tell me what people there are likely typing into
Google / Bing right now, in the past few weeks. Base your list on:

    - Pew Research + Google Trends aggregates for that region
    - Local newspaper / TV-station headline volume
    - Cost-of-living pressure (housing, insurance, groceries, gas)
    - Real ongoing news, elections, court cases, storms, sports
    - Political / policy topics ONLY when they'd realistically appear
      in the top 30 (immigration in border districts, abortion
      post-Dobbs in swing states, tax bill discourse when it's active
      in Congress, etc.). Do NOT force-fill with partisan search
      terms — most everyday searches are apolitical.

Return a JSON object `{rows: [...]}` where each row is:
    {"term": lower-case string (3-80 chars, plain English, no URLs),
     "weight": integer 1..100 (rough relative popularity),
     "political": boolean (true if the term is explicitly political,
                  a politician's name, or a policy issue; false for
                  cost-of-living, weather, sports, entertainment,
                  practical everyday searches),
     "reason": one short phrase citing WHY this district would search
               this (e.g. "Central Valley agriculture", "post-Dobbs
               abortion pill searches", "median home price stress")}

Ground rules:
    - Return 20-25 rows.
    - No duplicates. No branded product SKUs. No search-engine junk.
    - `term` must be lower-case, natural search phrasing.
    - Sort by descending `weight`.
    - Political-flagged rows should be 15-40% of the list, not more —
      most Americans do not primarily search for political content.
    - Skew for the geography: rural district ≠ urban district ≠ the
      country as a whole. Reference cost-of-living, industries, and
      demographic composition where relevant.
    - Output ONLY the JSON object. No prose, no markdown fences.
"""


def _agent_top_searches(geo_label: str) -> list[dict]:
    """Call the top-searches agent and return normalized
    `[{term, weight, political, reason}]`. Returns [] on any failure.
    """
    client = _openai_client()
    if client is None:
        logger.info("synth[top_searches]: no OpenAI client; skipping")
        return []
    user_prompt = (
        f"Geography: {geo_label}\n"
        "Return the JSON object per the system instructions."
    )
    t0 = time.time()
    text = ''
    try:
        resp = client.responses.create(
            model=AGENT_MODEL,
            tools=[{'type': 'web_search_preview'}],
            input=[
                {'role': 'system', 'content': _TOP_SEARCHES_SYSTEM},
                {'role': 'user',   'content': user_prompt},
            ],
            max_output_tokens=4500,
        )
        text = getattr(resp, 'output_text', '') or ''
        logger.info("synth[top_searches|ws] %s -> %d chars (%.1fs)",
                    geo_label, len(text), time.time() - t0)
    except Exception as e:
        logger.info("synth[top_searches] web_search failed (%s); trying chat",
                    str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _TOP_SEARCHES_SYSTEM},
                    {'role': 'user',   'content': user_prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.3,
                max_tokens=2500,
            )
            text = chat.choices[0].message.content or ''
        except Exception as e2:
            logger.warning("synth[top_searches] BOTH paths failed for %s: %s",
                            geo_label, e2)
            return []
    obj = _parse_agent_response(text)
    if not isinstance(obj, dict):
        return []
    rows = obj.get('rows') or []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        term = str(r.get('term') or '').strip().lower()
        if not term or len(term) < 3 or len(term) > 120:
            continue
        if term in seen:
            continue
        seen.add(term)
        try:
            weight = int(r.get('weight') or 0)
        except Exception:
            weight = 0
        weight = max(1, min(100, weight))
        political = bool(r.get('political'))
        reason = str(r.get('reason') or '').strip()[:120]
        out.append({
            'term': term, 'weight': weight,
            'political': political, 'reason': reason,
        })
    out.sort(key=lambda r: -r['weight'])
    return out[:30]


def synthesize_top_searches(geo_label: str, panel_size: int) -> list[dict]:
    """Return synthetic top_searches rows in the same frontend shape as
    `_shape_top_searches` in blue_iq.py:
        `[{term, count, share, political, synthetic, source_note}]`

    `panel_size` scales `count` so the max-bar UI renders alongside
    real-panel numbers. `share` is `weight / sum(weight)` so the row's
    proportion is preserved.

    Returns [] on any error — caller keeps the (thin) panel list.
    """
    if not geo_label:
        geo_label = 'United States'
    panel_size = max(1, int(panel_size or 1))

    card_key = 'top_searches'
    cached = _cache_get(card_key, geo_label)
    if cached:
        return _scale_top_searches(cached, panel_size)

    rows = _agent_top_searches(geo_label)
    if not rows:
        return []
    _cache_put(card_key, geo_label, rows)
    return _scale_top_searches(rows, panel_size)


def _scale_top_searches(rows: list[dict], panel_size: int) -> list[dict]:
    """Turn `[{term, weight, political, reason}]` into the frontend
    payload shape. `count` is the row's share of `panel_size` (so bars
    are relative to the panel cell) and `share` sums to ~1.0 across the
    row set.
    """
    total_w = sum(int(r.get('weight') or 0) for r in rows) or 1
    out: list[dict] = []
    for r in rows:
        w = int(r.get('weight') or 0)
        if w <= 0:
            continue
        share = round(w / total_w, 4)
        count = int(round(share * panel_size))
        out.append({
            'term':        str(r.get('term') or '').strip(),
            'count':       count,
            'share':       share,
            'political':   bool(r.get('political')),
            'synthetic':   True,
            'source_note': str(r.get('reason') or '').strip(),
        })
    return out
