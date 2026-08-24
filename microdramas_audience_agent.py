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

# Canonical Gen Pop CSV in the dashboard S3 bucket. Same file the
# BG.py pipeline reads for its own Gen Pop calibration (see bg.py
# GEN_POP_CANONICAL_KEY). Falling back to the local repo copy lets
# this module work in dev without S3 credentials.
GEN_POP_S3_KEY   = 'Gen_Pop_2026.csv'
GEN_POP_LOCAL    = os.environ.get('GEN_POP_LOCAL_PATH') or ''


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
        "Bachelors Degree", 'Graduate or Professional Degree',
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
# Hostmap gating for interests (workspace rule #4).
# ============================================================================
# Jenna 2026-08-19: "make sure the interests listed are only from the
# ones that are canonical in the hostmap file."
#
# Every interest label surfaced on a microdrama title card MUST be a
# real brand in reference.host_mapping (never a made-up topic string
# like "BookTok / romance novels" or "Reality dating shows"). Both
# the Claude agent and the heuristic fallback pull from the same
# curated shortlist below, and any label that fails the hostmap gate
# is dropped from the payload before it reaches the frontend.
_HOSTMAP_INTEREST_NORM: Optional[set] = None
_HOSTMAP_INTEREST_UPPER: Optional[set] = None
_HOSTMAP_INTEREST_CANONICAL: Optional[dict] = None
_HOSTMAP_HIDDEN_UPPER: Optional[set] = None


def _norm_brand(s) -> str:
    """Case + punctuation insensitive brand key (matches the workspace
    rule 'Duplicate check is case + punctuation insensitive')."""
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _load_hostmap_brands() -> bool:
    """Load the canonical hostmap brand list once per process. Mirrors
    the pattern in migration/post_generation_enforcers._ensure_hostmap_loaded.

    Populates:
      _HOSTMAP_INTEREST_UPPER      punct-sensitive upper-case set
      _HOSTMAP_INTEREST_NORM       punct-insensitive normalized set
      _HOSTMAP_INTEREST_CANONICAL  upper-key -> original casing
      _HOSTMAP_HIDDEN_UPPER        SECTION='Hidden' upper-case set

    Returns True on success; False (open-mode: caller decides) if no
    cache is available.
    """
    global _HOSTMAP_INTEREST_NORM, _HOSTMAP_INTEREST_UPPER
    global _HOSTMAP_INTEREST_CANONICAL, _HOSTMAP_HIDDEN_UPPER
    if _HOSTMAP_INTEREST_NORM is not None:
        return True
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, '..', 'reference',
                                       'hostmap_brands_canonical.txt')),
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands_canonical.txt',
        '/root/finished_codes/reference/hostmap_brands_canonical.txt',
        os.path.abspath(os.path.join(here, '..', 'reference',
                                       'hostmap_brands.txt')),
        '/tmp/hostmap_brands.txt',
    ]
    hidden_candidates = [
        os.path.abspath(os.path.join(here, '..', 'reference',
                                       'hostmap_hidden_brands.txt')),
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_hidden_brands.txt',
        '/root/finished_codes/reference/hostmap_hidden_brands.txt',
    ]
    lines: list[str] = []
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
                break
            except Exception as e:
                logger.info('microdramas_audience_agent: hostmap read failed at %s (%s)', p, e)
    if not lines:
        return False
    hidden: set = set()
    for p in hidden_candidates:
        if p and os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    hidden = {ln.strip().upper() for ln in f if ln.strip()}
                break
            except Exception:
                continue
    _HOSTMAP_INTEREST_UPPER    = {b.upper() for b in lines}
    _HOSTMAP_INTEREST_NORM     = {_norm_brand(b) for b in lines}
    canon: dict = {}
    for b in lines:
        uk = b.upper()
        prev = canon.get(uk)
        # Prefer non-Hidden over Hidden, then Title-case over ALL CAPS
        prev_hidden = prev is not None and prev.upper() in hidden
        new_hidden  = uk in hidden
        if prev is None:
            canon[uk] = b
        elif prev_hidden and not new_hidden:
            canon[uk] = b
        elif not prev_hidden and new_hidden:
            pass
        elif prev.isupper() and not b.isupper():
            canon[uk] = b
    _HOSTMAP_INTEREST_CANONICAL = canon
    _HOSTMAP_HIDDEN_UPPER       = hidden
    return True


