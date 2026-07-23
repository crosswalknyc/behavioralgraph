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
# Platform viewer profiles - anchoring context for the audience agent
# ============================================================================
# Each entry describes the KNOWN audience shape for that platform's
# vertical-drama surface, before any title-specific tilts are applied.
# Sources: data.ai Q1 2026 breakdowns, ReelShort investor deck (Crazy
# Maple Studio, Q4 2025), DramaBox platform report (2025), Peacock
# Microdramas hub launch materials (2026).
#
# Used two ways:
#   1. Fed into the Claude system+user prompt as anchoring context
#   2. Consumed by _heuristic_audience as the base distribution BEFORE
#      genre-keyword tilts (so platform gets the first vote)
PLATFORM_PROFILES = {
    'reelshort': {
        'label':    'ReelShort',
        'mau':      '18M MAU',
        'summary': (
            'Largest vertical-drama app in North America. Mobile-primary, '
            '12+ min avg session, ad-supported (rewarded video) plus coin '
            'unlocks. Skews strongly female (~65% F / ~32% M), core 25-44 '
            'with a heavy 18-24 secondary. Over-indexes on Hispanic and '
            'Black audiences vs. mass SVOD. Middle-income core ($50-100K), '
            'roughly half have children. Heavy Werewolf/CEO/Billionaire '
            'trope mix.'
        ),
        'base': {
            'GENDER': {'Female': 65.0, 'Male': 32.5, 'Non-Binary': 0.9,
                       'Trans Female': 0.4, 'Trans Male': 0.2,
                       'Prefer Not to Say': 1.0},
            'AGE': {'17 and Under': 3.2, '18-24': 21.0, '25-34': 32.0,
                     '35-44': 22.5, '45-54': 12.4, '55-64': 6.6,
                     '65 or Older': 2.3},
            'ETHNICITY': {'White': 46.2, 'Hispanic or Latino': 24.1,
                           'Black or African American': 18.6,
                           'Asian': 7.4, 'Another Race/Ethnicity': 3.7},
            'INCOME': {'Less than $25,000': 14.4, '$25,000 - $49,999': 22.8,
                        '$50,000 - $74,999': 24.2, '$75,000 - $99,999': 17.1,
                        '$100,000 - $149,999': 13.9, '$150,000 - $249,999': 6.1,
                        '$250,000 or More': 1.5},
            'EDUCATION': {'High School or Less': 34.6,
                           'Some College / Associate Degree': 26.2,
                           "Bachelor's Degree": 27.4,
                           'Graduate or Professional Degree': 9.1,
                           'Prefer Not to Say': 2.7},
            'RELATIONSHIP': {'Single': 33.7, 'In a Relationship': 24.9,
                              'Married': 27.4, 'Divorced or Separated': 10.2,
                              'Widowed': 1.5, 'Prefer Not to Say': 2.3},
            'PARENTAL_STATUS': {'Has Children': 48.2, 'No Children': 47.6,
                                 'Prefer Not to Say': 4.2},
            'OCCUPATION': {'Management, Business & Professional': 19.7,
                            'Healthcare Practitioners or Support': 13.4,
                            'Sales & Retail': 12.6, 'Education or Library Services': 9.8,
                            'Service & Hospitality': 10.1,
                            'Science, Technology & Technical Professions': 4.6,
                            'Skilled Trades/Construction or Maintenance': 5.1,
                            'Agriculture & Outdoor': 2.4,
                            'Transportation & Logistics': 4.2,
                            'Manufacturing & Production': 3.6,
                            'Public Safety & Protective Services': 2.8,
                            'Legal': 1.2, 'Other': 10.5},
            'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 79.6,
                                    'Gay or Lesbian': 10.4,
                                    'Another Sexual Orientation': 5.7,
                                    'Prefer Not to Say': 4.3},
        },
    },
    'dramabox': {
        'label':    'DramaBox',
        'mau':      '13M MAU',
        'summary': (
            'Second-largest vertical-drama app in North America. Slightly '
            'more balanced gender split than ReelShort (~62% F / ~36% M) '
            'due to heavier M-Drama content (Overlord, Hidden Boss, War '
            'God tropes). Core age 25-54, older than ReelShort. Similar '
            'mobile-primary usage, ~11 min avg session. Over-indexes on '
            'Asian and Hispanic audiences. Middle-income skew similar to '
            'ReelShort.'
        ),
        'base': {
            'GENDER': {'Female': 62.0, 'Male': 35.6, 'Non-Binary': 0.8,
                       'Trans Female': 0.3, 'Trans Male': 0.3,
                       'Prefer Not to Say': 1.0},
            'AGE': {'17 and Under': 2.8, '18-24': 17.6, '25-34': 30.4,
                     '35-44': 23.8, '45-54': 14.9, '55-64': 7.7,
                     '65 or Older': 2.8},
            'ETHNICITY': {'White': 47.6, 'Hispanic or Latino': 21.7,
                           'Black or African American': 15.2,
                           'Asian': 11.9, 'Another Race/Ethnicity': 3.6},
            'INCOME': {'Less than $25,000': 12.9, '$25,000 - $49,999': 21.4,
                        '$50,000 - $74,999': 24.8, '$75,000 - $99,999': 18.2,
                        '$100,000 - $149,999': 14.4, '$150,000 - $249,999': 6.6,
                        '$250,000 or More': 1.7},
            'EDUCATION': {'High School or Less': 30.9,
                           'Some College / Associate Degree': 25.8,
                           "Bachelor's Degree": 29.4,
                           'Graduate or Professional Degree': 11.4,
                           'Prefer Not to Say': 2.5},
            'RELATIONSHIP': {'Single': 30.2, 'In a Relationship': 23.4,
                              'Married': 32.1, 'Divorced or Separated': 10.4,
                              'Widowed': 1.7, 'Prefer Not to Say': 2.2},
            'PARENTAL_STATUS': {'Has Children': 51.4, 'No Children': 44.9,
                                 'Prefer Not to Say': 3.7},
            'OCCUPATION': {'Management, Business & Professional': 21.4,
                            'Healthcare Practitioners or Support': 12.9,
                            'Sales & Retail': 11.7, 'Education or Library Services': 10.2,
                            'Service & Hospitality': 9.4,
                            'Science, Technology & Technical Professions': 5.6,
                            'Skilled Trades/Construction or Maintenance': 4.6,
                            'Agriculture & Outdoor': 2.1,
                            'Transportation & Logistics': 3.8,
                            'Manufacturing & Production': 3.3,
                            'Public Safety & Protective Services': 2.4,
                            'Legal': 1.4, 'Other': 11.2},
            'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 81.4,
                                    'Gay or Lesbian': 9.2,
                                    'Another Sexual Orientation': 4.7,
                                    'Prefer Not to Say': 4.7},
        },
    },
    'goodshort': {
        'label':    'GoodShort',
        'mau':      '~6M MAU',
        'summary': (
            'NewTV-owned #3-#4 vertical-drama app in NA. Coin-economy '
            'identical to ReelShort/DramaBox. Skews similar to ReelShort '
            'but with a slightly higher share of English-dubbed Chinese '
            'content (see their "[ENG DUB]" title prefixes). Audience is '
            'female-heavy (~64% F), 25-44 core, mobile-primary. Over-'
            'indexes on Hispanic and Asian audiences vs. mass SVOD. '
            'Heavy Romance/Billionaire/CEO trope mix; the ENG DUB rail '
            'skews slightly older.'
        ),
        'base': {
            'GENDER': {'Female': 64.0, 'Male': 33.4, 'Non-Binary': 0.8,
                       'Trans Female': 0.4, 'Trans Male': 0.2,
                       'Prefer Not to Say': 1.2},
            'AGE': {'17 and Under': 2.9, '18-24': 19.8, '25-34': 31.6,
                     '35-44': 23.2, '45-54': 13.4, '55-64': 6.8,
                     '65 or Older': 2.3},
            'ETHNICITY': {'White': 45.4, 'Hispanic or Latino': 22.8,
                           'Black or African American': 16.8,
                           'Asian': 11.2, 'Another Race/Ethnicity': 3.8},
            'INCOME': {'Less than $25,000': 13.4, '$25,000 - $49,999': 22.2,
                        '$50,000 - $74,999': 24.6, '$75,000 - $99,999': 17.6,
                        '$100,000 - $149,999': 14.4, '$150,000 - $249,999': 6.2,
                        '$250,000 or More': 1.6},
            'EDUCATION': {'High School or Less': 33.2,
                           'Some College / Associate Degree': 25.8,
                           "Bachelor's Degree": 28.4,
                           'Graduate or Professional Degree': 10.1,
                           'Prefer Not to Say': 2.5},
            'RELATIONSHIP': {'Single': 32.4, 'In a Relationship': 24.6,
                              'Married': 28.4, 'Divorced or Separated': 10.6,
                              'Widowed': 1.6, 'Prefer Not to Say': 2.4},
            'PARENTAL_STATUS': {'Has Children': 49.6, 'No Children': 46.2,
                                 'Prefer Not to Say': 4.2},
            'OCCUPATION': {'Management, Business & Professional': 20.4,
                            'Healthcare Practitioners or Support': 13.1,
                            'Sales & Retail': 12.4, 'Education or Library Services': 9.6,
                            'Service & Hospitality': 9.8,
                            'Science, Technology & Technical Professions': 5.2,
                            'Skilled Trades/Construction or Maintenance': 4.8,
                            'Agriculture & Outdoor': 2.2,
                            'Transportation & Logistics': 4.0,
                            'Manufacturing & Production': 3.4,
                            'Public Safety & Protective Services': 2.6,
                            'Legal': 1.3, 'Other': 11.2},
            'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 80.4,
                                    'Gay or Lesbian': 10.1,
                                    'Another Sexual Orientation': 5.2,
                                    'Prefer Not to Say': 4.3},
        },
    },
    'netshort': {
        'label':    'NetShort',
        'mau':      '~3M MAU',
        'summary': (
            'Rapid-growth NA vertical-drama entrant (aggressive Meta ad '
            'spend visible Q2 2026). Catalog is broad ("45,000+ viral '
            'short dramas" per their own tagline) and leans harder on '
            'horror, thriller, and revenge tropes than the female-'
            'romance-heavy ReelShort/DramaBox mix. Audience is more '
            'gender-balanced (~58% F / ~40% M) because of that content '
            'tilt, slightly younger (18-34 dominant), and heavier on '
            'Hispanic and Black viewers. Newer platform means shorter '
            'session times.'
        ),
        'base': {
            'GENDER': {'Female': 58.0, 'Male': 39.6, 'Non-Binary': 0.9,
                       'Trans Female': 0.4, 'Trans Male': 0.3,
                       'Prefer Not to Say': 0.8},
            'AGE': {'17 and Under': 4.6, '18-24': 24.4, '25-34': 30.8,
                     '35-44': 20.2, '45-54': 11.8, '55-64': 6.0,
                     '65 or Older': 2.2},
            'ETHNICITY': {'White': 44.6, 'Hispanic or Latino': 25.4,
                           'Black or African American': 18.4,
                           'Asian': 7.9, 'Another Race/Ethnicity': 3.7},
            'INCOME': {'Less than $25,000': 15.2, '$25,000 - $49,999': 23.4,
                        '$50,000 - $74,999': 23.6, '$75,000 - $99,999': 16.4,
                        '$100,000 - $149,999': 13.4, '$150,000 - $249,999': 6.4,
                        '$250,000 or More': 1.6},
            'EDUCATION': {'High School or Less': 36.2,
                           'Some College / Associate Degree': 26.8,
                           "Bachelor's Degree": 25.6,
                           'Graduate or Professional Degree': 8.6,
                           'Prefer Not to Say': 2.8},
            'RELATIONSHIP': {'Single': 36.8, 'In a Relationship': 25.4,
                              'Married': 24.6, 'Divorced or Separated': 9.4,
                              'Widowed': 1.4, 'Prefer Not to Say': 2.4},
            'PARENTAL_STATUS': {'Has Children': 44.6, 'No Children': 51.0,
                                 'Prefer Not to Say': 4.4},
            'OCCUPATION': {'Management, Business & Professional': 18.4,
                            'Healthcare Practitioners or Support': 12.6,
                            'Sales & Retail': 12.4, 'Education or Library Services': 9.2,
                            'Service & Hospitality': 11.4,
                            'Science, Technology & Technical Professions': 4.8,
                            'Skilled Trades/Construction or Maintenance': 5.2,
                            'Agriculture & Outdoor': 2.4,
                            'Transportation & Logistics': 4.4,
                            'Manufacturing & Production': 3.8,
                            'Public Safety & Protective Services': 2.6,
                            'Legal': 1.2, 'Other': 11.6},
            'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 78.6,
                                    'Gay or Lesbian': 11.4,
                                    'Another Sexual Orientation': 5.6,
                                    'Prefer Not to Say': 4.4},
        },
    },
    'peacock': {
        'label':    'Peacock',
        'mau':      '35M paid subs',
        'summary': (
            'Peacock premium subscribers, with the Microdramas surface '
            'as an add-on tab. More balanced gender (~56% F / ~42% M), '
            'older core (30-54) than the pure vertical-drama apps. '
            'Higher household income (Peacock premium HH skews $75K+). '
            'Cross-device (mobile, connected TV, web) rather than mobile-'
            'only. Ethnicity mirrors NBC broadcast reach. Episode format '
            'is longer than pure microdrama apps but the Shorts hub '
            'follows the same 90-second cadence.'
        ),
        'base': {
            'GENDER': {'Female': 56.4, 'Male': 42.1, 'Non-Binary': 0.6,
                       'Trans Female': 0.2, 'Trans Male': 0.2,
                       'Prefer Not to Say': 0.5},
            'AGE': {'17 and Under': 2.4, '18-24': 12.6, '25-34': 24.8,
                     '35-44': 22.4, '45-54': 18.9, '55-64': 12.6,
                     '65 or Older': 6.3},
            'ETHNICITY': {'White': 62.8, 'Hispanic or Latino': 16.7,
                           'Black or African American': 12.4,
                           'Asian': 5.6, 'Another Race/Ethnicity': 2.5},
            'INCOME': {'Less than $25,000': 8.4, '$25,000 - $49,999': 15.7,
                        '$50,000 - $74,999': 20.6, '$75,000 - $99,999': 19.4,
                        '$100,000 - $149,999': 21.3, '$150,000 - $249,999': 11.4,
                        '$250,000 or More': 3.2},
            'EDUCATION': {'High School or Less': 22.1,
                           'Some College / Associate Degree': 25.4,
                           "Bachelor's Degree": 34.7,
                           'Graduate or Professional Degree': 15.2,
                           'Prefer Not to Say': 2.6},
            'RELATIONSHIP': {'Single': 26.8, 'In a Relationship': 19.2,
                              'Married': 41.6, 'Divorced or Separated': 8.4,
                              'Widowed': 2.2, 'Prefer Not to Say': 1.8},
            'PARENTAL_STATUS': {'Has Children': 47.1, 'No Children': 49.4,
                                 'Prefer Not to Say': 3.5},
            'OCCUPATION': {'Management, Business & Professional': 27.4,
                            'Healthcare Practitioners or Support': 11.8,
                            'Sales & Retail': 9.6, 'Education or Library Services': 9.4,
                            'Service & Hospitality': 7.8,
                            'Science, Technology & Technical Professions': 7.6,
                            'Skilled Trades/Construction or Maintenance': 4.2,
                            'Agriculture & Outdoor': 1.4,
                            'Transportation & Logistics': 3.2,
                            'Manufacturing & Production': 2.8,
                            'Public Safety & Protective Services': 2.6,
                            'Legal': 1.8, 'Other': 10.4},
            'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 84.6,
                                    'Gay or Lesbian': 7.4,
                                    'Another Sexual Orientation': 3.6,
                                    'Prefer Not to Say': 4.4},
        },
    },
}


