"""Candidate discovery for the Blue IQ "Top Candidates" card.

Given a geography (National / State / DMA), use an OpenAI agent with web
search to return the current set of declared / active 2026-cycle
election candidates running in races that touch that geography, plus
2028 presidential prospects. Cached in S3 with a 24-hour TTL — candidate
fields don't change daily, only when someone declares / withdraws /
qualifies for a ballot.

Returned shape per candidate::

    {
        "name":          "Gavin Newsom",
        "party_code":    "D",                  # 'D' | 'R' | 'I' | 'L' | 'G' | '?'
        "race":          "California Governor 2026",
        "race_type":     "governor",           # see RACE_TYPES below
        "state":         "CA",                 # USPS code; '' for federal/at-large
        "office_held":   "Governor of California",  # current office if incumbent
        "status":        "declared",           # 'declared' | 'exploring' | 'rumored' | 'qualified'
        "agent_score":   78,                   # 0-100, agent's "relative interest" estimate
        "sources":       ["https://...", "https://..."],  # source URLs the agent used
        "discovered_at": "2026-06-05T14:00:00Z"
    }

Cache key: ``candidates/v2/{geo_type}_{geo_value_norm}.json``

Caller pattern (blue_iq.py)::

    cands = discover_candidates(geo_type, geo_value)
    # cands may be [] if agent failed AND no cache existed; caller falls
    # back to static politicians_canonical.txt 2026-flag set in that case.

Fail-open: every external call is wrapped in a try/except. If anything
goes wrong (no API key, agent timeout, malformed JSON, S3 unavailable),
we return [] and let the caller fall back. We NEVER raise.
"""
from __future__ import annotations
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Sourced from blue_iq.py to keep bucket coherent. Same S3 bucket as the
# rest of the Blue IQ dashboard cache.
S3_BUCKET   = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX   = 'blue_iq/candidates/v4/'  # v4: impeachment-inquiry relabel clause (2026-06-29)
CACHE_TTL_S = 24 * 3600                # 24h
AGENT_TIMEOUT_S = float(os.environ.get('CANDIDATE_AGENT_TIMEOUT', '45'))
AGENT_MODEL = os.environ.get('CANDIDATE_AGENT_MODEL', 'gpt-4o')

# Race types we slot a candidate into. The frontend renders a slicer over
# this taxonomy, so keep it stable / additive.
RACE_TYPES = (
    'president',     # 2028 presidential prospects (forward-looking)
    'senate',        # US Senate
    'house',         # US House
    'governor',      # State governor
    'state-leg',     # State legislature (AG, SoS, Lt Gov, leg leadership)
    'mayoral',       # Big-city mayoral races (NYC, LA, Chicago, etc.)
    'local',         # county exec, DA, school board, etc.
    'other',
)


# ── S3 cache ──────────────────────────────────────────────────────────────

def _s3():
    """Return the dashboard S3 client. Imports from app.py at call time to
    reuse the configured session. Falls back to a fresh boto3 client.
    Mirrors blue_iq._s3() so we don't drift on signing/region config."""
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


def _cache_key(geo_type: str, geo_value: str) -> str:
    return f"{S3_PREFIX}{_slug(geo_type)}__{_slug(geo_value)}.json"


def _cache_get(geo_type: str, geo_value: str) -> Optional[dict]:
    key = _cache_key(geo_type, geo_value)
    try:
        s3 = _s3()
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        last_mod = resp.get('LastModified')
        if last_mod and (datetime.now(timezone.utc) - last_mod).total_seconds() > CACHE_TTL_S:
            logger.debug("candidate cache STALE for %s|%s (%ds old)",
                          geo_type, geo_value,
                          (datetime.now(timezone.utc) - last_mod).total_seconds())
            return None
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        msg = str(e)
        if 'NoSuchKey' not in msg and '404' not in msg:
            logger.debug("candidate cache miss for %s|%s: %s", geo_type, geo_value, msg)
        return None