def _is_in_hostmap(brand: str) -> bool:
    """Case + punctuation-insensitive hostmap membership check. Never
    matches a brand whose SECTION='Hidden' (per workspace rule #4b).
    Returns True permissively when the hostmap cache is unavailable so
    dev environments without the reference file don't drop every row."""
    if not _load_hostmap_brands():
        return True
    if not brand:
        return False
    bu = str(brand).upper()
    if bu in (_HOSTMAP_HIDDEN_UPPER or set()):
        return False
    if bu in (_HOSTMAP_INTEREST_UPPER or set()):
        return True
    return _norm_brand(brand) in (_HOSTMAP_INTEREST_NORM or set())


def _hostmap_canonical(brand: str) -> Optional[str]:
    """Return the hostmap's canonical casing for a brand, or None if
    it isn't in hostmap (or is Hidden). Guarantees the label rendered
    on the dashboard uses the exact spelling from the source of truth."""
    if not _load_hostmap_brands():
        return brand  # open-mode fallback for dev
    if not brand:
        return None
    bu = str(brand).upper()
    if bu in (_HOSTMAP_HIDDEN_UPPER or set()):
        return None
    canon = _HOSTMAP_INTEREST_CANONICAL or {}
    if bu in canon:
        return canon[bu]
    # Punctuation-stripped fallback for typos like "Coca Cola" -> "Coca-Cola"
    nb = _norm_brand(brand)
    for k, v in canon.items():
        if _norm_brand(k) == nb:
            return v
    return None


