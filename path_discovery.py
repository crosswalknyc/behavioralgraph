"""Issue-path discovery for the Blue IQ "Top observed paths" card.

For each policy issue bucket surfaced in the dashboard slice (Healthcare,
Immigration, Gas & Energy, etc.), use an OpenAI agent with web search +
public research to model what U.S. voters TYPICALLY do online after
searching that issue. The result is a per-issue 3-step path:

    SEARCHED <issue> -> NEXT <specific action + share>
                     -> THEN <specific follow-up + share>

Replaces the prior hardcoded BLUE_IQ_PATH_FOLLOWUPS table which produced
identical paths for every issue (e.g. every row showed "Read more
political news -> Continued to a left/right opinion piece, 0%/0%").

Returned shape per issue::

    {
        "bucket":            "Healthcare",
        "next_action":       "Read a candidate's healthcare policy page",
        "next_share":        0.28,          # 0-1, share of the issue cohort
        "follow_up_action":  "Signed up for the campaign email list",
        "follow_up_share":   0.18,          # 0-1, share of step-1 cohort
        "rationale":         "One-line research basis (Pew 2024 / Statista)"
    }

Cache key: ``blue_iq/issue_paths/v1/{geo_type}__{geo_value}__{issue_hash}.json``
The issue_hash is a stable 8-char hash of the sorted issue list so the
cache invalidates if the dashboard's bucket set drifts.

Fail-open: every external call is wrapped. If anything goes wrong (no
API key, agent timeout, malformed JSON, S3 unavailable), we return [] and
the frontend falls back to the previous static path-followups table.
We NEVER raise.
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
S3_PREFIX   = 'blue_iq/issue_paths/v2/'  # v2: banned-term system-prompt clause (2026-06-12)
CACHE_TTL_S = 24 * 3600
AGENT_TIMEOUT_S = float(os.environ.get('PATH_AGENT_TIMEOUT', '60'))
AGENT_MODEL = os.environ.get('PATH_AGENT_MODEL', 'gpt-4o')


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


def _issue_hash(issues: list[str]) -> str:
    """Stable 8-char hash of the sorted issue list so the cache key
    invalidates whenever the dashboard's bucket set changes."""
    joined = '|'.join(sorted({(i or '').strip().lower() for i in issues}))
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()[:8]


def _cache_key(geo_type: str, geo_value: str, issues: list[str]) -> str:
    return (f"{S3_PREFIX}{_slug(geo_type)}__{_slug(geo_value)}__"
            f"{_issue_hash(issues)}.json")


def _cache_get(geo_type: str, geo_value: str, issues: list[str]) -> Optional[dict]:
    key = _cache_key(geo_type, geo_value, issues)
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
            logger.debug("path cache miss for %s|%s: %s", geo_type, geo_value, msg)
        return None