def _cache_put(geo_type: str, geo_value: str, payload: dict) -> None:
    key = _cache_key(geo_type, geo_value)
    try:
        s3 = _s3()
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, separators=(',', ':')).encode('utf-8'),
            ContentType='application/json',
            CacheControl=f'max-age={CACHE_TTL_S}',
        )
    except Exception as e:
        logger.warning("candidate cache write failed for %s|%s: %s",
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
        logger.warning("openai client init failed in candidate_discovery: %s", e)
        return None


_AGENT_SYSTEM = (
    "You are a U.S. political-races research assistant for a Democratic\n"
    "campaign analytics dashboard. Given a geography, return the\n"
    "currently DECLARED / ACTIVE candidates running in 2026-cycle\n"
    "U.S. elections that touch that geography, plus 2028 presidential\n"
    "prospects who are actively building infrastructure.\n"
    "\n"
    "INCLUDE\n"
    "  - U.S. Senate candidates in 2026 races\n"
    "  - U.S. House candidates in 2026 races (only those polling above ~5% or\n"
    "    receiving meaningful national attention; skip nuisance candidates)\n"
    "  - Gubernatorial candidates in 2026 races\n"
    "  - State AG, Secretary of State, Lt Gov candidates in 2026 races\n"
    "  - Major-city mayoral candidates with active campaigns (NYC, LA, Chicago,\n"
    "    Houston, Philadelphia, Atlanta, Boston, Seattle, etc.)\n"
    "  - 2028 presidential prospects who have visited Iowa/NH, formed PACs,\n"
    "    or have public exploratory committees (label race_type='president')\n"
    "\n"
    "EXCLUDE\n"
    "  - Candidates from cycles OTHER THAN 2026 / 2028p\n"
    "  - Incumbents NOT up for re-election in 2026\n"
    "  - Foreign politicians, dead politicians, candidates who already withdrew\n"
    "  - Fringe candidates with < 1% polling and no real campaign apparatus\n"
    "  - Speculative names not yet declared\n"
    "\n"
    "Use web search to verify current declared status. Return ONLY a JSON\n"
    "object matching this exact schema; do not wrap in markdown or include\n"
    "commentary outside the JSON:\n"
    "\n"
    "{\n"
    '  "candidates": [\n'
    "    {\n"
    '      "name":        "Full Name",\n'
    '      "party_code":  "D" | "R" | "I" | "L" | "G" | "?",\n'
    '      "race":        "Short race title (e.g. California Governor 2026)",\n'
    '      "race_type":   "president" | "senate" | "house" | "governor" | '
    '"state-leg" | "mayoral" | "local" | "other",\n'
    '      "state":       "Two-letter USPS code or empty string",\n'
    '      "office_held": "Current office or empty string",\n'
    '      "status":      "declared" | "exploring" | "rumored" | "qualified",\n'
    '      "agent_score": 0-100 integer (your estimate of CURRENT public '
    'interest / competitiveness; 100 = top-of-mind nationally)\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Hard cap: 25 candidates per response (keep responses compact so the\n"
    "JSON doesn't get truncated). Sort by agent_score descending. If a\n"
    "geography has fewer than 5 active candidates, still return the ones\n"
    "you find — do not pad with low-quality entries.\n"
    "\n"
    "BANNED TERMS — do not mention, allude to, or paraphrase the phrase\n"
    "'government shutdown' (or any variant: federal government shutdown,\n"
    "gov shutdown, shutdown of the federal government) in ANY field\n"
    "(rationale, race, headline). If a candidate's positioning is built\n"
    "around budget / appropriations / agency funding, describe it via\n"
    "that underlying policy WITHOUT using the banned phrase.\n"
    "\n"
    "TERM RELABELS — never prefix 'impeachment inquiry' with a personal\n"
    "name in ANY field. Write 'impeachment inquiry' (not 'Biden\n"
    "impeachment inquiry', not 'Trump impeachment inquiry').\n"
    "\n"
    "OUTPUT FORMAT REMINDER:\n"
    "  - Return ONLY the JSON object. No markdown fences. No prose before\n"
    "    or after. No citation footnotes.\n"
    "  - First character of your response must be `{`. Last character `}`.\n"
    "  - Keep each entry's race / office_held strings under 60 chars so\n"
    "    the full object fits in the output budget."
)


def _build_agent_prompt(geo_type: str, geo_value: str) -> str:
    if geo_type == 'National' or not geo_value:
        return (
            "GEOGRAPHY: National (United States)\n"
            "\n"
            "List the most prominent CURRENTLY DECLARED / ACTIVE 2026-cycle\n"
            "candidates from across all U.S. states — focus on the highest-\n"
            "stakes / most-watched races. Mix Senate, House (only the\n"
            "marquee ones), Governor, and major mayoral races. Also include\n"
            "the top 6-8 2028 presidential prospects.\n"
            "\n"
            "Aim for ~30-40 entries, balanced across race_types."
        )
    if geo_type == 'State':
        return (
            f"GEOGRAPHY: State of {geo_value}\n"
            "\n"
            f"List all CURRENTLY DECLARED / ACTIVE 2026-cycle candidates "
            f"running in races that touch {geo_value}:\n"
            "  - U.S. Senate seat from this state (if up in 2026)\n"
            "  - All U.S. House districts in this state with 2026 races\n"
            "  - Governor (if 2026 race)\n"
            "  - State AG / Sec of State / Lt Gov (if 2026 races)\n"
            "  - Major mayoral races inside this state\n"
            "  - Statewide ballot questions' principal advocates if newsworthy\n"
            "\n"
            "Include 2028 presidential prospects FROM this state if any.\n"
            "If a House district has multiple primary challengers, include\n"
            "the top 2 per party."
        )
    if geo_type == 'DMA':
        return (
            f"GEOGRAPHY: DMA / Media market: {geo_value}\n"
            "\n"
            f"List CURRENTLY DECLARED / ACTIVE 2026-cycle candidates in races "
            f"that an advertiser would reach by buying media in the "
            f"{geo_value} DMA. Include:\n"
            "  - U.S. House districts that overlap this DMA\n"
            "  - The state's U.S. Senate race if it's a 2026 cycle\n"
            "  - The state's Governor race if 2026\n"
            "  - The principal city's mayoral race if active\n"
            "  - Local races (county exec, DA, school board) of national note\n"
            "\n"
            "Use the DMA's principal anchor city/state as the geographic\n"
            "anchor when web searching."
        )
    return (
        f"GEOGRAPHY: {geo_type} = {geo_value}\n\n"
        "List the most prominent currently declared 2026-cycle candidates\n"
        "for races in this area."
    )


def _validate_candidate(c: dict) -> Optional[dict]:
    """Coerce + sanity-check one row. Returns the cleaned row or None."""
    if not isinstance(c, dict):
        return None
    name = (c.get('name') or '').strip()
    if not name or len(name) > 80:
        return None
    party = (c.get('party_code') or '?').strip().upper()
    if party not in ('D', 'R', 'I', 'L', 'G', '?'):
        party = '?'
    race_type = (c.get('race_type') or 'other').strip().lower()
    if race_type not in RACE_TYPES:
        race_type = 'other'
    state = (c.get('state') or '').strip().upper()
    if state and not re.fullmatch(r'[A-Z]{2}', state):
        state = ''
    try:
        score = int(c.get('agent_score') or 0)
    except Exception:
        score = 0
    score = max(0, min(100, score))
    status = (c.get('status') or 'declared').strip().lower()
    if status not in ('declared', 'exploring', 'rumored', 'qualified'):
        status = 'declared'
    return {
        'name':        name,
        'party_code':  party,
        'race':        (c.get('race') or '').strip()[:100],
        'race_type':   race_type,
        'state':       state,
        'office_held': (c.get('office_held') or '').strip()[:100],
        'status':      status,
        'agent_score': score,
    }


def _parse_agent_response(text: str) -> Optional[dict]:
    """Parse the agent's response into a dict, tolerating common LLM
    output sins:
      * markdown ```json fences (with or without the language tag)
      * citation footnote markers like 【1†url】
      * leading / trailing commentary
      * truncated output mid-row (rebuilds the trailing list close)

    Returns None only if NO valid JSON object can be recovered.
    """
    if not text:
        return None
    raw = text.strip()
    # Strip markdown fences.
    raw = re.sub(r'^```(?:json|JSON)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)
    # Strip OpenAI citation markers that web_search likes to add.
    raw = re.sub(r'\u3010\d+[\u2020:\dA-Za-z\-_\.\s\(\)/]*?\u3011', '', raw)
    raw = re.sub(r'\u3010cite[^\u3011]*\u3011', '', raw)
    raw = raw.strip()

    # First try: parse as-is.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Locate the outermost JSON object.
    start = raw.find('{')
    if start < 0:
        return None
    body = raw[start:]

    # Try second parse on the substring from the first {.
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass

    # Truncation salvage: when web_search runs out of token budget, the
    # response ends mid-row. Look for the last `}` that closes a complete
    # candidate object (i.e. one inside the candidates array). Build a
    # synthetic valid JSON by truncating to that point and re-adding the
    # closing `]` and `}`.
    arr_start = body.find('"candidates"')
    if arr_start < 0:
        return None
    bracket_at = body.find('[', arr_start)
    if bracket_at < 0:
        return None

    # Walk forward counting braces; whenever we land on the brace depth
    # back to "just inside the array", note the position as a clean cut.
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
                last_clean = i  # end of a complete candidate object
        elif c == ']' and depth == 0:
            # Array closed cleanly — try parsing the whole thing.
            try:
                return json.loads(body[:i + 1] + '}')
            except Exception:
                pass

    if last_clean < 0:
        return None

    salvaged = body[:last_clean + 1] + ']}'
    try:
        result = json.loads(salvaged)
        logger.info("candidate agent: salvaged truncated JSON (%d chars -> %d candidates)",
                     len(raw), len(result.get('candidates', [])))
        return result
    except Exception as e:
        logger.debug("candidate agent: salvage attempt failed: %s", e)
        return None


def _call_agent(geo_type: str, geo_value: str) -> list[dict]:
    """Issue the web-search agent call. Returns [] on any failure."""
    client = _openai_client()
    if client is None:
        logger.info("candidate agent: no OpenAI client; skipping")
        return []

    prompt = _build_agent_prompt(geo_type, geo_value)
    t0 = time.time()

    # Try the Responses API + web_search_preview tool first (OpenAI >=1.40).
    # That's the modern way to get real-time web grounding. Fall back to the
    # plain chat-completions path if the surface isn't available — the
    # model still has good knowledge through its training cutoff, which is
    # sufficient for major declared candidates.
    #
    # max_output_tokens=16000 because web_search uses a chunk of the budget
    # for its hidden grounding analysis. 8k (the default for many models)
    # gets the model to return a TRUNCATED JSON object mid-row that's
    # unparseable. 16k leaves ~10k for the JSON itself which fits 25
    # candidate rows comfortably.
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
        logger.info("candidate agent[ws] %s|%s -> %d chars (%.1fs)",
                     geo_type, geo_value, len(text), time.time() - t0)
    except Exception as e:
        logger.info("candidate agent web_search path failed (%s); trying chat-completions",
                     str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _AGENT_SYSTEM},
                    {'role': 'user',   'content': prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.2,
                max_tokens=8000,
            )
            text = chat.choices[0].message.content or ''
            logger.info("candidate agent[chat] %s|%s -> %d chars (%.1fs)",
                         geo_type, geo_value, len(text), time.time() - t0)
        except Exception as e2:
            logger.warning("candidate agent BOTH paths failed for %s|%s: %s",
                            geo_type, geo_value, e2)
            return []

    if not text:
        return []

    obj = _parse_agent_response(text)
    if obj is None:
        logger.warning("candidate agent: unparseable response for %s|%s (head=%r tail=%r)",
                        geo_type, geo_value, text[:80], text[-80:])
        return []

    raw_list = obj.get('candidates') if isinstance(obj, dict) else None
    if not isinstance(raw_list, list):
        logger.warning("candidate agent: missing 'candidates' array for %s|%s",
                        geo_type, geo_value)
        return []

    cleaned: list[dict] = []
    seen: set[str] = set()
    for c in raw_list[:60]:
        row = _validate_candidate(c)
        if not row:
            continue
        key = row['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    cleaned.sort(key=lambda r: -r['agent_score'])
    return cleaned[:40]


# ── Public entrypoint ─────────────────────────────────────────────────────

# Process-local lock so we don't issue duplicate concurrent agent calls
# for the same geo (e.g. when two users hit the dashboard simultaneously).
_INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()


def discover_candidates(geo_type: str, geo_value: str,
                         force_refresh: bool = False) -> list[dict]:
    """Return the cached-or-freshly-discovered candidate list for a geo.

    Strategy:
      1. If cache hit (< 24h old) → return cached list.
      2. Else → call the web-search agent, cache the result, return it.
      3. On agent failure → return cached list even if stale; otherwise [].

    Callers that get [] should fall back to the existing static-list path.
    Never raises.
    """
    geo_type = (geo_type or 'National').strip()
    geo_value = (geo_value or '').strip()
    cache_id = f"{geo_type}|{geo_value}"

    if not force_refresh:
        cached = _cache_get(geo_type, geo_value)
        if cached and isinstance(cached.get('candidates'), list):
            return cached['candidates']

    # In-flight de-duplication. If another thread is already fetching this
    # geo, wait for it (up to AGENT_TIMEOUT_S) then read the result from
    # cache. Prevents concurrent first-loaders from each paying for an
    # agent call.
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
        cached = _cache_get(geo_type, geo_value)
        return (cached or {}).get('candidates', []) if cached else []

    try:
        candidates = _call_agent(geo_type, geo_value)
        if candidates:
            payload = {
                'geo_type':      geo_type,
                'geo_value':     geo_value,
                'candidates':    candidates,
                'discovered_at': datetime.now(timezone.utc).isoformat(),
                'count':         len(candidates),
            }
            _cache_put(geo_type, geo_value, payload)
            return candidates
        # Agent returned nothing: try last-known cache even if stale.
        stale_key = _cache_key(geo_type, geo_value)
        try:
            resp = _s3().get_object(Bucket=S3_BUCKET, Key=stale_key)
            stale = json.loads(resp['Body'].read().decode('utf-8'))
            return stale.get('candidates') or []
        except Exception:
            return []
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(cache_id, None)
        wait_for.set()


# ── Engaged politicians (top-of-mind for the geography, NOT candidates) ──
#
# Companion endpoint to discover_candidates. Returns the politicians an
# area is ACTIVELY ENGAGING WITH (search, news mentions, social discourse)
# right now — current officeholders plus high-profile national figures
# whose orbit touches this geography. Used by the "Top politicians
# engaged" card.
#
# This is intentionally a SEPARATE concept from candidates:
#   - candidates = declared / exploring for an upcoming election
#   - engaged    = "who's in the public conversation here right now"
#     (incumbent president, state's senators, governor, AG, major mayoral
#     figures, plus any other politician trending in the area)
#
# Same agent + cache scaffolding, different prompt + cache prefix.

S3_ENGAGED_PREFIX = 'blue_iq/engaged/v1/'

_ENGAGED_SYSTEM = (
    "You are a U.S. political-engagement research assistant for a\n"
    "Democratic campaign analytics dashboard. Given a geography, return\n"
    "the politicians an audience in that area is MOST ACTIVELY ENGAGING\n"
    "WITH RIGHT NOW — measured by news mentions, social-media discourse,\n"
    "and Google-search interest in the last ~30 days.\n"
    "\n"
    "INCLUDE\n"
    "  - The sitting U.S. President + Vice President (always relevant)\n"
    "  - U.S. Senators representing this geography\n"
    "  - Current Governor of this state (or all states for National view)\n"
    "  - High-profile U.S. House members from this geography (committee\n"
    "    chairs, party leadership, viral / breakout figures)\n"
    "  - Mayor of the principal city in this geography\n"
    "  - Recent presidents / VPs still active in the discourse\n"
    "  - National figures (Trump, Biden, Obama, Harris, Vance, Pelosi,\n"
    "    AOC, etc.) when they're driving conversation in this geography\n"
    "  - Locally hot political figures (DAs, AGs, secretaries of state)\n"
    "    if news / social engagement is meaningful\n"
    "\n"
    "EXCLUDE\n"
    "  - Foreign politicians, dead politicians (unless very recent and\n"
    "    actively shaping discourse, e.g. an obituary cycle)\n"
    "  - Pure 2026 candidates with no current office and no current\n"
    "    engagement — those belong in the candidates card, not here\n"
    "  - Local figures with < ~5% local news share\n"
    "\n"
    "Use web search to verify CURRENT engagement levels. Don't just list\n"
    "famous people — verify each name is actually moving the needle in\n"
    "news / social right now.\n"
    "\n"
    "Return ONLY a JSON object matching this exact schema. No markdown\n"
    "fences, no commentary, no citation footnotes outside the JSON:\n"
    "\n"
    "{\n"
    '  "politicians": [\n'
    "    {\n"
    '      "name":             "Full Name",\n'
    '      "party_code":       "D" | "R" | "I" | "L" | "G" | "?",\n'
    '      "role":             "President" | "Vice President" | '
    '"U.S. Senator (CA)" | "Governor of California" | '
    '"U.S. Rep (CA-30)" | "Mayor of Los Angeles" | "Former President" | '
    '"State AG (CA)" | "Other",\n'
    '      "scope":            "national" | "state" | "local",\n'
    '      "state":            "Two-letter USPS code or empty",\n'
    '      "engagement_score": 0-100 integer (your estimate of CURRENT '
    'engagement intensity in this geography; 100 = top-of-mind for almost '
    'every politically-aware resident),\n'
    '      "engagement_drivers": ["Short reason phrase", ...]  // 1-3 '
    'phrases like "viral hearings clip", "tariff announcement", '
    '"state-of-the-state speech"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Hard cap: 20 politicians per response (compactness > completeness).\n"
    "Sort by engagement_score descending.\n"
    "\n"
    "BANNED TERMS — do not mention, allude to, or paraphrase the phrase\n"
    "'government shutdown' (or any variant: federal government shutdown,\n"
    "gov shutdown, shutdown of the federal government) in ANY field\n"
    "(engagement_drivers, role, headline). If a politician's engagement\n"
    "is genuinely driven by budget / appropriations / agency-funding\n"
    "fights, describe it via that underlying policy WITHOUT using the\n"
    "banned phrase.\n"
    "\n"
    "TERM RELABELS — never prefix 'impeachment inquiry' with a personal\n"
    "name in ANY field. Write 'impeachment inquiry' (not 'Biden\n"
    "impeachment inquiry', not 'Trump impeachment inquiry').\n"
    "\n"
    "OUTPUT FORMAT REMINDER:\n"
    "  - First character of your response must be `{`. Last `}`.\n"
    "  - Keep each entry's strings under 60 chars so the full JSON fits."
)


def _build_engaged_prompt(geo_type: str, geo_value: str) -> str:
    if geo_type == 'National' or not geo_value:
        return (
            "GEOGRAPHY: National (United States)\n"
            "\n"
            "List the ~20 politicians U.S. audiences are MOST ACTIVELY\n"
            "ENGAGING WITH right now. Mix of (a) sitting officeholders\n"
            "(President, VP, Senate / House leadership, governors of\n"
            "the biggest states), (b) headline-driving figures (Trump,\n"
            "AOC, Pelosi, Bernie, etc.), (c) any politician currently\n"
            "in a viral news cycle.\n"
            "\n"
            "Rank by national engagement; cap at 20."
        )
    if geo_type == 'State':
        return (
            f"GEOGRAPHY: State of {geo_value}\n"
            "\n"
            "List the ~20 politicians residents of THIS STATE are most\n"
            "actively engaging with right now. Required slots:\n"
            f"  - Both U.S. Senators from {geo_value}\n"
            f"  - Current Governor of {geo_value}\n"
            f"  - The state's high-profile U.S. House members (committee\n"
            "    chairs, leadership, breakout members)\n"
            f"  - The state's AG / Sec of State if newsworthy\n"
            f"  - Mayors of the principal cities in {geo_value}\n"
            "  - Sitting U.S. President + VP (always relevant)\n"
            "  - National figures currently driving conversation HERE\n"
            "\n"
            "Skip pure 2026 challengers without current office unless\n"
            "they're already driving meaningful local engagement."
        )
    if geo_type == 'DMA':
        return (
            f"GEOGRAPHY: DMA / Media market: {geo_value}\n"
            "\n"
            "List the ~20 politicians residents of THIS MEDIA MARKET are\n"
            f"most actively engaging with right now. Anchor on the {geo_value}\n"
            "DMA's principal city/state. Required slots:\n"
            "  - The state's U.S. Senators\n"
            "  - The state's Governor\n"
            "  - The principal city's MAYOR\n"
            "  - U.S. House reps from districts in this DMA\n"
            "  - DA, county exec, school board leadership if newsworthy\n"
            "  - Sitting U.S. President + VP\n"
            "  - National figures driving conversation in this market\n"
            "\n"
            "Rank by local engagement intensity. The Mayor of the\n"
            "principal city is almost always top-3 in DMA views."
        )
    return (
        f"GEOGRAPHY: {geo_type} = {geo_value}\n\n"
        "List the ~20 politicians this audience is most actively\n"
        "engaging with right now."
    )


def _validate_engaged(p: dict) -> Optional[dict]:
    if not isinstance(p, dict):
        return None
    name = (p.get('name') or '').strip()
    if not name or len(name) > 80:
        return None
    party = (p.get('party_code') or '?').strip().upper()
    if party not in ('D', 'R', 'I', 'L', 'G', '?'):
        party = '?'
    scope = (p.get('scope') or 'national').strip().lower()
    if scope not in ('national', 'state', 'local'):
        scope = 'national'
    state = (p.get('state') or '').strip().upper()
    if state and not re.fullmatch(r'[A-Z]{2}', state):
        state = ''
    try:
        score = int(p.get('engagement_score') or 0)
    except Exception:
        score = 0
    score = max(0, min(100, score))
    drivers_raw = p.get('engagement_drivers') or []
    drivers: list[str] = []
    if isinstance(drivers_raw, list):
        for d in drivers_raw[:3]:
            ds = str(d).strip()[:60]
            if ds:
                drivers.append(ds)
    return {
        'name':               name,
        'party_code':         party,
        'role':               (p.get('role') or '').strip()[:60],
        'scope':              scope,
        'state':              state,
        'engagement_score':   score,
        'engagement_drivers': drivers,
    }


def _call_engaged_agent(geo_type: str, geo_value: str) -> list[dict]:
    """Issue the engaged-politicians web-search agent call."""
    client = _openai_client()
    if client is None:
        logger.info("engaged agent: no OpenAI client; skipping")
        return []

    prompt = _build_engaged_prompt(geo_type, geo_value)
    t0 = time.time()

    text = ''
    try:
        resp = client.responses.create(
            model=AGENT_MODEL,
            tools=[{'type': 'web_search_preview'}],
            input=[
                {'role': 'system', 'content': _ENGAGED_SYSTEM},
                {'role': 'user',   'content': prompt},
            ],
            max_output_tokens=12000,
        )
        text = getattr(resp, 'output_text', '') or ''
        logger.info("engaged agent[ws] %s|%s -> %d chars (%.1fs)",
                     geo_type, geo_value, len(text), time.time() - t0)
    except Exception as e:
        logger.info("engaged agent web_search failed (%s); trying chat-completions",
                     str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _ENGAGED_SYSTEM},
                    {'role': 'user',   'content': prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.2,
                max_tokens=6000,
            )
            text = chat.choices[0].message.content or ''
        except Exception as e2:
            logger.warning("engaged agent BOTH paths failed for %s|%s: %s",
                            geo_type, geo_value, e2)
            return []

    if not text:
        return []

    obj = _parse_agent_response_engaged(text)
    if obj is None:
        logger.warning("engaged agent: unparseable response for %s|%s",
                        geo_type, geo_value)
        return []

    raw_list = obj.get('politicians') if isinstance(obj, dict) else None
    if not isinstance(raw_list, list):
        logger.warning("engaged agent: missing 'politicians' array for %s|%s",
                        geo_type, geo_value)
        return []

    cleaned: list[dict] = []
    seen: set[str] = set()
    for p in raw_list[:30]:
        row = _validate_engaged(p)
        if not row:
            continue
        key = row['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    cleaned.sort(key=lambda r: -r['engagement_score'])
    return cleaned[:20]


def _parse_agent_response_engaged(text: str) -> Optional[dict]:
    """Same parser as _parse_agent_response, but the truncation-salvage
    looks for a 'politicians' array rather than 'candidates'."""
    if not text:
        return None
    raw = text.strip()
    raw = re.sub(r'^```(?:json|JSON)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)
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
    arr_start = body.find('"politicians"')
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


_INFLIGHT_ENGAGED: dict[str, threading.Event] = {}
_INFLIGHT_ENGAGED_LOCK = threading.Lock()


def discover_engaged_politicians(geo_type: str, geo_value: str,
                                   force_refresh: bool = False) -> list[dict]:
    """Return the cached-or-freshly-discovered list of politicians the
    given geography is most actively engaging with right now.

    Same lazy-fill + 24h S3 cache pattern as discover_candidates.
    Returns [] on agent failure (caller falls back to internal blend).
    """
    geo_type = (geo_type or 'National').strip()
    geo_value = (geo_value or '').strip()
    cache_id = f"engaged|{geo_type}|{geo_value}"

    cache_key = S3_ENGAGED_PREFIX + _slug(geo_type) + '__' + _slug(geo_value) + '.json'

    def _get_cached() -> Optional[dict]:
        try:
            resp = _s3().get_object(Bucket=S3_BUCKET, Key=cache_key)
            last_mod = resp.get('LastModified')
            if last_mod and (datetime.now(timezone.utc) - last_mod).total_seconds() > CACHE_TTL_S:
                return None
            return json.loads(resp['Body'].read().decode('utf-8'))
        except Exception as e:
            msg = str(e)
            if 'NoSuchKey' not in msg and '404' not in msg:
                logger.debug("engaged cache miss for %s|%s: %s",
                              geo_type, geo_value, msg)
            return None

    if not force_refresh:
        cached = _get_cached()
        if cached and isinstance(cached.get('politicians'), list):
            return cached['politicians']

    with _INFLIGHT_ENGAGED_LOCK:
        ev = _INFLIGHT_ENGAGED.get(cache_id)
        if ev is not None:
            wait_for = ev
            owner = False
        else:
            wait_for = threading.Event()
            _INFLIGHT_ENGAGED[cache_id] = wait_for
            owner = True

    if not owner:
        wait_for.wait(timeout=AGENT_TIMEOUT_S)
        cached = _get_cached()
        return (cached or {}).get('politicians', []) if cached else []

    try:
        pols = _call_engaged_agent(geo_type, geo_value)
        if pols:
            payload = {
                'geo_type':      geo_type,
                'geo_value':     geo_value,
                'politicians':   pols,
                'discovered_at': datetime.now(timezone.utc).isoformat(),
                'count':         len(pols),
            }
            try:
                _s3().put_object(
                    Bucket=S3_BUCKET, Key=cache_key,
                    Body=json.dumps(payload, separators=(',', ':')).encode('utf-8'),
                    ContentType='application/json',
                    CacheControl=f'max-age={CACHE_TTL_S}',
                )
            except Exception as e:
                logger.warning("engaged cache write failed for %s|%s: %s",
                                geo_type, geo_value, e)
            return pols
        # Stale-cache fallback: serve last-known list if agent returned
        # nothing this round (e.g. rate-limited).
        try:
            resp = _s3().get_object(Bucket=S3_BUCKET, Key=cache_key)
            stale = json.loads(resp['Body'].read().decode('utf-8'))
            return stale.get('politicians') or []
        except Exception:
            return []
    finally:
        with _INFLIGHT_ENGAGED_LOCK:
            _INFLIGHT_ENGAGED.pop(cache_id, None)
        wait_for.set()


def prewarm_geos(geos: list[tuple[str, str]]) -> dict[str, int]:
    """Refresh the cache for a list of (geo_type, geo_value) pairs.
    Intended for nightly cron use. Returns {cache_id: candidate_count}.
    """
    out: dict[str, int] = {}
    for gt, gv in geos:
        try:
            cands = discover_candidates(gt, gv, force_refresh=True)
            out[f"{gt}|{gv}"] = len(cands)
        except Exception as e:
            logger.warning("prewarm %s|%s failed: %s", gt, gv, e)
            out[f"{gt}|{gv}"] = -1
    return out