# ============================================================================
# Canonical interest menu (hostmap-gated)
# ============================================================================
# The single source of truth for every interest label rendered on any
# microdrama title card. Each entry is a real brand in
# reference.host_mapping (verified at module import via
# _validate_interest_menu below). Both the Claude agent and the
# heuristic fallback draw from this menu; anything they return that
# isn't on the menu (or later fails the hostmap gate at post-shape
# time) is dropped from the payload before it reaches the frontend.
#
# Fields:
#   label      exact canonical hostmap casing (rendered as-is)
#   base       cross-audience index vs Gen Pop (100 = parity)
#   tilts      set of tilt tags that BOOST this brand's index when
#              the title/audience matches. Tags:
#                'female'          female-skew title/audience
#                'male'            male-skew title/audience
#                'young'           heavy 18-24
#                'core'            heavy 25-44
#                'older'           heavy 45+
#                'romance'         romance/CEO/billionaire/bride/wife
#                'mdrama'          M-Drama / mafia / overlord / assassin
#                'revenge'         revenge / rebirth
#                'family'          second-chance / has-children
#                'higher_income'   $100K+ index skew
#                'lower_income'    <$50K index skew
#
# Never add a brand here that isn't in hostmap. The
# _validate_interest_menu() call at module import will log a warning
# and drop any entry that fails the gate so a stale menu can't leak
# a non-canonical label onto the dashboard.
HOSTMAP_INTEREST_MENU: list[dict] = [
    # Social / short-form video (universal core for microdrama viewers)
    {'label': 'TikTok',            'base': 205, 'tilts': {'young', 'female'}},
    {'label': 'Instagram',         'base': 178, 'tilts': {'female', 'young'}},
    {'label': 'YouTube',           'base': 154, 'tilts': set()},
    {'label': 'Snapchat',          'base': 148, 'tilts': {'young'}},
    {'label': 'Facebook',          'base': 132, 'tilts': {'older', 'family'}},
    {'label': 'Pinterest',         'base': 141, 'tilts': {'female', 'romance'}},
    {'label': 'Reddit',            'base': 118, 'tilts': {'male', 'mdrama'}},
    {'label': 'Threads',           'base': 108, 'tilts': {'young'}},
    {'label': 'BeReal',            'base': 116, 'tilts': {'young'}},

    # Reading / audio (BookTok crossover, romance-novel adjacency).
    # Wattpad is in hostmap but SECTION='Hidden' (workspace rule #4b)
    # so it never ships - Kindle + Audible + Barnes & Noble carry the
    # romance-reader signal instead.
    {'label': 'Kindle',            'base': 168, 'tilts': {'female', 'romance', 'young'}},
    {'label': 'Audible',           'base': 152, 'tilts': {'female', 'romance', 'core'}},
    {'label': 'Barnes & Noble',    'base': 138, 'tilts': {'female', 'romance', 'higher_income'}},

    # Dating (romance-trope audience over-indexes)
    {'label': 'Bumble',            'base': 168, 'tilts': {'female', 'young', 'romance'}},
    {'label': 'Hinge',             'base': 152, 'tilts': {'female', 'core', 'romance'}},

    # Beauty (female-skew core, higher for younger + romance tropes)
    {'label': 'Sephora',           'base': 174, 'tilts': {'female', 'higher_income'}},
    {'label': 'Ulta Beauty',       'base': 168, 'tilts': {'female'}},
    {'label': 'Fenty Beauty',      'base': 156, 'tilts': {'female', 'young'}},
    {'label': 'Rare Beauty',       'base': 152, 'tilts': {'female', 'young'}},
    # e.l.f. Cosmetics is in hostmap but SECTION='Hidden' - Maybelline
    # and Wet n Wild carry the affordable-drugstore-beauty signal.
    {'label': 'Maybelline',        'base': 148, 'tilts': {'female', 'lower_income'}},
    {'label': 'Wet n Wild',        'base': 138, 'tilts': {'female', 'young', 'lower_income'}},
    {'label': 'Glossier',          'base': 138, 'tilts': {'female', 'young', 'higher_income'}},
    {'label': 'MAC Cosmetics',     'base': 142, 'tilts': {'female'}},
    {'label': 'Milk Makeup',       'base': 132, 'tilts': {'female', 'young'}},
    {'label': 'Colourpop',         'base': 146, 'tilts': {'female', 'young', 'lower_income'}},
    {'label': 'Kylie Cosmetics',   'base': 138, 'tilts': {'female', 'young'}},
    {'label': 'Urban Decay',       'base': 128, 'tilts': {'female', 'young'}},
    {'label': 'Benefit Cosmetics', 'base': 128, 'tilts': {'female'}},
    {'label': 'Bath & Body Works', 'base': 158, 'tilts': {'female'}},

    # Fashion / apparel (romance + young female tilt)
    {'label': 'Shein',             'base': 178, 'tilts': {'female', 'young', 'lower_income'}},
    {'label': 'Boohoo',            'base': 152, 'tilts': {'female', 'young', 'lower_income'}},
    {'label': 'Nasty Gal',         'base': 138, 'tilts': {'female', 'young'}},
    {'label': 'ASOS',              'base': 132, 'tilts': {'female', 'young'}},
    {'label': 'Skims',             'base': 156, 'tilts': {'female', 'young'}},
    {'label': 'Fabletics',         'base': 138, 'tilts': {'female', 'core'}},
    {'label': 'Free People',       'base': 128, 'tilts': {'female', 'higher_income'}},
    {'label': 'Anthropologie',     'base': 124, 'tilts': {'female', 'higher_income', 'older'}},
    {'label': 'Urban Outfitters',  'base': 136, 'tilts': {'female', 'young'}},
    {'label': 'Old Navy',          'base': 118, 'tilts': {'female', 'family'}},
    {'label': 'Poshmark',          'base': 148, 'tilts': {'female'}},
    {'label': 'Etsy',              'base': 138, 'tilts': {'female', 'romance'}},

    # QSR / mobile-first food
    {'label': 'DoorDash',          'base': 152, 'tilts': {'young', 'core'}},
    {'label': 'Uber Eats',         'base': 141, 'tilts': {'young', 'core'}},
    {'label': 'Grubhub',           'base': 118, 'tilts': set()},
    {'label': 'Instacart',         'base': 128, 'tilts': {'family', 'higher_income'}},
    {'label': 'GoPuff',            'base': 132, 'tilts': {'young'}},
    {'label': 'Chick-Fil-A',       'base': 128, 'tilts': {'family'}},
    {'label': 'Wingstop',          'base': 138, 'tilts': {'male', 'young'}},
    {'label': 'Popeyes',           'base': 132, 'tilts': {'male'}},
    {'label': 'Taco Bell',         'base': 138, 'tilts': {'young'}},
    {'label': 'Dunkin',            'base': 118, 'tilts': {'core'}},

    # Streaming (over-index because microdrama viewers are heavy SVOD)
    {'label': 'Netflix',           'base': 168, 'tilts': set()},
    {'label': 'Hulu',              'base': 142, 'tilts': set()},
    {'label': 'Peacock',           'base': 138, 'tilts': {'older'}},
    {'label': 'Amazon Prime Video', 'base': 148, 'tilts': set()},
    {'label': 'Spotify',           'base': 152, 'tilts': {'young'}},
    {'label': 'Amazon Music',      'base': 118, 'tilts': set()},
    {'label': 'Apple Music',       'base': 124, 'tilts': set()},

    # Retail (mobile-shopper crossover)
    {'label': 'Amazon',            'base': 156, 'tilts': set()},
    {'label': 'Target',            'base': 138, 'tilts': {'female', 'family'}},
    {'label': 'Walmart',           'base': 128, 'tilts': {'family', 'lower_income'}},
    {'label': 'Costco',            'base': 118, 'tilts': {'family', 'higher_income'}},
    {'label': 'Kroger',            'base': 108, 'tilts': {'family'}},
    {'label': 'Publix',            'base': 108, 'tilts': {'family'}},
    {'label': 'ALDI',              'base': 118, 'tilts': {'family', 'lower_income'}},

    # Fitness / wellness (aspirational for romance-trope viewers)
    {'label': 'Peloton',           'base': 132, 'tilts': {'higher_income', 'core'}},
    {'label': 'SoulCycle',         'base': 118, 'tilts': {'female', 'higher_income'}},
    {'label': 'Equinox',           'base': 112, 'tilts': {'higher_income'}},
    {'label': 'ClassPass',         'base': 122, 'tilts': {'female', 'core'}},
    {'label': 'Alo Yoga',          'base': 128, 'tilts': {'female', 'higher_income'}},

    # Fin / payments (young mobile-first)
    {'label': 'Cash App',          'base': 148, 'tilts': {'young', 'lower_income'}},
    {'label': 'Venmo',             'base': 138, 'tilts': {'young'}},
    {'label': 'PayPal',            'base': 118, 'tilts': set()},
    {'label': 'Robinhood',         'base': 132, 'tilts': {'male', 'young'}},

    # Games (M-Drama / male-tilt crossover)
    {'label': 'Roblox',            'base': 138, 'tilts': {'young'}},
    {'label': 'Grand Theft Auto',  'base': 132, 'tilts': {'male', 'mdrama'}},
    {'label': 'Marvel',            'base': 128, 'tilts': {'male', 'mdrama'}},

    # Utility / lifestyle
    {'label': 'Duolingo',          'base': 132, 'tilts': {'young'}},
    {'label': 'WhatsApp',          'base': 128, 'tilts': set()},
    {'label': 'Uber',              'base': 128, 'tilts': {'young', 'core'}},
    {'label': 'Lyft',              'base': 118, 'tilts': {'young', 'core'}},
    {'label': 'Airbnb',            'base': 128, 'tilts': {'higher_income', 'romance'}},
    {'label': 'Expedia',           'base': 108, 'tilts': {'higher_income', 'older'}},
    {'label': 'Yelp',              'base': 108, 'tilts': set()},

    # Talent (celebrity-gossip crossover, hostmap Talent brands)
    {'label': 'Kim Kardashian',    'base': 132, 'tilts': {'female'}},
    {'label': 'Kylie Jenner',      'base': 128, 'tilts': {'female', 'young'}},
    {'label': 'Rihanna',           'base': 128, 'tilts': {'female'}},
    {'label': 'Taylor Swift',      'base': 138, 'tilts': {'female'}},
    {'label': 'Ariana Grande',     'base': 122, 'tilts': {'female', 'young'}},
    {'label': 'Doja Cat',          'base': 118, 'tilts': {'young'}},
    {'label': 'Sabrina Carpenter', 'base': 118, 'tilts': {'young', 'female'}},
    {'label': 'Olivia Rodrigo',    'base': 122, 'tilts': {'young', 'female'}},
    {'label': 'Chappell Roan',     'base': 108, 'tilts': {'young', 'female'}},
    {'label': 'Alix Earle',        'base': 128, 'tilts': {'female', 'young'}},
    {'label': 'Emma Chamberlain',  'base': 118, 'tilts': {'female', 'young'}},
]


