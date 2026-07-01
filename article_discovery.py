"""Political-article discovery for the Blue IQ "Top political articles" card.

Given a geography (National / State / DMA), use an OpenAI agent with web
search to return the current most-read political news articles in that
slice. Replaces / augments the GDELT + panel-URL blend, which was
surfacing junk like "Main Page", "AP Top 25 College Football Poll",
"Entertainment", and hash-suffixed apnews URL slugs because the panel
side has no editorial filter and GDELT's political topic tag bleeds
into sports / regional human-interest stories.

Returned shape per article::

    {
        "title":          "Newsom signs sweeping AI regulation bill",
        "url":            "https://...",
        "source":         "calmatters.org",
        "topic":          "Tech & AI",            # one of the issue buckets
        "summary":        "One-line context for the article",
        "interest_score": 78,                     # 0-100 (relative)
        "discovered_at":  "2026-06-05T19:00:00Z"
    }

Cache key: ``blue_iq/articles/v1/{geo_type}__{geo_value}.json``

Fail-open: every external call is wrapped in try/except. If anything
goes wrong (no API key, agent timeout, malformed JSON, S3 unavailable),
we return [] and the caller falls back to the existing panel + GDELT
blend. We NEVER raise.
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

S3_BUCKET   = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX   = 'blue_iq/articles/v3/'  # v3: impeachment-inquiry relabel clause (2026-06-29)
CACHE_TTL_S = 12 * 3600                # 12h — articles are time-sensitive
AGENT_TIMEOUT_S = float(os.environ.get('ARTICLE_AGENT_TIMEOUT', '60'))
AGENT_MODEL = os.environ.get('ARTICLE_AGENT_MODEL', 'gpt-4o')


# ── S3 cache ──────────────────────────────────────────────────────────────

def _s3():
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
            return None
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        msg = str(e)
        if 'NoSuchKey' not in msg and '404' not in msg:
            logger.debug("article cache miss for %s|%s: %s", geo_type, geo_value, msg)
        return None


def _cache_put(geo_type: str, geo_value: str, payload: dict) -> None:
    key = _cache_key(geo_type, geo_value)
    try:
        _s3().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, separators=(',', ':')).encode('utf-8'),
            ContentType='application/json',
            CacheControl=f'max-age={CACHE_TTL_S}',
        )
    except Exception as e:
        logger.warning("article cache write failed for %s|%s: %s",
                        geo_type, geo_value, e)


# ── OpenAI agent ──────────────────────────────────────────────────────────

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
        logger.warning("openai client init failed in article_discovery: %s", e)
        return None


_AGENT_SYSTEM = (
    "You are a political-news research assistant for a Democratic\n"
    "campaign analytics dashboard. Given a U.S. geography, use web\n"
    "search to surface the CURRENT most-read political news articles\n"
    "voters in that geography are likely encountering — over the last\n"
    "7 days, refreshed daily.\n"
    "\n"
    "INCLUDE\n"
    "  - Domestic policy reporting (healthcare, immigration, taxes,\n"
    "    housing, gas/energy, climate, education, election integrity,\n"
    "    crime, foreign policy)\n"
    "  - 2026-cycle race coverage (Senate, House, Governor, mayoral)\n"
    "  - 2028 presidential prospect coverage\n"
    "  - State-house / state legislature reporting when geographically\n"
    "    relevant\n"
    "  - Investigative reporting, candidate scandals, polling stories\n"
    "  - Major executive action / legislation coverage\n"
    "  - Ballotpedia explainers / Wikipedia race pages ONLY when they're\n"
    "    the canonical reference for the race itself (e.g. United States\n"
    "    Congress elections 2026); avoid generic wiki Main Page entries\n"
    "\n"
    "EXCLUDE — strict\n"
    "  - Sports (any league, any college team, AP football poll, etc.)\n"
    "  - Entertainment / celebrity / awards shows / box office\n"
    "  - True crime / human-interest / disease outbreaks (Hantavirus\n"
    "    cruise ship, missing-person stories, weather) unless those\n"
    "    stories are driving a political narrative\n"
    "  - Generic homepage / index pages ('Main Page', 'Entertainment',\n"
    "    'Trending Now')\n"
    "  - Listicles / SEO bait\n"
    "  - Foreign-only news with no U.S. political angle\n"
    "  - Tech product reviews / gadget coverage\n"
    "\n"
    "Use REAL ARTICLE URLs and REAL TITLES from your web search. Do not\n"
    "fabricate. If you cannot verify an article, omit it. Prefer national\n"
    "+ regional newsrooms: AP, Reuters, NYT, WSJ, WaPo, Politico, Axios,\n"
    "NPR, The Hill, Bloomberg, calmatters.org, propublica.org, plus the\n"
    "geography's flagship local outlets (Chicago Tribune, Houston\n"
    "Chronicle, etc.). Skip clickbait outlets.\n"
    "\n"
    "TOPIC tagging: classify each article into ONE of these buckets so\n"
    "the dashboard can group + color them. Use exact strings:\n"
    "  - 'Healthcare', 'Immigration', 'Taxes', 'Housing & Rent',\n"
    "    'Gas & Energy', 'Climate', 'Education',\n"
    "    'Election Integrity & Voting', 'Crime & Safety',\n"
    "    'Foreign Policy', 'Tech & AI', 'Jobs & Economy',\n"
    "    'Race & Civil Rights', 'Abortion & Reproductive Rights',\n"
    "    'Campaign & Election', 'Other Policy'\n"
    "\n"
    "INTEREST_SCORE 0-100: your estimate of how much this article is\n"
    "RIGHT NOW driving discourse in the geography. 100 = top-of-mind\n"
    "national headline (e.g. a Supreme Court ruling that dropped today).\n"
    "20-40 = solid regional / niche piece that's still relevant. Vary\n"
    "the scores — don't return everything at 70.\n"
    "\n"
    "BANNED TERMS — do not return any article whose title, topic, or\n"
    "summary mentions:\n"
    "  - government shutdown / federal government shutdown / gov shutdown\n"
    "If a budget / appropriations story is genuinely the lead story for\n"
    "the geography, only include it if the title can be cited verbatim\n"
    "WITHOUT the banned phrase. Skip otherwise.\n"
    "\n"
    "TERM RELABELS — never prefix 'impeachment inquiry' with a personal\n"
    "name in title / topic / summary. Write 'impeachment inquiry' (not\n"
    "'Biden impeachment inquiry', not 'Trump impeachment inquiry'). If\n"
    "the source's actual headline uses the prefixed form, rewrite to\n"
    "the unprefixed label before returning.\n"
    "\n"
    "OUTPUT FORMAT: return ONLY a JSON object. No markdown fences, no\n"
    "commentary. First char `{`, last char `}`:\n"
    "\n"
    "{\n"
    '  "articles": [\n'
    "    {\n"
    '      "title":          "Full article title from the source",\n'
    '      "url":            "https://full-url-to-the-article",\n'
    '      "source":         "domain.com (no www)",\n'
    '      "topic":          "one of the topic strings above",\n'
    '      "summary":        "one-line context, max 140 chars",\n'
    '      "interest_score": 0-100 integer\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Hard cap: 20 articles per response. Sort by interest_score desc."
)


def _build_agent_prompt(geo_type: str, geo_value: str) -> str:
    if geo_type == 'National' or not geo_value:
        return (
            "GEOGRAPHY: National (United States)\n\n"
            "Surface the top ~20 most-read U.S. political articles of the\n"
            "last 7 days. Mix national headlines (Senate, White House,\n"
            "Supreme Court, marquee 2026 races) with a couple of\n"
            "high-profile state-level stories that broke into national\n"
            "discourse. Skip sports, entertainment, and human-interest."
        )
    if geo_type == 'State':
        return (
            f"GEOGRAPHY: State of {geo_value}\n\n"
            f"Surface the top ~20 most-read political articles relevant to\n"
            f"voters in {geo_value} over the last 7 days. Include:\n"
            "  - State-level races (Senate / House / Governor 2026, state\n"
            "    legislature, AG, SoS)\n"
            "  - Major city mayoral coverage if applicable\n"
            "  - National stories that disproportionately matter to this\n"
            "    state (e.g. border / immigration for TX/AZ, climate for\n"
            "    CA/FL/LA, manufacturing for MI/OH)\n"
            "  - State-house policy fights\n"
            "Prioritize the state's flagship newspaper + AP regional + NPR\n"
            "member station coverage in the source mix."
        )
    if geo_type == 'DMA':
        return (
            f"GEOGRAPHY: DMA / Media market: {geo_value}\n\n"
            f"Surface the top ~20 most-read political articles a voter in\n"
            f"the {geo_value} media market is likely encountering. Use the\n"
            f"DMA's principal anchor city as your geographic anchor.\n"
            "Include:\n"
            "  - The principal city's mayoral / council coverage\n"
            "  - Congressional districts that overlap this DMA\n"
            "  - The state's 2026 Senate / Governor race coverage\n"
            "  - National stories driving local talk-radio / TV news\n"
            "Prefer this DMA's flagship daily + local NPR + AP regional."
        )
    return (
        f"GEOGRAPHY: {geo_type} = {geo_value}\n\n"
        "Surface the top 20 most-read U.S. political articles relevant\n"
        "to voters in this area."
    )


# ── Validation ────────────────────────────────────────────────────────────

VALID_TOPICS = frozenset({
    'Healthcare', 'Immigration', 'Taxes', 'Housing & Rent', 'Gas & Energy',
    'Climate', 'Education', 'Election Integrity & Voting', 'Crime & Safety',
    'Foreign Policy', 'Tech & AI', 'Jobs & Economy', 'Race & Civil Rights',
    'Abortion & Reproductive Rights', 'Campaign & Election', 'Other Policy',
})


def _validate_article(a: dict) -> Optional[dict]:
    if not isinstance(a, dict):
        return None
    title = (a.get('title') or '').strip()
    url   = (a.get('url') or '').strip()
    if not title or not url:
        return None
    if len(title) > 240 or len(url) > 600:
        return None
    if not url.startswith(('http://', 'https://')):
        return None
    source = (a.get('source') or '').strip().lower()
    # Strip leading scheme + www if the agent included them.
    source = re.sub(r'^https?://', '', source)
    source = re.sub(r'^www\.', '', source)
    source = source.split('/', 1)[0][:80]
    if not source:
        try:
            import urllib.parse
            source = (urllib.parse.urlparse(url).hostname or '').lower()
            if source.startswith('www.'):
                source = source[4:]
        except Exception:
            source = ''
    topic = (a.get('topic') or '').strip()
    if topic not in VALID_TOPICS:
        topic = 'Other Policy'
    try:
        score = int(a.get('interest_score') or 0)
    except Exception:
        score = 0
    score = max(0, min(100, score))
    return {
        'title':          title[:240],
        'url':            url,
        'source':         source,
        'topic':          topic,
        'summary':        (a.get('summary') or '').strip()[:180],
        'interest_score': score,
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
    arr_start = body.find('"articles"')
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


def _call_agent(geo_type: str, geo_value: str) -> list[dict]:
    client = _openai_client()
    if client is None:
        logger.info("article agent: no OpenAI client; skipping")
        return []
    prompt = _build_agent_prompt(geo_type, geo_value)
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
            max_output_tokens=14000,
        )
        text = getattr(resp, 'output_text', '') or ''
        logger.info("article agent[ws] %s|%s -> %d chars (%.1fs)",
                     geo_type, geo_value, len(text), time.time() - t0)
    except Exception as e:
        logger.info("article agent web_search failed (%s); trying chat", str(e)[:200])
        try:
            chat = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {'role': 'system', 'content': _AGENT_SYSTEM},
                    {'role': 'user',   'content': prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0.2,
                max_tokens=6000,
            )
            text = chat.choices[0].message.content or ''
            logger.info("article agent[chat] %s|%s -> %d chars (%.1fs)",
                         geo_type, geo_value, len(text), time.time() - t0)
        except Exception as e2:
            logger.warning("article agent BOTH paths failed for %s|%s: %s",
                            geo_type, geo_value, e2)
            return []
    if not text:
        return []
    obj = _parse_agent_response(text)
    if obj is None:
        logger.warning("article agent: unparseable response for %s|%s (head=%r tail=%r)",
                        geo_type, geo_value, text[:120], text[-120:])
        return []
    raw_list = obj.get('articles') if isinstance(obj, dict) else None
    if not isinstance(raw_list, list):
        logger.warning("article agent: missing 'articles' array for %s|%s",
                        geo_type, geo_value)
        return []
    cleaned: list[dict] = []
    seen_url: set[str] = set()
    seen_title: set[str] = set()
    for a in raw_list[:40]:
        row = _validate_article(a)
        if not row:
            continue
        u_key = row['url'].lower()
        t_key = row['title'].lower()
        if u_key in seen_url or t_key in seen_title:
            continue
        seen_url.add(u_key)
        seen_title.add(t_key)
        cleaned.append(row)
    cleaned.sort(key=lambda r: -r['interest_score'])
    return cleaned[:25]


# ── Public entrypoint ─────────────────────────────────────────────────────

_INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()


def discover_political_articles(geo_type: str, geo_value: str,
                                  force_refresh: bool = False) -> list[dict]:
    """Return cached-or-freshly-discovered political articles for a geo.

    Strategy:
      1. Cache hit (< 12h) -> return cached list.
      2. Else -> call agent, cache, return.
      3. On agent failure -> stale cache, then [].
    Never raises.
    """
    geo_type = (geo_type or 'National').strip()
    geo_value = (geo_value or '').strip()
    cache_id = f"{geo_type}|{geo_value}"

    if not force_refresh:
        cached = _cache_get(geo_type, geo_value)
        if cached and isinstance(cached.get('articles'), list):
            return cached['articles']

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
        return (cached or {}).get('articles', []) if cached else []

    try:
        articles = _call_agent(geo_type, geo_value)
        if articles:
            payload = {
                'geo_type':      geo_type,
                'geo_value':     geo_value,
                'articles':      articles,
                'discovered_at': datetime.now(timezone.utc).isoformat(),
                'count':         len(articles),
            }
            _cache_put(geo_type, geo_value, payload)
            return articles
        try:
            resp = _s3().get_object(
                Bucket=S3_BUCKET,
                Key=_cache_key(geo_type, geo_value),
            )
            stale = json.loads(resp['Body'].read().decode('utf-8'))
            return stale.get('articles') or []
        except Exception:
            return []
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(cache_id, None)
        wait_for.set()