def _cache_put(geo_type: str, geo_value: str, issues: list[str], payload: dict) -> None:
    key = _cache_key(geo_type, geo_value, issues)
    try:
        _s3().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, separators=(',', ':')).encode('utf-8'),
            ContentType='application/json',
            CacheControl=f'max-age={CACHE_TTL_S}',
        )
    except Exception as e:
        logger.warning("path cache write failed for %s|%s: %s",
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
        logger.warning("openai client init failed in path_discovery: %s", e)
        return None


_AGENT_SYSTEM = (
    "You are a political digital-marketing research assistant. For each\n"
    "U.S. policy issue listed, model the TYPICAL voter's online journey\n"
    "AFTER searching for that issue: what is the next action they\n"
    "predominantly take, and what is the most common follow-up to that?\n"
    "\n"
    "Use web search and public research where possible. Reason from\n"
    "the audience persona for each issue. Make EACH issue's path\n"
    "DISTINCT — do not return the same generic 'read more political news'\n"
    "/ 'continued to opinion piece' for every issue. The whole point is\n"
    "that voters search Healthcare differently from how they search Gas\n"
    "& Energy, Immigration, or Election Integrity.\n"
    "\n"
    "EXAMPLES of good distinct paths (DO NOT reuse verbatim; tune each\n"
    "to the actual issue and to recent research):\n"
    "  Healthcare    -> NEXT: 'Checked candidate's stance on ACA / Medicare' (28%)\n"
    "                -> THEN: 'Compared with their incumbent's voting record' (16%)\n"
    "  Gas & Energy  -> NEXT: 'Watched a campaign ad about energy on YouTube' (24%)\n"
    "                -> THEN: 'Searched local gas prices for last week' (19%)\n"
    "  Immigration   -> NEXT: 'Read a local news article about state enforcement' (32%)\n"
    "                -> THEN: 'Engaged with an immigration-focused social post' (14%)\n"
    "  Election Integrity & Voting -> NEXT: 'Looked up their state's voter ID rules' (35%)\n"
    "                              -> THEN: 'Verified their voter registration status' (22%)\n"
    "\n"
    "GROUND your numbers in plausible public research:\n"
    "  - Pew Research voter-behavior studies\n"
    "  - eMarketer / Statista digital-engagement reports\n"
    "  - Civic engagement studies (CIRCLE, Knight Foundation)\n"
    "  - DMA-specific media-consumption reports\n"
    "  - The Knight Foundation / Brennan Center voter-journey work\n"
    "\n"
    "next_share ranges: 12% - 45% (realistic 'next-action share within\n"
    "issue cohort'). follow_up_share ranges: 6% - 32% (share of the\n"
    "step-1 cohort who also do the follow-up). Avoid identical shares\n"
    "across issues — they vary by issue urgency, search intent, etc.\n"
    "\n"
    "BANNED TERMS — do not mention, allude to, or paraphrase any of\n"
    "these in any field (next_action, follow_up_action, rationale):\n"
    "  - government shutdown / federal government shutdown / gov shutdown\n"
    "If an issue's typical journey would naturally involve such language,\n"
    "frame it via the underlying policy (budget impasse, appropriations,\n"
    "agency funding) WITHOUT using the banned phrase.\n"
    "\n"
    "OUTPUT FORMAT: return ONLY a JSON object matching this schema, no\n"
    "markdown fences, no commentary, first char `{`, last char `}`:\n"
    "\n"
    "{\n"
    '  "paths": [\n'
    "    {\n"
    '      "bucket":           "Healthcare",\n'
    '      "next_action":      "<specific action, max 70 chars>",\n'
    '      "next_share":       0.28,\n'
    '      "follow_up_action": "<specific follow-up, max 70 chars>",\n'
    '      "follow_up_share":  0.16,\n'
    '      "rationale":        "<one short sentence citing research basis>"\n'
    "    }\n"
    "  ]\n"
    "}"
)


def _build_agent_prompt(geo_type: str, geo_value: str, issues: list[str]) -> str:
    issue_list = '\n'.join(f'  - {i}' for i in issues)
    geo_blurb = (
        "Audience: U.S. voters generally (no geographic refinement)."
        if (geo_type == 'National' or not geo_value)
        else f"Audience: U.S. voters in {geo_type} = {geo_value}. "
             "Tune the rationale + specific actions to that geography's "
             "media environment / race calendar where relevant."
    )
    return (
        f"{geo_blurb}\n"
        "\n"
        "Model each policy-issue path independently. The issues are:\n"
        f"{issue_list}\n"
        "\n"
        "Return one entry per issue in the SAME ORDER as the list above. "
        "Each entry must have a DISTINCT next_action / follow_up_action — "
        "do not reuse the same string across issues. Vary the shares to "
        "reflect realistic differences in voter intent across issues."
    )


# ── Validation ────────────────────────────────────────────────────────────

def _validate_path(p: dict) -> Optional[dict]:
    if not isinstance(p, dict):
        return None
    bucket = (p.get('bucket') or '').strip()
    next_action = (p.get('next_action') or '').strip()
    follow_up = (p.get('follow_up_action') or '').strip()
    if not bucket or not next_action or not follow_up:
        return None
    try:
        next_share = float(p.get('next_share') or 0.0)
        follow_share = float(p.get('follow_up_share') or 0.0)
    except Exception:
        return None
    # Clamp to sane ranges. We accept either fraction (0-1) or percent
    # (1-100) input and normalize to fraction.
    if next_share > 1.0:
        next_share = next_share / 100.0
    if follow_share > 1.0:
        follow_share = follow_share / 100.0
    next_share = max(0.05, min(0.55, next_share))
    follow_share = max(0.03, min(0.40, follow_share))
    return {
        'bucket':           bucket[:80],
        'next_action':      next_action[:120],
        'next_share':       round(next_share, 4),
        'follow_up_action': follow_up[:120],
        'follow_up_share':  round(follow_share, 4),
        'rationale':        (p.get('rationale') or '').strip()[:200],
    }


# ── Truncation-tolerant JSON parser ──────────────────────────────────────

def _parse_agent_response(text: str) -> Optional[dict]:
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
    # Truncation salvage: find last complete path-object close.
    arr_start = body.find('"paths"')
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


def _call_agent(geo_type: str, geo_value: str, issues: list[str]) -> list[dict]:
    """Issue the agent call. Returns [] on any failure."""
    client = _openai_client()
    if client is None:
        logger.info("path agent: no OpenAI client; skipping")
        return []
    prompt = _build_agent_prompt(geo_type, geo_value, issues)
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
            max_output_tokens=12000,
        )
        text = getattr(resp, 'output_text', '') or ''
        logger.info("path agent[ws] %s|%s (%d issues) -> %d chars (%.1fs)",
                     geo_type, geo_value, len(issues), len(text), time.time() - t0)
    except Exception as e:
        logger.info("path agent web_search failed (%s); trying chat", str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _AGENT_SYSTEM},
                    {'role': 'user',   'content': prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.3,
                max_tokens=6000,
            )
            text = chat.choices[0].message.content or ''
            logger.info("path agent[chat] %s|%s -> %d chars (%.1fs)",
                         geo_type, geo_value, len(text), time.time() - t0)
        except Exception as e2:
            logger.warning("path agent BOTH paths failed for %s|%s: %s",
                            geo_type, geo_value, e2)
            return []
    if not text:
        return []
    obj = _parse_agent_response(text)
    if obj is None:
        logger.warning("path agent: unparseable response for %s|%s (head=%r tail=%r)",
                        geo_type, geo_value, text[:120], text[-120:])
        return []
    raw_list = obj.get('paths') if isinstance(obj, dict) else None
    if not isinstance(raw_list, list):
        logger.warning("path agent: missing 'paths' array for %s|%s", geo_type, geo_value)
        return []
    issue_set = {i.strip().lower() for i in issues}
    cleaned: list[dict] = []
    seen: set[str] = set()
    for p in raw_list[:30]:
        row = _validate_path(p)
        if not row:
            continue
        key = row['bucket'].lower()
        # Only keep paths for issues we actually asked about (drops
        # hallucinated extra buckets).
        if key not in issue_set:
            continue
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    # If next_action / follow_up_action collide across rows, perturb the
    # share so they don't render identically. The agent occasionally
    # repeats the same text; we keep the first one as-is and let the UI
    # show that one. (Could also de-dupe entirely but that risks empty
    # rows for the affected bucket.)
    return cleaned