def _validate_interest_menu() -> None:
    """Drop any menu entry whose label isn't in hostmap at import time.
    Logs a warning so a drift between the menu and the hostmap cache
    surfaces immediately (rather than silently rendering a phantom
    label on the dashboard)."""
    if not _load_hostmap_brands():
        # No hostmap cache locally - keep the menu as-is and let the
        # runtime gate in _shape_agent_payload filter server-side.
        return
    kept: list[dict] = []
    dropped: list[str] = []
    for entry in HOSTMAP_INTEREST_MENU:
        lbl = entry.get('label') or ''
        canon = _hostmap_canonical(lbl)
        if canon:
            entry['label'] = canon
            kept.append(entry)
        else:
            dropped.append(lbl)
    if dropped:
        logger.warning('microdramas_audience_agent: dropped %d non-hostmap '
                       'interest menu entries: %s',
                       len(dropped), ', '.join(dropped[:10]))
    HOSTMAP_INTEREST_MENU[:] = kept


_validate_interest_menu()


def _interests_from_menu(tilts: set, target_n: int = 8) -> list[dict]:
    """Rank the hostmap-gated menu against a set of tilt tags and
    return the top `target_n` as [{label, index}, ...]. Deterministic
    for identical tilt sets so the same title always renders the same
    list. The index nudges +8..+22 for each matching tilt so a brand
    that resonates with three tilt tags will float above one that
    matches only one."""
    if not HOSTMAP_INTEREST_MENU:
        return []
    scored: list[tuple[float, str, dict]] = []
    for entry in HOSTMAP_INTEREST_MENU:
        base = float(entry.get('base') or 100)
        etilts = entry.get('tilts') or set()
        overlap = len(etilts & tilts) if tilts else 0
        # Each tilt-match adds a modest lift; three matches = ~+45 index
        bonus = 15.0 * overlap
        # Ambiguous entries (no tilts) still surface at their base index
        idx = base + bonus
        # Stable tie-break on label so ordering is deterministic
        scored.append((-idx, entry['label'], entry))
    scored.sort()
    out: list[dict] = []
    for _neg_idx, _lbl, entry in scored[:target_n]:
        out.append({
            'label': entry['label'],
            'index': int(round(-_neg_idx)),
        })
    return out


