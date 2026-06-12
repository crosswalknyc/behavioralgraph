"""Creative-playbook discovery for the Blue IQ "Creative playbook" card.

For each policy issue bucket surfaced in the dashboard slice (Healthcare,
Housing & Rent, Foreign Policy, Climate, etc.) use an OpenAI agent with
web search to research CURRENT best-practice digital-marketing
recommendations the user should ship — both:

    1. Where to buy   — concrete inventory + creator partnerships +
                         podcast / streaming-radio / OOH / connected-TV
                         placements that reach the voter cohort
                         searching THIS issue in THIS geography RIGHT
                         NOW.
    2. Creative direction — what the spot / ad / post should actually
                             SAY: framing, tone, specific copy hook,
                             social proof, and what to avoid.

Replaces the prior frontend-only ``BLUE_IQ_ISSUE_PLAYS`` static dict
that returned the same generic copy for every audience regardless of
geography, time period, or what voters are actually doing in the
panel after touching that issue.

Returned shape per issue::

    {
        "bucket":            "Healthcare",
        "where_to_buy":      "AARP newsletter sponsorships + Spotify
                              Daily Drive for 50+ audiences; TikTok
                              creator partnerships (#GLP1 / weight-loss
                              advocates) for under-40s; pre-rolls on
                              The Daily / Pod Save America.",
        "creative_direction":"Lead with the candidate's ONE-line stance
                              on prescription-drug pricing or Medicare
                              expansion. For older audiences, use a
                              testimonial from a constituent. For
                              under-40s, lean on creator UGC about
                              insurance frustrations. AVOID abstract
                              policy jargon like 'single payer'.",
        "rationale":         "Per Pew 2025, 56% of voters 50+ get
                              health news from AARP digital; Spotify
                              Daily Drive over-indexes 1.6x on
                              Medicare-age listeners (eMarketer 2026)."
    }

Cache key: ``blue_iq/playbook/v1/{geo_type}__{geo_value}__{ctx_hash}.json``
The ctx_hash mixes the sorted issue list AND the dominant-follow-up
destination per issue, so the cache invalidates if the underlying
panel behavior shifts (e.g. an issue's voters move from news_dive to
candidate_site).

Fail-open: every external call is wrapped. On any failure (no API
key, agent timeout, malformed JSON, S3 unavailable) we return [] and
the frontend falls back to the prior client-side ``BLUE_IQ_ISSUE_PLAYS``
table. We NEVER raise.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

S3_BUCKET   = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX   = 'blue_iq/playbook/v2/'  # v2: banned-term system-prompt clause (2026-06-12)
CACHE_TTL_S = 24 * 3600
AGENT_TIMEOUT_S = float(os.environ.get('PLAYBOOK_AGENT_TIMEOUT', '90'))
AGENT_MODEL = os.environ.get('PLAYBOOK_AGENT_MODEL', 'gpt-4o')


# ── S3 cache ──────────────────────────────────────────────────────────────

def _s3():
    """Mirror blue_iq._s3() so signing/region config stays in sync."""
    try:
        from app import s3_client                              # type: ignore
        if s3_client is not None:
            return s3_client
    except Exception:
        pass
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', (s or '').lower()).strip('_') or 'none'


def _ctx_hash(issue_ctx: list[dict]) -> str:
    """Stable 8-char hash of the (issue, dominant_destination) pairs so
    the cache invalidates whenever either the bucket set OR the
    panel-derived dominant follow-up per issue shifts.
    """
    norm = sorted(
        ((c.get('bucket') or '').strip().lower(),
         (c.get('dominant_dest') or '').strip().lower())
        for c in (issue_ctx or [])
    )
    joined = '|'.join(f'{b}->{d}' for b, d in norm)
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()[:8]


def _cache_key(geo_type: str, geo_value: str, issue_ctx: list[dict]) -> str:
    return (f"{S3_PREFIX}{_slug(geo_type)}__{_slug(geo_value)}__"
            f"{_ctx_hash(issue_ctx)}.json")


def _cache_get(geo_type: str, geo_value: str,
                issue_ctx: list[dict]) -> Optional[dict]:
    key = _cache_key(geo_type, geo_value, issue_ctx)
    try:
        s3 = _s3()
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        last_mod = resp.get('LastModified')
        if last_mod and (datetime.now(timezone.utc) - last_mod).total_seconds() > CACHE_TTL_S:
            return None
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        msg = str(e)
        if 'NoSuchKey' not in msg and '404' not in msg:
            logger.debug("playbook cache miss for %s|%s: %s",
                          geo_type, geo_value, msg)
        return None


def _cache_put(geo_type: str, geo_value: str, issue_ctx: list[dict],
                payload: dict) -> None:
    key = _cache_key(geo_type, geo_value, issue_ctx)
    try:
        _s3().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, separators=(',', ':')).encode('utf-8'),
            ContentType='application/json',
            CacheControl=f'max-age={CACHE_TTL_S}',
        )
    except Exception as e:
        logger.warning("playbook cache write failed for %s|%s: %s",
                        geo_type, geo_value, e)


# ── OpenAI agent (Responses API + web_search_preview tool) ───────────────

def _openai_client():
    try:
        from blue_iq import _openai_client as _shared        # type: ignore
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
        logger.warning("openai client init failed in playbook_discovery: %s", e)
        return None


_AGENT_SYSTEM = (
    "You are a senior political digital-marketing strategist for a\n"
    "Democratic campaign analytics dashboard. Use web search to surface\n"
    "CURRENT (last 90 days) best-practice ad placement + creative\n"
    "recommendations for each U.S. policy issue listed.\n"
    "\n"
    "For each issue produce ONE concrete, actionable recommendation that\n"
    "ties (a) WHERE to buy media (specific inventory, podcasts, creators,\n"
    "streaming-audio placements, OOH, CTV networks, social platforms), to\n"
    "(b) WHAT the creative should say (framing, tone, copy hook, social\n"
    "proof). The recommendation must reflect:\n"
    "  - The audience persona of voters who care about that issue\n"
    "    (age, gender, race, geography, political lean)\n"
    "  - The dominant follow-up action panelists take after the search\n"
    "    (provided in the prompt as `dominant_dest` — e.g. news_dive,\n"
    "    candidate_site, social_dive, video_dive, abandoned)\n"
    "  - Geography (DMA / state if provided), with media that actually\n"
    "    reaches THAT market (e.g. local news affiliates, regional\n"
    "    sports podcasts, market-specific creator partnerships)\n"
    "  - Recent shifts in voter behavior (post-election analyses, 2026\n"
    "    midterm prep, tariff coverage, etc.) where relevant\n"
    "\n"
    "EACH issue's recommendation must be DISTINCT. Do NOT return the same\n"
    "generic 'NYT/Fox political news inventory + issue-anchored explainer\n"
    "creative' for every issue — that is the EXACT failure mode this\n"
    "agent exists to replace.\n"
    "\n"
    "GROUND your recommendations in plausible public research:\n"
    "  - Pew Research / Knight Foundation voter-media studies\n"
    "  - eMarketer / Statista digital-ad inventory reports\n"
    "  - Edison Research podcast / streaming audio listenership\n"
    "  - GroupM / WARC creator marketing benchmarks\n"
    "  - Recent campaign post-mortems (NYT Politics, POLITICO,\n"
    "    The Atlantic, NPR Politics) discussing what actually moved\n"
    "    persuadable voters\n"
    "\n"
    "STYLE rules for the output strings (these render as small body-text\n"
    "blocks in the dashboard card so concision matters):\n"
    "  - `where_to_buy` ≤ 320 chars; lead with the highest-ROI placement,\n"
    "    then 1-2 secondary. Name specific inventory (e.g. 'The Daily',\n"
    "    'Pod Save America', 'TikTok #FirstTimeHomebuyer creators',\n"
    "    'Spotify Daily Drive') instead of generic categories.\n"
    "  - `creative_direction` ≤ 360 chars; lead with the headline /\n"
    "    creative hook in plain English, then the proof point, then one\n"
    "    explicit AVOID. Quote what voters are likely searching when\n"
    "    helpful.\n"
    "  - `rationale` ≤ 220 chars; one short sentence citing the research\n"
    "    basis (Pew %, Edison Research, etc.).\n"
    "  - No em-dashes. No emojis. No 'one-pager', 'flywheel', 'GTM',\n"
    "    'omnichannel' or other agency cliches.\n"
    "  - American English. Real, specific publishers, podcasts, and\n"
    "    creator handles where you can. Avoid 'social media in general'.\n"
    "\n"
    "BANNED TERMS — do not mention, allude to, or paraphrase any of\n"
    "these in any field (where_to_buy, creative_direction, rationale):\n"
    "  - government shutdown / federal government shutdown / gov shutdown\n"
    "If a recommendation would naturally invoke that phrase, frame it\n"
    "via the underlying policy (budget impasse, appropriations, agency\n"
    "funding) WITHOUT using the banned phrase.\n"
    "\n"
    "OUTPUT FORMAT: return ONLY a JSON object matching this schema, no\n"
    "markdown fences, no commentary, first char `{`, last char `}`:\n"
    "\n"
    "{\n"
    '  "plays": [\n'
    "    {\n"
    '      "bucket":             "Healthcare",\n'
    '      "where_to_buy":       "<placement recommendation, max 320 chars>",\n'
    '      "creative_direction": "<creative hook + proof + avoid, max 360 chars>",\n'
    '      "rationale":          "<one short sentence with research basis>"\n'
    "    }\n"
    "  ]\n"
    "}"
)


def _build_agent_prompt(geo_type: str, geo_value: str,
                          issue_ctx: list[dict]) -> str:
    issue_list = '\n'.join(
        f"  - {c.get('bucket')} (dominant_dest={c.get('dominant_dest') or 'unknown'}, "
        f"share={c.get('dom_share', 0):.0%})"
        for c in issue_ctx
    )
    geo_blurb = (
        "Audience: U.S. voters generally (no geographic refinement)."
        if (geo_type == 'National' or not geo_value)
        else (
            f"Audience: U.S. voters in {geo_type} = {geo_value}. Tune the "
            "placement recommendations to that market's actual media "
            "environment (local affiliates, regional podcasts, market-"
            "specific creator partnerships, OOH zones, etc.) where it "
            "matters. Tune the creative tone to the political makeup of "
            "that market."
        )
    )
    return (
        f"{geo_blurb}\n"
        "\n"
        "Produce one placement + creative recommendation for EACH issue\n"
        "below, in the SAME ORDER. The bucket name must match exactly.\n"
        "Each bucket includes the dominant follow-up action panelists\n"
        "are taking after the search; let that shape the recommendation\n"
        "(e.g. if voters of an issue mostly news_dive, you can buy news\n"
        "inventory adjacent to that flow; if they candidate_site, lean\n"
        "into direct-response on the candidate's policy plank).\n"
        "\n"
        f"{issue_list}\n"
        "\n"
        "Return one entry per bucket. Each must be DISTINCT — do not\n"
        "repeat the same placement or creative across buckets."
    )


# ── Validation ────────────────────────────────────────────────────────────

def _validate_play(p: dict) -> Optional[dict]:
    if not isinstance(p, dict):
        return None
    bucket = (p.get('bucket') or '').strip()
    where = (p.get('where_to_buy') or '').strip()
    creative = (p.get('creative_direction') or '').strip()
    if not bucket or not where or not creative:
        return None
    # Strip leftover em-dashes the agent occasionally slips in.
    where = where.replace('\u2014', ',').replace('\u2013', ',')
    creative = creative.replace('\u2014', ',').replace('\u2013', ',')
    return {
        'bucket':             bucket[:80],
        'where_to_buy':       where[:400],
        'creative_direction': creative[:440],
        'rationale':          (p.get('rationale') or '').strip()[:240],
    }


# ── Truncation-tolerant JSON parser ──────────────────────────────────────

def _parse_agent_response(text: str) -> Optional[dict]:
    if not text:
        return None
    raw = text.strip()
    raw = re.sub(r'^```(?:json|JSON)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)
    # Strip web_search_preview citation glyphs (e.g. 【1†src】).
    raw = re.sub(r'\u3010\d+[\u2020:\dA-Za-z\-_\.\s\(\)/]*?\u3011', '', raw)
    raw = re.sub(r'\u3010cite[^\u3011]*\u3011', '', raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find('{')
    if start < 0:
        return None
    body = raw[start:]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    arr_start = body.find('"plays"')
    if arr_start < 0:
        return None
    bracket_at = body.find('[', arr_start)
    if bracket_at < 0:
        return None
    depth = 0
    last_clean = -1
    in_str = False
    esc = False
    for i in range(bracket_at, len(body)):
        c = body[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                last_clean = i
        elif c == ']' and depth == 0:
            try:
                return json.loads(body[:i + 1] + '}')
            except Exception:
                pass
    if last_clean < 0:
        return None
    salvaged = body[:last_clean + 1] + ']}'
    try:
        return json.loads(salvaged)
    except Exception:
        return None


def _call_agent(geo_type: str, geo_value: str,
                 issue_ctx: list[dict]) -> list[dict]:
    """Issue the agent call. Returns [] on any failure."""
    client = _openai_client()
    if client is None:
        logger.info("playbook agent: no OpenAI client; skipping")
        return []
    prompt = _build_agent_prompt(geo_type, geo_value, issue_ctx)
    t0 = time.time()
    text = ''
    try:
        resp = client.responses.create(
            model=AGENT_MODEL,
            tools=[{'type': 'web_search_preview'}],
            input=[
                {'role': 'system', 'content': _AGENT_SYSTEM},
                {'role': 'user',   'content': prompt},
            ],
            max_output_tokens=16000,
        )
        text = getattr(resp, 'output_text', '') or ''
        logger.info("playbook agent[ws] %s|%s (%d issues) -> %d chars (%.1fs)",
                     geo_type, geo_value, len(issue_ctx), len(text),
                     time.time() - t0)
    except Exception as e:
        logger.info("playbook agent web_search failed (%s); trying chat",
                     str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _AGENT_SYSTEM},
                    {'role': 'user',   'content': prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.4,
                max_tokens=8000,
            )
            text = chat.choices[0].message.content or ''
            logger.info("playbook agent[chat] %s|%s -> %d chars (%.1fs)",
                         geo_type, geo_value, len(text), time.time() - t0)
        except Exception as e2:
            logger.warning("playbook agent BOTH paths failed for %s|%s: %s",
                            geo_type, geo_value, e2)
            return []
    if not text:
        return []
    obj = _parse_agent_response(text)
    if obj is None:
        logger.warning("playbook agent: unparseable response for %s|%s "
                        "(head=%r tail=%r)",
                        geo_type, geo_value, text[:120], text[-120:])
        return []
    raw_list = obj.get('plays') if isinstance(obj, dict) else None
    if not isinstance(raw_list, list):
        logger.warning("playbook agent: missing 'plays' array for %s|%s",
                        geo_type, geo_value)
        return []
    asked = {(c.get('bucket') or '').strip().lower() for c in issue_ctx}
    cleaned: list[dict] = []
    seen: set[str] = set()
    for p in raw_list[:30]:
        row = _validate_play(p)
        if not row:
            continue
        key = row['bucket'].lower()
        # Only keep plays for issues we actually asked about (drops
        # hallucinated extra buckets the agent invents).
        if key not in asked:
            continue
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    return cleaned


# ── Public entrypoint ─────────────────────────────────────────────────────

_INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()


def discover_creative_playbook(geo_type: str, geo_value: str,
                                 issue_ctx: list[dict],
                                 force_refresh: bool = False) -> list[dict]:
    """Return per-issue researched playbook recommendations.

    issue_ctx = ordered list of
        {'bucket': str, 'dominant_dest': str|None, 'dom_share': float}
    where bucket is the issue name, dominant_dest is the panel-derived
    most-common follow-up action after voters search that issue
    (news_dive, candidate_site, social_dive, video_dive, abandoned, ...),
    and dom_share is that destination's share of the issue cohort.

    Strategy:
      1. Cache hit (< 24h, matching issue+dest hash) -> return cached.
      2. Else -> call agent, cache, return.
      3. On agent failure -> stale-cache OR empty list (caller falls
         back to the static client-side BLUE_IQ_ISSUE_PLAYS).
    Never raises.
    """
    geo_type = (geo_type or 'National').strip()
    geo_value = (geo_value or '').strip()
    issue_ctx = [c for c in (issue_ctx or [])
                  if isinstance(c, dict) and (c.get('bucket') or '').strip()]
    if not issue_ctx:
        return []
    # Hard cap so a runaway list of buckets doesn't blow the prompt.
    issue_ctx = issue_ctx[:12]
    cache_id = f"{geo_type}|{geo_value}|{_ctx_hash(issue_ctx)}"

    if not force_refresh:
        cached = _cache_get(geo_type, geo_value, issue_ctx)
        if cached and isinstance(cached.get('plays'), list):
            return cached['plays']

    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.get(cache_id)
        if ev is not None:
            wait_for = ev
            owner = False
        else:
            wait_for = threading.Event()
            _INFLIGHT[cache_id] = wait_for
            owner = True

    if not owner:
        wait_for.wait(timeout=AGENT_TIMEOUT_S)
        cached = _cache_get(geo_type, geo_value, issue_ctx)
        return (cached or {}).get('plays', []) if cached else []

    try:
        plays = _call_agent(geo_type, geo_value, issue_ctx)
        if plays:
            payload = {
                'geo_type':      geo_type,
                'geo_value':     geo_value,
                'issue_ctx':     issue_ctx,
                'plays':         plays,
                'discovered_at': datetime.now(timezone.utc).isoformat(),
                'count':         len(plays),
            }
            _cache_put(geo_type, geo_value, issue_ctx, payload)
            return plays
        # Agent failed: try stale cache (any age) as a graceful fallback.
        try:
            resp = _s3().get_object(
                Bucket=S3_BUCKET,
                Key=_cache_key(geo_type, geo_value, issue_ctx),
            )
            stale = json.loads(resp['Body'].read().decode('utf-8'))
            return stale.get('plays') or []
        except Exception:
            return []
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(cache_id, None)
        wait_for.set()
