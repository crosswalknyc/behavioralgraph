"""
microdramas_audience_agent.py

Claude-backed per-title audience research for the Microdramas IQ
dashboard. Mirrors the pattern used by BG.persona_research_agent:

  1. Take (title, series, genre, platform)
  2. Call Claude with a system prompt asking for the audience profile
     in structured JSON that matches the canonical demographic bucket
     labels from `/Users/jennamenking/Downloads/demos.csv`
  3. Parse the JSON, sum-to-100 renormalize each category
  4. Cache the result in
     s3://dashboard-inputs/microdramas_iq/audience_cache/{key}.json
  5. Return the payload the frontend renders in the click-through modal

Canonical demographic categories (per demos.csv):
  - GENDER
  - ETHNICITY
  - INCOME
  - EDUCATION
  - AGE
  - RELATIONSHIP
  - PARENTAL_STATUS
  - OCCUPATION
  - SEXUAL_ORIENTATION

Falls back to a deterministic keyword-tilt heuristic when the Claude
client is unavailable (no ANTHROPIC_API_KEY, network failure, or the
`migration/claude_client.py` module can't be imported from bg-webapp).
The frontend cannot tell the difference; the payload shape is
identical either way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Config
# ============================================================================
S3_BUCKET             = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
S3_AUDIENCE_CACHE_KEY = 'microdramas_iq/audience_cache/{key}.json'
CACHE_TTL_S           = int(os.environ.get('MICRODRAMAS_AUDIENCE_TTL', str(60 * 60 * 24 * 14)))  # 14 days

CLAUDE_MODEL = os.environ.get('MICRODRAMAS_CLAUDE_MODEL') or 'claude-sonnet-4-5'


# ============================================================================
# Canonical demographic bucket labels (matches demos.csv exactly)
# ============================================================================
DEMO_BUCKETS = {
    'GENDER': [
        'Female', 'Male', 'Non-Binary',
        'Trans Female', 'Trans Male', 'Prefer Not to Say',
    ],
    'AGE': [
        '17 and Under', '18-24', '25-34', '35-44',
        '45-54', '55-64', '65 or Older',
    ],
    'ETHNICITY': [
        'White', 'Hispanic or Latino', 'Black or African American',
        'Asian', 'Another Race/Ethnicity',
    ],
    'INCOME': [
        'Less than $25,000', '$25,000 - $49,999',
        '$50,000 - $74,999', '$75,000 - $99,999',
        '$100,000 - $149,999', '$150,000 - $249,999',
        '$250,000 or More',
    ],
    'EDUCATION': [
        'High School or Less', 'Some College / Associate Degree',
        "Bachelor's Degree", 'Graduate or Professional Degree',
        'Prefer Not to Say',
    ],
    'RELATIONSHIP': [
        'Single', 'In a Relationship', 'Married',
        'Divorced or Separated', 'Widowed', 'Prefer Not to Say',
    ],
    'PARENTAL_STATUS': [
        'Has Children', 'No Children', 'Prefer Not to Say',
    ],
    'OCCUPATION': [
        'Management, Business & Professional',
        'Healthcare Practitioners or Support',
        'Sales & Retail',
        'Education or Library Services',
        'Service & Hospitality',
        'Science, Technology & Technical Professions',
        'Skilled Trades/Construction or Maintenance',
        'Agriculture & Outdoor',
        'Transportation & Logistics',
        'Manufacturing & Production',
        'Public Safety & Protective Services',
        'Legal',
        'Other',
    ],
    'SEXUAL_ORIENTATION': [
        'Straight / Heterosexual', 'Gay or Lesbian',
        'Another Sexual Orientation', 'Prefer Not to Say',
    ],
}

# Order preserved for rendering (matches the demos.csv layout)
DEMO_ORDER = [
    'GENDER', 'AGE', 'ETHNICITY', 'INCOME', 'EDUCATION',
    'RELATIONSHIP', 'PARENTAL_STATUS', 'OCCUPATION', 'SEXUAL_ORIENTATION',
]


# ============================================================================
# Claude client shim - reuse migration/claude_client if available
# ============================================================================
def _load_claude_client():
    """Return `claude_messages` from migration/claude_client, or None."""
    # bg-webapp is a submodule; migration/ lives one level up
    here  = os.path.dirname(os.path.abspath(__file__))
    root  = os.path.abspath(os.path.join(here, '..'))
    mpath = os.path.join(root, 'migration')
    if mpath not in sys.path:
        sys.path.insert(0, mpath)
    try:
        from claude_client import claude_messages, get_claude_client  # type: ignore
        # Confirm we have a real key (the singleton returns None when unset)
        if get_claude_client() is None:
            return None
        return claude_messages
    except Exception as e:
        logger.info('microdramas_audience_agent: claude_client unavailable (%s)', e)
        return None


# ============================================================================
# S3 cache
# ============================================================================
def _s3_client():
    import boto3  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region)


def _cache_key(title: str, series: str, platform: str) -> str:
    safe = re.sub(r'[^a-z0-9]+', '_', (title or '').lower()).strip('_') or 'untitled'
    h = hashlib.md5(f'{title}|{series}|{platform}'.encode('utf-8')).hexdigest()[:12]
    return f'{safe}_{h}'


def _read_cache(key: str) -> Optional[dict]:
    try:
        s3 = _s3_client()
        resp = s3.get_object(Bucket=S3_BUCKET,
                              Key=S3_AUDIENCE_CACHE_KEY.format(key=key))
        raw = resp['Body'].read().decode('utf-8')
        payload = json.loads(raw)
        # Freshness check
        gen = payload.get('generated_at')
        if gen:
            try:
                dt = datetime.fromisoformat(gen.replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if age > CACHE_TTL_S:
                    return None
            except Exception:
                pass
        return payload
    except Exception as e:
        logger.debug('microdramas_audience_agent: cache miss for %s (%s)', key, e)
        return None


def _write_cache(key: str, payload: dict) -> None:
    try:
        s3 = _s3_client()
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=S3_AUDIENCE_CACHE_KEY.format(key=key),
            Body=body,
            ContentType='application/json',
            CacheControl='public, max-age=3600',
        )
    except Exception as e:
        logger.warning('microdramas_audience_agent: cache write failed for %s (%s)', key, e)


# ============================================================================
# Claude prompt
# ============================================================================
_SYSTEM_PROMPT = (
    "You are a senior audience-research analyst specializing in mobile-first "
    "vertical drama (\"microdrama\") content. You research the audience for a "
    "single microdrama title and return one valid JSON object matching the "
    "user's schema.\n\n"
    "For every microdrama title you analyze:\n"
    "- Reason about the title's genre, tropes, and typical viewer archetype\n"
    "- Anchor demographics to the published vertical-drama audience shape "
    "(ReelShort 18M MAU, DramaBox 13M MAU, Peacock Shorts hub launch data), "
    "then tilt for the specific title's tropes (werewolf/mafia titles skew "
    "younger; billionaire romance skews wider age; sports/revenge titles pull "
    "more men)\n"
    "- Every demographic category must sum to exactly 100%\n"
    "- Use ONLY the canonical bucket labels provided in the user prompt\n"
    "- Never invent new buckets\n"
    "- Include a 3-5 sentence audience_summary and 5-8 interests with an "
    "index vs Gen Pop (100 = matches Gen Pop, 150 = 1.5x more likely)\n\n"
    "Return ONLY the JSON object, no markdown fences, no commentary before or after."
)


def _build_user_prompt(title: str, series: str, genre: str, platform: str) -> str:
    lines = []
    lines.append(f'Title: {title}')
    if series and series != title:
        lines.append(f'Series: {series}')
    if genre:
        lines.append(f'Genre: {genre}')
    if platform:
        lines.append(f'Platform: {platform}')
    lines.append('')
    lines.append('Research the audience for this microdrama title.')
    lines.append('')
    lines.append('Canonical demographic bucket labels (use these EXACTLY, sum each to 100):')
    for cat in DEMO_ORDER:
        buckets = DEMO_BUCKETS[cat]
        lines.append(f'  {cat}: {buckets}')
    lines.append('')
    lines.append('Return JSON in this exact shape:')
    lines.append('{')
    lines.append('  "audience_summary": "<3-5 sentences about who watches this title>",')
    lines.append('  "demographics": {')
    lines.append('    "GENDER": {"Female": 62.4, "Male": 34.8, ...},')
    lines.append('    "AGE": {"17 and Under": 3.2, "18-24": 22.1, ...},')
    for cat in DEMO_ORDER[2:]:
        lines.append(f'    "{cat}": {{...canonical buckets sum to 100...}}')
    lines.append('  },')
    lines.append('  "interests": [')
    lines.append('    {"label": "BookTok / romance novels", "index": 172},')
    lines.append('    {"label": "Reality dating shows",      "index": 168},')
    lines.append('    ...5-8 total, each index between 60 and 260')
    lines.append('  ],')
    lines.append('  "platform_affinities": [')
    lines.append('    {"label": "TikTok",          "reach_pct": 84.6},')
    lines.append('    {"label": "Instagram Reels", "reach_pct": 78.3},')
    lines.append('    ...include at least: TikTok, Instagram Reels, YouTube Shorts, Snapchat Spotlight, Facebook, Pinterest, Reddit, X (Twitter)')
    lines.append('  ]')
    lines.append('}')
    return '\n'.join(lines)


# ============================================================================
# JSON extraction + validation
# ============================================================================
def _extract_json_block(text: str) -> Optional[dict]:
    """Find the outermost {...} block and json.loads it. Robust to
    markdown fences or leading/trailing commentary."""
    if not text:
        return None
    s = text.strip()
    # Strip common fences
    s = re.sub(r'^```(?:json)?\s*', '', s)
    s = re.sub(r'\s*```$', '', s)
    # First-brace to last-brace slice
    i = s.find('{')
    j = s.rfind('}')
    if i < 0 or j < i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return None


def _renormalize_100(demo_dict: dict, buckets: list[str]) -> list[dict]:
    """Ensure a category sums to 100% and uses exactly the canonical buckets.
    Returns [{label, pct}] ordered as `buckets`."""
    if not isinstance(demo_dict, dict):
        demo_dict = {}
    # Case-insensitive & punctuation-insensitive matching against canonical labels
    def _norm(s):
        return re.sub(r'[^a-z0-9]+', '', str(s).lower())
    idx = {_norm(k): float(v) for k, v in demo_dict.items()
            if isinstance(v, (int, float))}
    out_pcts = []
    for b in buckets:
        pct = idx.get(_norm(b), 0.0)
        out_pcts.append(max(0.0, pct))
    total = sum(out_pcts)
    if total <= 0:
        # Uniform fallback
        n = len(buckets)
        return [{'label': b, 'pct': round(100.0 / n, 2)} for b in buckets]
    factor = 100.0 / total
    scaled = [round(p * factor, 2) for p in out_pcts]
    # Repair rounding drift by nudging the largest bucket
    drift = round(100.0 - sum(scaled), 2)
    if abs(drift) >= 0.01:
        largest = max(range(len(scaled)), key=lambda i: scaled[i])
        scaled[largest] = round(scaled[largest] + drift, 2)
    return [{'label': b, 'pct': p} for b, p in zip(buckets, scaled)]


def _shape_agent_payload(raw: dict, title: str, series: str,
                         genre: str, platform: str,
                         source: str) -> dict:
    """Normalize an agent response into the exact shape the dashboard renders."""
    demos_in_raw = raw.get('demographics') or {}
    # Case-insensitive lookup: Claude sometimes returns lowercase keys
    # ('gender', 'age') even though the prompt asks for uppercase.
    demos_in = {str(k).upper(): v for k, v in demos_in_raw.items()}
    demographics = {
        cat.lower(): _renormalize_100(demos_in.get(cat) or {}, DEMO_BUCKETS[cat])
        for cat in DEMO_ORDER
    }
    interests = []
    for r in (raw.get('interests') or [])[:8]:
        if not isinstance(r, dict):
            continue
        lbl = str(r.get('label') or '').strip()
        idx = r.get('index')
        if lbl and isinstance(idx, (int, float)):
            interests.append({'label': lbl, 'index': int(idx)})
    platforms = []
    for r in (raw.get('platform_affinities') or [])[:12]:
        if not isinstance(r, dict):
            continue
        lbl = str(r.get('label') or '').strip()
        rp = r.get('reach_pct')
        if lbl and isinstance(rp, (int, float)):
            platforms.append({'label': lbl, 'reach_pct': round(float(rp), 1)})
    return {
        'success':       True,
        'title':         title,
        'series':        series or title,
        'genre':         genre or '',
        'platform':      platform or '',
        'audience_summary': (raw.get('audience_summary') or '').strip(),
        'demographics':  demographics,
        'demo_order':    [c.lower() for c in DEMO_ORDER],
        'demo_labels':   {c.lower(): c.replace('_', ' ').title()
                          for c in DEMO_ORDER},
        'interests':     interests,
        'platform_affinities': platforms,
        'source':        source,   # 'claude' | 'heuristic'
        'generated_at':  datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# Heuristic fallback (used when Claude is unreachable)
# ============================================================================
# Keeps the payload shape identical to the agent path. Baseline
# distribution mirrors the vertical-drama audience shape from the
# Peacock Shorts hub launch materials and data.ai Q1 2026 ReelShort +
# DramaBox breakdowns. Genre keyword bumps small deltas for age /
# gender / income / relationship / parental status.
_BASE_DISTRIBUTION = {
    'GENDER': {'Female': 61.4, 'Male': 37.9, 'Non-Binary': 0.4,
               'Trans Female': 0.15, 'Trans Male': 0.15, 'Prefer Not to Say': 0.0},
    'AGE': {'17 and Under': 4.0, '18-24': 22.8, '25-34': 34.1, '35-44': 21.5,
             '45-54': 11.2, '55-64': 4.6, '65 or Older': 1.8},
    'ETHNICITY': {'White': 51.3, 'Hispanic or Latino': 20.6,
                   'Black or African American': 16.4, 'Asian': 8.1,
                   'Another Race/Ethnicity': 3.6},
    'INCOME': {'Less than $25,000': 12.8, '$25,000 - $49,999': 21.4,
                '$50,000 - $74,999': 23.1, '$75,000 - $99,999': 17.6,
                '$100,000 - $149,999': 15.7, '$150,000 - $249,999': 7.6,
                '$250,000 or More': 1.8},
    'EDUCATION': {'High School or Less': 32.4,
                   'Some College / Associate Degree': 24.7,
                   "Bachelor's Degree": 30.1,
                   'Graduate or Professional Degree': 10.8,
                   'Prefer Not to Say': 2.0},
    'RELATIONSHIP': {'Single': 34.2, 'In a Relationship': 24.6, 'Married': 26.8,
                      'Divorced or Separated': 9.6, 'Widowed': 1.4,
                      'Prefer Not to Say': 3.4},
    'PARENTAL_STATUS': {'Has Children': 34.8, 'No Children': 60.2,
                         'Prefer Not to Say': 5.0},
    'OCCUPATION': {'Management, Business & Professional': 22.1,
                    'Healthcare Practitioners or Support': 12.8,
                    'Sales & Retail': 11.4,
                    'Education or Library Services': 9.6,
                    'Service & Hospitality': 8.7,
                    'Science, Technology & Technical Professions': 5.1,
                    'Skilled Trades/Construction or Maintenance': 4.6,
                    'Agriculture & Outdoor': 2.2,
                    'Transportation & Logistics': 3.9,
                    'Manufacturing & Production': 3.4,
                    'Public Safety & Protective Services': 2.5,
                    'Legal': 1.4,
                    'Other': 12.3},
    'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 82.4,
                            'Gay or Lesbian': 9.6,
                            'Another Sexual Orientation': 3.8,
                            'Prefer Not to Say': 4.2},
}

_KEYWORD_TILTS = {
    'werewolf':    {'AGE.18-24': +6, 'AGE.25-34': +2, 'AGE.55-64': -3, 'AGE.65 or Older': -2,
                     'GENDER.Female': +5, 'GENDER.Male': -4},
    'vampire':     {'AGE.18-24': +5, 'AGE.55-64': -2, 'GENDER.Female': +4},
    'billionaire': {'AGE.25-34': +4, 'AGE.35-44': +3, 'GENDER.Female': +6,
                     'INCOME.$100,000 - $149,999': +3},
    'ceo':         {'AGE.25-34': +3, 'AGE.35-44': +3, 'GENDER.Female': +5,
                     'EDUCATION.Bachelor\'s Degree': +3},
    'mafia':       {'AGE.18-24': +4, 'AGE.55-64': -3, 'GENDER.Male': +3},
    'revenge':     {'AGE.25-34': +3, 'GENDER.Female': +3,
                     'RELATIONSHIP.Divorced or Separated': +5},
    'second chance': {'AGE.35-44': +5, 'AGE.45-54': +3,
                       'RELATIONSHIP.Divorced or Separated': +6,
                       'PARENTAL_STATUS.Has Children': +4},
    'bride':       {'GENDER.Female': +7, 'AGE.18-24': +4},
    'wife':        {'GENDER.Female': +6, 'AGE.35-44': +3},
    'sports':      {'GENDER.Male': +9, 'AGE.18-24': +3},
    'cop':         {'GENDER.Male': +5, 'AGE.35-44': +3},
    'assassin':    {'GENDER.Male': +5, 'AGE.18-24': +3},
    'stepbrother': {'AGE.18-24': +8, 'GENDER.Female': +6},
    'stepsister':  {'AGE.18-24': +8, 'GENDER.Female': +6},
}

_BASE_INTERESTS = [
    {'label': 'BookTok / romance novels',       'index': 168},
    {'label': 'Reality dating shows',           'index': 172},
    {'label': 'Beauty & skincare',              'index': 156},
    {'label': 'Vertical short-form video',      'index': 214},
    {'label': 'Celebrity gossip',               'index': 148},
    {'label': 'K-drama / anime fandom',         'index': 137},
    {'label': 'Fast casual dining',             'index': 131},
    {'label': 'Streaming subscriptions (SVOD)', 'index': 128},
]

_BASE_PLATFORMS = [
    {'label': 'TikTok',            'reach_pct': 84.6},
    {'label': 'Instagram Reels',   'reach_pct': 78.3},
    {'label': 'YouTube Shorts',    'reach_pct': 71.9},
    {'label': 'Snapchat Spotlight','reach_pct': 44.2},
    {'label': 'Facebook',          'reach_pct': 38.7},
    {'label': 'Pinterest',         'reach_pct': 31.5},
    {'label': 'Reddit',            'reach_pct': 22.4},
    {'label': 'X (Twitter)',       'reach_pct': 18.9},
]


def _heuristic_audience(title: str, series: str, genre: str, platform: str) -> dict:
    hay = (title + ' ' + (series or '') + ' ' + (genre or '')).lower()
    tilts: dict[str, float] = {}
    for kw, delta in _KEYWORD_TILTS.items():
        if kw in hay:
            for k, v in delta.items():
                tilts[k] = tilts.get(k, 0) + v

    # Return uppercase-keyed demographics so _shape_agent_payload's
    # normalization path handles heuristic + Claude identically.
    demographics: dict[str, dict] = {}
    for cat in DEMO_ORDER:
        vals = dict(_BASE_DISTRIBUTION[cat])
        for k, v in tilts.items():
            if not k.startswith(cat + '.'):
                continue
            label = k.split('.', 1)[1]
            if label in vals:
                vals[label] = max(0.0, vals[label] + v)
        demographics[cat] = vals

    # Interest tilt: bump BookTok/dating/beauty for female-skew titles, sports/UFC-like for male
    interests = [dict(x) for x in _BASE_INTERESTS]
    if any(k.startswith('GENDER.Male') and v > 0 for k, v in tilts.items()):
        interests.append({'label': 'Sports betting', 'index': 148})
        interests.append({'label': 'Combat sports / MMA', 'index': 156})

    return {
        'audience_summary': (
            f'{title} draws the core vertical-drama audience: female-skewing, '
            f'concentrated 18-34, high mobile-first consumption. '
            + (f'The {genre} genre tilt pulls this cut toward the top of that curve.'
                if genre else '')
        ).strip(),
        'demographics':         demographics,
        'interests':            interests[:8],
        'platform_affinities':  _BASE_PLATFORMS,
    }


# ============================================================================
# Top-level entrypoint
# ============================================================================
def research_title_audience(title: str,
                             *,
                             series: str = '',
                             genre: str = '',
                             platform: str = '',
                             force_refresh: bool = False) -> dict:
    """Research the audience for one microdrama title. Returns a
    payload shaped for the frontend modal:

        {
          success, title, series, genre, platform,
          audience_summary,
          demographics: { gender:[{label,pct},...], age:[...], ... },
          demo_order:   ['gender','age', ...],
          demo_labels:  {'gender': 'Gender', ...},
          interests:    [{label, index}, ...],
          platform_affinities: [{label, reach_pct}, ...],
          source:       'claude' | 'heuristic',
          generated_at: ISO8601,
        }
    """
    title = (title or '').strip()
    if not title:
        return {'success': False, 'error': 'title is required'}

    key = _cache_key(title, series, platform)
    if not force_refresh:
        cached = _read_cache(key)
        if cached:
            cached['from_cache'] = True
            return cached

    claude_messages = _load_claude_client()
    raw: Optional[dict] = None
    source = 'heuristic'

    if claude_messages is not None:
        try:
            resp = claude_messages(
                system=_SYSTEM_PROMPT,
                user=_build_user_prompt(title, series, genre, platform),
                model=CLAUDE_MODEL,
                max_tokens=4096,
                temperature=0.3,
            )
            parsed = _extract_json_block(resp)
            if parsed:
                raw = parsed
                source = 'claude'
        except Exception as e:
            logger.warning('microdramas_audience_agent: Claude call failed for %s (%s)',
                            title, e)

    if raw is None:
        raw = _heuristic_audience(title, series, genre, platform)

    payload = _shape_agent_payload(raw, title, series, genre, platform, source)

    try:
        _write_cache(key, payload)
    except Exception:
        pass  # cache write failures never break the response

    return payload