def _tilts_from_context(title: str, series: str, genre: str,
                         platform: str) -> set:
    """Derive tilt tags from a title/genre/platform combo. Mirrors the
    keyword logic used by the heuristic demographic path so the menu
    ranking stays consistent with the demographic tilt."""
    hay = f'{title or ""} {series or ""} {genre or ""}'.lower()
    tilts: set = set()

    # Trope keywords (map to _KEYWORD_TILTS style)
    romance_kw = ('billionaire', 'ceo', 'bride', 'wife', 'werewolf',
                    'vampire', 'stepbrother', 'stepsister', 'second chance',
                    'love', 'kiss', 'wedding', 'pregnan', 'baby')
    mdrama_kw  = ('mafia', 'overlord', 'assassin', 'hidden', 'son-in-law',
                    'god', 'war', 'revenge', 'cop', 'agent', 'boss')
    revenge_kw = ('revenge', 'rebirth', 'reborn', 'too late', 'ivy')
    family_kw  = ('second chance', 'wife', 'husband', 'pregnan', 'baby',
                    'children', 'family')

    if any(k in hay for k in romance_kw): tilts.add('romance'); tilts.add('female')
    if any(k in hay for k in mdrama_kw):  tilts.add('mdrama');  tilts.add('male')
    if any(k in hay for k in revenge_kw): tilts.add('revenge'); tilts.add('core')
    if any(k in hay for k in family_kw):  tilts.add('family');  tilts.add('core')

    # Werewolf / stepbrother / stepsister skew young
    if any(k in hay for k in ('werewolf', 'vampire', 'stepbrother',
                                'stepsister', 'high school')):
        tilts.add('young')

    # Billionaire / CEO widen to older core and higher income
    if any(k in hay for k in ('billionaire', 'ceo', 'boss')):
        tilts.add('higher_income'); tilts.add('core')

    # Platform anchors: Peacock is older / higher income; the coin
    # apps skew young + core
    plat_key = _platform_key(platform) if platform else ''
    if plat_key == 'peacock':
        tilts.add('older'); tilts.add('higher_income')
    elif plat_key in ('reelshort', 'dramabox', 'goodshort', 'netshort'):
        tilts.add('core')
        if plat_key in ('reelshort', 'goodshort'):
            tilts.add('female')
        if plat_key == 'netshort':
            tilts.add('young')  # netshort skews the youngest

    # Every microdrama viewer over-indexes on short-form video and
    # streaming - guarantees TikTok / Netflix / Prime never fall out
    tilts.add('core')

    return tilts