# ── Public entrypoint ─────────────────────────────────────────────────────

_INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()


def discover_issue_paths(geo_type: str, geo_value: str, issues: list[str],
                          force_refresh: bool = False) -> list[dict]:
    """Return researched per-issue journey paths for a geography.

    issues = ordered list of issue-bucket names (Healthcare, Gas & Energy,
    etc.) to research. The agent returns one path per issue, in the same
    order, with distinct next/follow-up text + realistic shares.

    Strategy:
      1. Cache hit (< 24h, matching issue hash) -> return cached paths.
      2. Else -> call agent, cache, return.
      3. On agent failure -> stale-cache OR empty list (caller falls
         back to the static client-side BLUE_IQ_PATH_FOLLOWUPS).
    Never raises.
    """
    geo_type = (geo_type or 'National').strip()
    geo_value = (geo_value or '').strip()
    issues = [i for i in (issues or []) if i and isinstance(i, str)]
    if not issues:
        return []
    # Hard cap so a runaway list of buckets doesn't blow the prompt.
    issues = issues[:12]
    cache_id = f"{geo_type}|{geo_value}|{_issue_hash(issues)}"

    if not force_refresh:
        cached = _cache_get(geo_type, geo_value, issues)
        if cached and isinstance(cached.get('paths'), list):
            return cached['paths']

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
        cached = _cache_get(geo_type, geo_value, issues)
        return (cached or {}).get('paths', []) if cached else []

    try:
        paths = _call_agent(geo_type, geo_value, issues)
        if paths:
            payload = {
                'geo_type':      geo_type,
                'geo_value':     geo_value,
                'issues':        issues,
                'paths':         paths,
                'discovered_at': datetime.now(timezone.utc).isoformat(),
                'count':         len(paths),
            }
            _cache_put(geo_type, geo_value, issues, payload)
            return paths
        # Agent failed: try stale cache.
        try:
            resp = _s3().get_object(
                Bucket=S3_BUCKET,
                Key=_cache_key(geo_type, geo_value, issues),
            )
            stale = json.loads(resp['Body'].read().decode('utf-8'))
            return stale.get('paths') or []
        except Exception:
            return []
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(cache_id, None)
        wait_for.set()
