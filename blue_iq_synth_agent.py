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