def _gate_interests_to_hostmap(rows: list[dict]) -> list[dict]:
    """Filter a list of {label, index} rows to hostmap-only entries.
    Canonicalizes the label to the hostmap's spelling on the way out
    (so 'Chick fil A' becomes 'Chick-Fil-A', 'coca cola' becomes
    'Coca-Cola', etc.). Any label that fails the hostmap gate is
    dropped with a debug log entry so we can trace what Claude
    hallucinated."""
    out: list[dict] = []
    seen: set = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        lbl = str(r.get('label') or '').strip()
        idx = r.get('index')
        if not lbl or not isinstance(idx, (int, float)):
            continue
        canon = _hostmap_canonical(lbl)
        if not canon:
            logger.debug('microdramas_audience_agent: dropping non-hostmap '
                          'interest "%s"', lbl)
            continue
        key = canon.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append({'label': canon, 'index': int(idx)})
    return out


# ============================================================================
# Gen Pop baseline loader
# ============================================================================
# Every title-audience payload ships a parallel `demographics_genpop`
# block using the exact same bucket labels as `demographics`, so the
# frontend can render a Gen Pop comparison marker on every bar without
# ever fetching a second endpoint.
#
# Source of truth = the same Gen_Pop_2026.csv that BG.py reads (see
# bg.py :: GEN_POP_CANONICAL_KEY). Loaded once per process, cached
# forever - the file only changes when engineering re-uploads it, and
# the web workers restart on every deploy.
#
# Bucket alignment: Gen Pop uses UPPERCASE labels and a slightly
# different bucket set from the agent's canonical list
# (see DEMO_BUCKETS). Mismatches handled explicitly below:
#
#   GENDER:              GP has no "Prefer Not to Say" -> 0
#   EDUCATION:           GP has no "Prefer Not to Say" -> 0
#   RELATIONSHIP:        GP has no "Prefer Not to Say" -> 0
#   OCCUPATION:          GP has SELF-EMPLOYED / STUDENT / HOMEMAKER /
#                        RETIRED / UNEMPLOYED buckets; agent doesn't.
#                        These sit at 0% in the current GP file so
#                        they drop out at normalization time.
#   SEXUAL_ORIENTATION:  GP collapses "Gay or Lesbian" + "Another"
#                        into a single "LGBTQ+" bucket. Split back
#                        into the agent's 2 buckets using a fixed
#                        63/37 ratio (Pew 2023 US LGBTQ+ breakdown:
#                        ~63% identify as gay/lesbian/bisexual with
#                        gay+lesbian dominant vs ~37% other identities).
#
# After mapping, each category is renormalized to sum to 100 via
# `_renormalize_100` (defined further down), so the small buckets
# that drop out don't leave the row summing to 98% or 102%.
_GENPOP_DEMOS: Optional[dict] = None
_GENPOP_LOAD_ATTEMPTED = False

# LGBTQ+ -> (Gay or Lesbian, Another Sexual Orientation) split
_LGBTQ_SPLIT = (0.63, 0.37)


def _norm_label(s) -> str:
    """Case + punctuation-insensitive key for demographic labels."""
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