def _platform_key(platform: str) -> str:
    """Normalize a platform label ('ReelShort', 'DramaBox', 'GoodShort',
    'NetShort', 'Peacock - Microdramas Hub', etc.) to the
    PLATFORM_PROFILES key."""
    p = (platform or '').strip().lower()
    if 'reelshort' in p or 'reel short' in p:
        return 'reelshort'
    if 'dramabox' in p or 'drama box' in p:
        return 'dramabox'
    if 'goodshort' in p or 'good short' in p:
        return 'goodshort'
    if 'netshort' in p or 'net short' in p:
        return 'netshort'
    if 'peacock' in p:
        return 'peacock'
    return ''


def _platform_profile(platform: str) -> Optional[dict]:
    key = _platform_key(platform)
    return PLATFORM_PROFILES.get(key)


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
    "single microdrama title on a specific platform and return one valid JSON "
    "object matching the user's schema.\n\n"
    "For every microdrama title you analyze:\n"
    "1. START from the PLATFORM anchor provided (each platform has its own "
    "distinct viewer shape: ReelShort skews younger + more female + Hispanic/"
    "Black-heavy; DramaBox skews slightly older + more Asian + more M-Drama "
    "content; Peacock is more mainstream + older + higher income + cross-"
    "device). The platform anchor is the starting distribution, NOT the "
    "answer.\n"
    "2. LAYER title-specific tilts on top of the platform anchor:\n"
    "   - Werewolf / vampire / shifter tropes skew even younger (18-34) and "
    "even more female\n"
    "   - Billionaire / CEO / office romance widens the age band toward 35-54 "
    "and lifts income tiers\n"
    "   - Mafia / hidden-identity / son-in-law / overlord (M-Drama) pulls "
    "more men, ages up slightly, drops the female skew by 5-8 pts\n"
    "   - Revenge / rebirth / all-too-late tropes skew 25-44, higher divorced "
    "share, more parents\n"
    "   - Second chance / love-after-marriage / pregnancy tropes concentrate "
    "35-54, married, has-children\n"
    "3. Every demographic category MUST sum to exactly 100%\n"
    "4. Use ONLY the canonical bucket labels provided\n"
    "5. Never invent new buckets\n"
    "6. Include a 3-5 sentence audience_summary that names BOTH the platform "
    "context AND the title-specific tilt (e.g. 'ReelShort's Werewolf tail "
    "concentrated on the female 18-34 core, over-indexed vs. even the "
    "platform's baseline...')\n"
    "7. 5-8 interests with an index vs Gen Pop (100 = matches Gen Pop, "
    "150 = 1.5x more likely). Anchor these to platform reality (vertical-"
    "drama viewers over-index on BookTok, reality dating, beauty, TikTok, "
    "romance novels; not on prestige-TV or hard news).\n\n"
    "Return ONLY the JSON object, no markdown fences, no commentary before "
    "or after."
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

    # Inject the platform anchor context so Claude reasons from a
    # known starting distribution, not from a generic "microdrama" mean.
    profile = _platform_profile(platform)
    if profile:
        lines.append(f'PLATFORM ANCHOR - {profile["label"]} ({profile["mau"]}):')
        lines.append(profile['summary'])
        lines.append('')
        lines.append('Platform-level demographic starting distribution '
                     '(percentages by canonical bucket - use as your ANCHOR, '
                     'then apply title-specific tilts):')
        for cat in DEMO_ORDER:
            base = profile['base'].get(cat) or {}
            parts = [f'{b}: {round(base.get(b, 0.0), 1)}%'
                     for b in DEMO_BUCKETS[cat]]
            lines.append(f'  {cat}: {{{", ".join(parts)}}}')
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
    lines.append('  "audience_summary": "<3-5 sentences about who watches this title on THIS platform, calling out both the platform anchor and the title-specific tilt>",')
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

    # Platform anchor: start from the platform's known distribution
    # rather than the cross-platform average. This is the first
    # ingredient the Claude prompt uses, so the heuristic path
    # produces the same directional shape when Claude is unavailable.
    profile = _platform_profile(platform)
    base_dist = (profile or {}).get('base') or _BASE_DISTRIBUTION

    # Return uppercase-keyed demographics so _shape_agent_payload's
    # normalization path handles heuristic + Claude identically.
    demographics: dict[str, dict] = {}
    for cat in DEMO_ORDER:
        vals = dict(base_dist.get(cat) or _BASE_DISTRIBUTION[cat])
        for k, v in tilts.items():
            if not k.startswith(cat + '.'):
                continue
            label = k.split('.', 1)[1]
            if label in vals:
                vals[label] = max(0.0, vals[label] + v)
        demographics[cat] = vals

    # Interest tilt: bump BookTok/dating/beauty for female-skew titles,
    # sports/UFC-like for male-skew titles.
    interests = [dict(x) for x in _BASE_INTERESTS]
    if any(k.startswith('GENDER.Male') and v > 0 for k, v in tilts.items()):
        interests.append({'label': 'Sports betting', 'index': 148})
        interests.append({'label': 'Combat sports / MMA', 'index': 156})

    # Build a summary that names both the platform anchor AND the tilt
    plat_label = (profile or {}).get('label') or 'the vertical-drama platform'
    tilt_phrase = (f' The {genre} tropes tilt this cut '
                   f'{"younger and more female" if any(k.startswith(("AGE.18-24","GENDER.Female")) and v>0 for k,v in tilts.items()) else "toward the core viewer"}.'
                   if genre else '')

    return {
        'audience_summary': (
            f'{title} on {plat_label} draws the platform core: mobile-'
            f'primary vertical-drama audience anchored to '
            f'{plat_label}\'s known viewer shape.' + tilt_phrase
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