def _read_genpop_csv_rows() -> Optional[list[dict]]:
    """Load Gen_Pop_2026.csv as a list of dicts. Tries S3 first, then
    the local repo copy if S3 is unavailable. Returns None if neither
    path works."""
    # 1) S3
    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=S3_BUCKET, Key=GEN_POP_S3_KEY)
        raw = obj['Body'].read().decode('utf-8')
        import csv, io
        return list(csv.DictReader(io.StringIO(raw)))
    except Exception as e:
        logger.info('microdramas_audience_agent: Gen Pop S3 read failed (%s), trying local', e)

    # 2) Local repo copy (finished_codes/Gen_Pop_2026.csv, one dir up
    #    from bg-webapp/)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        GEN_POP_LOCAL,
        os.path.join(here, GEN_POP_S3_KEY),
        os.path.abspath(os.path.join(here, '..', GEN_POP_S3_KEY)),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            import csv
            with open(path, 'r', encoding='utf-8') as f:
                return list(csv.DictReader(f))
        except Exception as e:
            logger.info('microdramas_audience_agent: Gen Pop local read failed at %s (%s)',
                        path, e)
    return None


def _parse_bp(bp_str: str) -> float:
    """Parse a "12.3456%" string into a float. Zero on any error."""
    if not bp_str:
        return 0.0
    try:
        return float(str(bp_str).rstrip('%').strip())
    except (ValueError, TypeError):
        return 0.0


def _extract_genpop_bp_by_category(rows: list[dict]) -> dict:
    """Group rows by Column (demographic category) and return
    {CATEGORY: {norm_label: bp}} for every canonical demo category we
    care about."""
    result: dict = {cat: {} for cat in DEMO_ORDER}
    for row in rows:
        col = str(row.get('Column') or '').strip().upper()
        if col not in DEMO_ORDER:
            continue
        val = str(row.get('Value') or '').strip()
        bp  = _parse_bp(row.get('Brand Penetration (Row)') or '')
        if val and bp > 0:
            result[col][_norm_label(val)] = bp
    return result


def _map_genpop_to_agent_buckets(gp_by_cat: dict) -> dict:
    """For each canonical category, walk the agent's bucket list and
    resolve each bucket's Gen Pop pct via label matching + the special
    cases documented at the top of this section."""
    out: dict = {}
    for cat in DEMO_ORDER:
        gp = gp_by_cat.get(cat) or {}
        buckets = DEMO_BUCKETS[cat]

        if cat == 'SEXUAL_ORIENTATION':
            # LGBTQ+ splits between "Gay or Lesbian" and "Another"
            lgbtq_pct = gp.get(_norm_label('LGBTQ+')) or 0.0
            straight  = gp.get(_norm_label('Straight / Heterosexual')) or 0.0
            pns       = gp.get(_norm_label('Prefer Not to Say')) or 0.0
            gay_share, other_share = _LGBTQ_SPLIT
            resolved = {
                'Straight / Heterosexual':     straight,
                'Gay or Lesbian':              round(lgbtq_pct * gay_share, 4),
                'Another Sexual Orientation':  round(lgbtq_pct * other_share, 4),
                'Prefer Not to Say':           pns,
            }
            out[cat] = {b: resolved.get(b, 0.0) for b in buckets}
            continue

        # Default path: canonical label match + missing-bucket -> 0
        resolved = {}
        for b in buckets:
            resolved[b] = gp.get(_norm_label(b)) or 0.0
        out[cat] = resolved
    return out


def _load_genpop_demographics() -> Optional[dict]:
    """Return {CATEGORY: {bucket_label: pct}} for the agent's canonical
    bucket set, aligned to the Gen Pop CSV. Values are pre-normalization
    - the caller should renormalize each category to 100 before
    surfacing to the frontend."""
    global _GENPOP_DEMOS, _GENPOP_LOAD_ATTEMPTED
    if _GENPOP_DEMOS is not None:
        return _GENPOP_DEMOS
    if _GENPOP_LOAD_ATTEMPTED:
        # We already tried and failed once this process - don't spam S3
        return None
    _GENPOP_LOAD_ATTEMPTED = True
    rows = _read_genpop_csv_rows()
    if not rows:
        logger.warning('microdramas_audience_agent: Gen Pop CSV unavailable; '
                        'comparison overlay will be omitted from payloads')
        return None
    _GENPOP_DEMOS = _map_genpop_to_agent_buckets(_extract_genpop_bp_by_category(rows))
    logger.info('microdramas_audience_agent: loaded Gen Pop baseline for %d categories',
                len(_GENPOP_DEMOS))
    return _GENPOP_DEMOS


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
                           "Bachelors Degree": 27.4,
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
                           "Bachelors Degree": 29.4,
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
                           "Bachelors Degree": 28.4,
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
                           "Bachelors Degree": 25.6,
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
                           "Bachelors Degree": 34.7,
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
    "150 = 1.5x more likely). Every interest label MUST be chosen from "
    "the CANONICAL HOSTMAP MENU provided in the user prompt - do NOT "
    "invent topic labels like 'BookTok / romance novels' or 'Reality "
    "dating shows'. Only real brand names from that menu. Any label "
    "that isn't on the menu will be dropped from the response.\n\n"
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

    # ------------------------------------------------------------------
    # Canonical HOSTMAP interest menu. Every "interests" label MUST be
    # chosen from this list - anything else will be dropped from the
    # response at post-processing time. Workspace rule #4: never surface
    # a brand that isn't in reference.host_mapping.
    # ------------------------------------------------------------------
    if HOSTMAP_INTEREST_MENU:
        lines.append('CANONICAL HOSTMAP MENU (interests): choose 5-8 for the '
                       '"interests" array. Use the exact spelling shown, one '
                       'per line, index each between 60 and 260 vs Gen Pop.')
        for entry in HOSTMAP_INTEREST_MENU:
            lines.append(f'  - {entry["label"]}')
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
    lines.append('    {"label": "TikTok",  "index": 205},')
    lines.append('    {"label": "Sephora", "index": 174},')
    lines.append('    ...5-8 total, each label chosen from the CANONICAL '
                 'HOSTMAP MENU below, each index between 60 and 260')
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
    # Gen Pop comparison overlay: same bucket labels, sourced from
    # Gen_Pop_2026.csv. Omitted from the payload when the CSV can't be
    # loaded so the frontend gracefully hides the overlay UI.
    genpop_by_cat = _load_genpop_demographics() or {}
    demographics_genpop = None
    if genpop_by_cat:
        demographics_genpop = {
            cat.lower(): _renormalize_100(genpop_by_cat.get(cat) or {}, DEMO_BUCKETS[cat])
            for cat in DEMO_ORDER
        }
    # Hostmap-gate every interest label. Workspace rule #4: never
    # surface a brand that isn't in reference.host_mapping. If Claude
    # (or the heuristic path) returned any label that isn't canonical,
    # drop it now and top up the list from the menu so the frontend
    # never renders an empty interests card. Jenna 2026-08-19: "make
    # sure the interests listed are only from the ones that are
    # canonical in the hostmap file."
    interests = _gate_interests_to_hostmap(raw.get('interests') or [])[:8]
    if len(interests) < 5:
        tilts = _tilts_from_context(title, series, genre, platform)
        menu = _interests_from_menu(tilts, target_n=8)
        seen = {r['label'].upper() for r in interests}
        for r in menu:
            if r['label'].upper() in seen:
                continue
            interests.append(r)
            seen.add(r['label'].upper())
            if len(interests) >= 8:
                break
    platforms = []
    for r in (raw.get('platform_affinities') or [])[:12]:
        if not isinstance(r, dict):
            continue
        lbl = str(r.get('label') or '').strip()
        rp = r.get('reach_pct')
        if lbl and isinstance(rp, (int, float)):
            platforms.append({'label': lbl, 'reach_pct': round(float(rp), 1)})
    payload = {
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
    if demographics_genpop is not None:
        payload['demographics_genpop']       = demographics_genpop
        payload['demographics_genpop_label'] = 'US Gen Pop (2026)'
    return payload


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
                   "Bachelors Degree": 30.1,
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

# _BASE_INTERESTS removed 2026-08-19: interests are now drawn from
# HOSTMAP_INTEREST_MENU (hostmap-gated) via _interests_from_menu().
# Every heuristic-path interest is guaranteed to be a real brand in
# reference.host_mapping - no invented topic labels ever ship.

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

    # Interest rankings drawn from the hostmap-gated canonical menu.
    # Both Claude and the heuristic use the SAME menu so the two code
    # paths render identically shaped interest bars. Workspace rule #4:
    # never surface a brand that isn't in reference.host_mapping.
    context_tilts = _tilts_from_context(title, series, genre, platform)
    interests = _interests_from_menu(context_tilts, target_n=8)

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
