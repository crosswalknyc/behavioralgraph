"""Canonical demographic buckets - pipeline source of truth.

Derived from `reference/demos.csv` (2026-08-06 snapshot of the
`userdata.user_data_sanitized` DISTINCT scan; ~36M rows). Only
buckets with material Row_Count (> a few hundred) are treated as
canonical. Legacy / accidental variants (Student at 1 row,
"$75,000 – $99,999" with en-dash at 5 rows, etc.) are NOT
canonical - the pipeline must collapse them.

RELATIONSHIP 'Widowed' is the one deliberate exception to the
demos.csv row-count bar (2026-08-27 schema-drift verdict): the panel
scan carries it at only 2 rows, but the PIPELINE emits it everywhere
- both BG.py / bg-webapp/bg.py LLM demo prompt templates, the persona
writer's _EXPECTED_DEMO_BUCKETS injection list, and the small-sample
schema back-fill (migration/small_sample_hardening.CANONICAL_BUCKETS)
- and deployed reality agrees: Gen_Pop_2026.csv RELATIONSHIP carries
WIDOWED at 5.51 and every TU shipped since 2026-08-14 carries a
Widowed row. Per rule 5a the pipeline is the source of truth, so
Widowed is canonical. Before this verdict, this module drop-aliased
Widowed while the back-fill re-inserted it, so every run destroyed
the reasoned Widowed value and replaced it with the ~2.0 back-fill
floor. Regression: scripts/test_demo_bracket_crater.py section [F].

Rule 5a of `.cursor/rules/profile-iq-pipeline-rules.mdc`:
    "Pipeline is the source of truth for demographic schema. Gen Pop
     demographic categories + bucket labels = exactly what the pipeline
     emits. Never the reverse."

Jenna 2026-08-06:
    "make sure for all synth profiles that it always only uses canonical
     demos. like north west has wrong ones in education for example.
     should only be from these [demos.csv]"

Use `enforce_canonical_demo_buckets(df, subject)` in
`post_generation_enforcers.py` to force-collapse any non-canonical
bucket into the closest canonical target.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# CANONICAL: the schema the pipeline emits and the dashboard renders.
# Order matters -- these are the display order.
# ---------------------------------------------------------------------------
PIPELINE_DEMO_SCHEMA: dict[str, list[str]] = {
    'GENDER': [
        'Female', 'Male', 'Non-Binary', 'Trans Female', 'Trans Male',
        'Prefer Not to Say',
    ],
    'AGE': [
        '17 and Under', '18-24', '25-34', '35-44', '45-54', '55-64',
        '65 or Older', 'Other',
    ],
    'ETHNICITY': [
        'White', 'Hispanic or Latino', 'Black or African American',
        'Asian', 'Another Race/Ethnicity',
    ],
    'INCOME': [
        'Less than $25,000', '$25,000 - $49,999', '$50,000 - $74,999',
        '$75,000 - $99,999', '$100,000 - $149,999', '$150,000 - $249,999',
        '$250,000 or More',
    ],
    'EDUCATION': [
        'High School or Less', 'Some College / Associate Degree',
        'Bachelors Degree', 'Graduate or Professional Degree',
        'Prefer Not to Say',
    ],
    'RELATIONSHIP': [
        # 'Widowed' is pipeline-canonical (2026-08-27 verdict, see module
        # docstring). 'Prefer Not to Say' is a panel keep (4.7M rows in
        # demos.csv) but is NOT back-fill-required - the prompt schema
        # does not emit it and no shipped file carries it.
        'Single', 'In a Relationship', 'Married', 'Divorced or Separated',
        'Widowed', 'Prefer Not to Say',
    ],
    'SEXUAL_ORIENTATION': [
        'Straight / Heterosexual', 'Gay or Lesbian',
        'Another Sexual Orientation', 'LGBTQ+', 'Prefer Not to Say',
    ],
    'PARENTAL_STATUS': [
        'No Children', 'Has Children', 'Prefer Not to Say',
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
    'PRIMARY_LANGUAGE': [
        'English', 'Spanish', 'Chinese', 'Other',
    ],
    'NUMBER_OF_CHILDREN': [
        '0', '1', '2', '3', '4+',
    ],
    'AGE_OF_CHILDREN': [
        'No Kids', 'Under 3', '3 to 5', '6 to 10', '11 to 13', '14 to 17',
    ],
}

# ---------------------------------------------------------------------------
# ALIASES: normalized-key -> canonical bucket. If the normalized key
# is in the canonical set (see _canonical_norm_set below), it is
# returned as-is. If it's in the aliases here, remap. Otherwise it's
# orphan and the enforcer will collapse it (usually to the most common
# canonical bucket for that category, else drop + redistribute).
#
# Normalization: uppercase, strip apostrophes, collapse en/em dashes
# to hyphen, collapse whitespace. See `_norm()` below.
# ---------------------------------------------------------------------------
_ALIASES: dict[str, dict[str, str]] = {
    'GENDER': {
        'F': 'Female',
        'M': 'Male',
        'NON BINARY': 'Non-Binary',
        'NONBINARY': 'Non-Binary',
        'GENDERQUEER': 'Non-Binary',
        'OTHER': 'Prefer Not to Say',
    },
    'AGE': {
        # Legacy edges
        '65+': '65 or Older',
        '65 AND OVER': '65 or Older',
        '55-64': '55-64',
        # Some sources use "13-17" / "UNDER 18"
        'UNDER 18': '17 and Under',
        '13-17': '17 and Under',
        '<18': '17 and Under',
        # "45+" from older synth outputs
        '45+': '45-54',
        # Multi-decade merged (map to peak neighbor)
        '16-18': '17 and Under',
        '18-20': '18-24',
        '21-25': '25-34',
        '26-30': '25-34',
        '31-40': '35-44',
        '41-59': '45-54',
        '60+': '55-64',
    },
    'ETHNICITY': {
        # UK / Ofcom variants (drop nation prefix)
        'WHITE BRITISH': 'White',
        'WHITE OTHER': 'White',
        'WHITE EUROPEAN': 'White',
        'ASIAN BRITISH': 'Asian',
        'BLACK BRITISH': 'Black or African American',
        'BLACK': 'Black or African American',
        'AFRICAN AMERICAN': 'Black or African American',
        'HISPANIC': 'Hispanic or Latino',
        'LATINO': 'Hispanic or Latino',
        'LATINX': 'Hispanic or Latino',
        'MIXED / MULTIPLE ETHNIC GROUPS': 'Another Race/Ethnicity',
        'MIXED': 'Another Race/Ethnicity',
        'OTHER ETHNIC GROUP': 'Another Race/Ethnicity',
        'OTHER': 'Another Race/Ethnicity',
        'ANOTHER RACE OR ETHNICITY': 'Another Race/Ethnicity',
        # Compound labels: collapse to primary
        'WHITE, HISPANIC OR LATINO': 'Hispanic or Latino',
        'BLACK OR AFRICAN AMERICAN, HISPANIC OR LATINO': 'Hispanic or Latino',
        # AAPI + First Nations spellings
        'NATIVE AMERICAN': 'Another Race/Ethnicity',
        'NATIVE AMERICAN / ALASKA NATIVE': 'Another Race/Ethnicity',
        'AMERICAN INDIAN OR ALASKA NATIVE': 'Another Race/Ethnicity',
        'PACIFIC ISLANDER': 'Asian',
        'NATIVE HAWAIIAN OR PACIFIC ISLANDER': 'Asian',
        'ASIAN OR PACIFIC ISLANDER': 'Asian',
        'TWO OR MORE RACES': 'Another Race/Ethnicity',
        'MIDDLE EASTERN OR NORTH AFRICAN': 'Another Race/Ethnicity',
        'MENA': 'Another Race/Ethnicity',
        # German-locale (Omaze Germany + future DE profiles)
        'DEUTSCH (OHNE MIGRATIONSHINTERGRUND)': 'White',
        'DEUTSCH (MIT MIGRATIONSHINTERGRUND)': 'Another Race/Ethnicity',
        'TURKEISTAMMIG': 'Another Race/Ethnicity',
        'TÜRKEISTAMMIG': 'Another Race/Ethnicity',
        'EU-AUSLANDER:IN': 'White',
        'EU-AUSLÄNDER:IN': 'White',
        'AUS DEM NAHEN OSTEN / NORDAFRIKA': 'Another Race/Ethnicity',
        'AUS OSTEUROPA (NICHT-EU)': 'White',
        'ANDERE': 'Another Race/Ethnicity',
    },
    'INCOME': {
        # Older synth output uses "Under $25,000"; canonical is "Less than"
        'UNDER $25,000': 'Less than $25,000',
        'LESS THAN $25K': 'Less than $25,000',
        '$25K - $49K': '$25,000 - $49,999',
        '$50K - $74K': '$50,000 - $74,999',
        '$75K - $99K': '$75,000 - $99,999',
        '$100K - $149K': '$100,000 - $149,999',
        '$150K - $249K': '$150,000 - $249,999',
        '$250K OR MORE': '$250,000 or More',
        '$250K+': '$250,000 or More',
        '$250,000+': '$250,000 or More',
        # UK £ buckets by-rank mapping (7DAYS and similar UK profiles)
        '£15,000 - £24,999': 'Less than $25,000',
        '£25,000 - £34,999': '$25,000 - $49,999',
        '£35,000 - £49,999': '$25,000 - $49,999',
        '£50,000 - £74,999': '$50,000 - $74,999',
        '£75,000 - £99,999': '$75,000 - $99,999',
        '£100,000+': '$100,000 - $149,999',
        # German € buckets (Omaze Germany + future DE profiles). By-rank
        # mapping mirrors GBP handling.
        '€15,000 - €24,999': 'Less than $25,000',
        '€25,000 - €34,999': '$25,000 - $49,999',
        '€35,000 - €49,999': '$25,000 - $49,999',
        '€50,000 - €74,999': '$50,000 - $74,999',
        '€75,000 - €99,999': '$75,000 - $99,999',
        '€100,000+': '$100,000 - $149,999',
        # A profile-side "Prefer Not to Say" income bucket is not
        # canonical -- redistribute during collapse.
        'PREFER NOT TO SAY': None,   # None means DROP + redistribute
    },
    'EDUCATION': {
        # The 7-bucket schema my new synth scripts invented
        'LESS THAN HIGH SCHOOL': 'High School or Less',
        'HIGH SCHOOL DIPLOMA / GED': 'High School or Less',
        'HIGH SCHOOL DIPLOMA': 'High School or Less',
        'HIGH SCHOOL / GED': 'High School or Less',
        'HIGH SCHOOL': 'High School or Less',
        'SOME COLLEGE': 'Some College / Associate Degree',
        'ASSOCIATE DEGREE': 'Some College / Associate Degree',
        'ASSOCIATES DEGREE': 'Some College / Associate Degree',
        'ASSOCIATE': 'Some College / Associate Degree',
        'TRADE SCHOOL': 'Some College / Associate Degree',
        'VOCATIONAL': 'Some College / Associate Degree',
        # Bachelor variants
        'BACHELORS': 'Bachelors Degree',
        'BACHELOR': 'Bachelors Degree',
        # Grad/Master/Doctorate all collapse to Graduate or Professional
        'MASTERS DEGREE': 'Graduate or Professional Degree',
        'MASTERS': 'Graduate or Professional Degree',
        'DOCTORATE': 'Graduate or Professional Degree',
        'DOCTORATE / PROFESSIONAL DEGREE': 'Graduate or Professional Degree',
        'DOCTORATE / PHD': 'Graduate or Professional Degree',
        'PHD': 'Graduate or Professional Degree',
        'PROFESSIONAL DEGREE': 'Graduate or Professional Degree',
        'MD/JD': 'Graduate or Professional Degree',
        'MD OR JD': 'Graduate or Professional Degree',
        'GRADUATE DEGREE': 'Graduate or Professional Degree',
        'GRADUATE': 'Graduate or Professional Degree',
        # UK
        'A-LEVEL / BTEC': 'Some College / Associate Degree',
        'A-LEVELS': 'Some College / Associate Degree',
        'GCSE / O-LEVEL': 'High School or Less',
        'GCSE': 'High School or Less',
        'NO FORMAL QUALIFICATION': 'High School or Less',
        # German (Omaze Germany + future DE profiles)
        'HAUPTSCHULABSCHLUSS': 'High School or Less',
        'REALSCHULABSCHLUSS (MITTLERE REIFE)': 'High School or Less',
        'REALSCHULABSCHLUSS': 'High School or Less',
        'ABITUR / FACHHOCHSCHULREIFE': 'Some College / Associate Degree',
        'ABITUR': 'Some College / Associate Degree',
        'BACHELOR / DIPLOM (FH)': 'Bachelors Degree',
        'BACHELOR / DIPLOM': 'Bachelors Degree',
        'MASTER / DIPLOM (UNI)': 'Graduate or Professional Degree',
        'MASTER / DIPLOM': 'Graduate or Professional Degree',
        'PROMOTION / PHD': 'Graduate or Professional Degree',
        'PROMOTION': 'Graduate or Professional Degree',
    },
    'RELATIONSHIP': {
        # NOTE: 'WIDOWED' was drop-aliased (None) here until 2026-08-27.
        # It is now canonical (see module docstring) and matches directly
        # in _CANONICAL_NORM - no alias needed.
        'SINGLE (NOT LIVING WITH A PARTNER)': 'Single',
        'DATING': 'In a Relationship',
        'PARTNERED': 'In a Relationship',
        'ENGAGED': 'In a Relationship',
        'COHABITING': 'In a Relationship',
        'LIVING TOGETHER': 'In a Relationship',
        'DOMESTIC PARTNERSHIP': 'In a Relationship',
        'SEPARATED': 'Divorced or Separated',
        'DIVORCED': 'Divorced or Separated',
    },
    'SEXUAL_ORIENTATION': {
        # Older BG.py outputs collapse "Gay or Lesbian" + "Another
        # Sexual Orientation" into "LGBTQ+"; both are valid. Only
        # legacy variants below.
        'STRAIGHT': 'Straight / Heterosexual',
        'HETEROSEXUAL': 'Straight / Heterosexual',
        'BISEXUAL': 'LGBTQ+',
        'PANSEXUAL': 'LGBTQ+',
        'QUEER': 'LGBTQ+',
        'ASEXUAL': 'LGBTQ+',
        'GAY': 'Gay or Lesbian',
        'LESBIAN': 'Gay or Lesbian',
        'OTHER': 'Another Sexual Orientation',
    },
    'PARENTAL_STATUS': {
        'YES': 'Has Children',
        'NO': 'No Children',
        'HAS KIDS': 'Has Children',
        'NO KIDS': 'No Children',
        "DOESN'T HAVE KIDS": 'No Children',
        "DOES NOT HAVE KIDS": 'No Children',
        "DOESN'T HAVE CHILDREN": 'No Children',
        "DOES NOT HAVE CHILDREN": 'No Children',
    },
    'OCCUPATION': {
        # Legacy 1-row buckets in demos.csv (Retired, Self-Employed,
        # Student, Homemaker, Business and Financial Operations,
        # Computer and Mathematical) collapse into 'Other' EXCEPT for
        # the few that map cleanly into the modern taxonomy.
        'RETIRED': 'Other',
        'HOMEMAKER': 'Other',
        'HOMEMAKER OR CAREGIVER': 'Other',
        'UNEMPLOYED': 'Other',
        'STUDENT': 'Other',
        'SELF-EMPLOYED': 'Management, Business & Professional',
        'SELF EMPLOYED': 'Management, Business & Professional',
        'BUSINESS AND FINANCIAL OPERATIONS':
            'Management, Business & Professional',
        'MANAGEMENT & PROFESSIONAL':
            'Management, Business & Professional',
        'BUSINESS/FINANCE':
            'Management, Business & Professional',
        'COMPUTER AND MATHEMATICAL':
            'Science, Technology & Technical Professions',
        'TECH': 'Science, Technology & Technical Professions',
        'ENGINEERING': 'Science, Technology & Technical Professions',
        'HEALTHCARE': 'Healthcare Practitioners or Support',
        'MEDICAL': 'Healthcare Practitioners or Support',
        'EDUCATION': 'Education or Library Services',
        'TEACHER': 'Education or Library Services',
        'RETAIL': 'Sales & Retail',
        'SALES': 'Sales & Retail',
        'SERVICE': 'Service & Hospitality',
        'HOSPITALITY': 'Service & Hospitality',
        'CONSTRUCTION': 'Skilled Trades/Construction or Maintenance',
        'TRADES': 'Skilled Trades/Construction or Maintenance',
        'MAINTENANCE': 'Skilled Trades/Construction or Maintenance',
        'FARMING': 'Agriculture & Outdoor',
        'AGRICULTURE': 'Agriculture & Outdoor',
        'TRUCKING': 'Transportation & Logistics',
        'TRANSPORTATION': 'Transportation & Logistics',
        'LOGISTICS': 'Transportation & Logistics',
        'MANUFACTURING': 'Manufacturing & Production',
        'PRODUCTION': 'Manufacturing & Production',
        'MILITARY': 'Public Safety & Protective Services',
        'POLICE': 'Public Safety & Protective Services',
        'FIRE': 'Public Safety & Protective Services',
        'LAW': 'Legal',
        'ATTORNEY': 'Legal',
        'LAWYER': 'Legal',
        # OCCUPATION also should not have a Prefer Not to Say bucket
        # in the canonical taxonomy (demos.csv doesn't have it either),
        # so redistribute.
        'PREFER NOT TO SAY': None,
        'NOT APPLICABLE': None,
        'N/A': None,
    },
    'PRIMARY_LANGUAGE': {
        # demos.csv has only English/Spanish/Chinese/Other
        'MANDARIN': 'Chinese',
        'CANTONESE': 'Chinese',
        'PORTUGUESE': 'Other',
        'FRENCH': 'Other',
        'ARABIC': 'Other',
        'VIETNAMESE': 'Other',
        'TAGALOG': 'Other',
        'KOREAN': 'Other',
        'JAPANESE': 'Other',
    },
    'NUMBER_OF_CHILDREN': {
        '4': '4+',
        '5': '4+',
        '5+': '4+',
        '6+': '4+',
    },
    'AGE_OF_CHILDREN': {
        'INFANT': 'Under 3',
        'INFANT/TODDLER': 'Under 3',
        '0-2': 'Under 3',
        'TODDLER': 'Under 3',
        '3-5': '3 to 5',
        '6-10': '6 to 10',
        '11-13': '11 to 13',
        '14-17': '14 to 17',
        '3–5': '3 to 5',
        '6–10': '6 to 10',
        '11–13': '11 to 13',
        '14–17': '14 to 17',
        'NO CHILDREN': 'No Kids',
        'N/A': 'No Kids',
    },
}


def _norm(s: str) -> str:
    """Normalize a value for canonical-lookup matching.

    - Uppercase
    - Strip apostrophes (canonical form has no apostrophes)
    - Convert en/em dashes to hyphen
    - Collapse whitespace
    """
    if s is None:
        return ''
    s = str(s).strip()
    if not s:
        return ''
    s = s.upper()
    s = s.replace('\u2013', '-').replace('\u2014', '-')
    s = s.replace('\u2019', '').replace('\u2018', '')
    s = s.replace("'", '')
    import re as _re
    s = _re.sub(r'\s+', ' ', s)
    return s


def _loose_norm(s: str) -> str:
    """Alphanumeric-only fold of `_norm` for punctuation-mangled labels.

    2026-08-27 (YMCA income crater): the interpret step emitted the
    INCOME bucket label `25,000 - $49,999` (leading `$` missing). The
    strict `_norm` key did not match the canonical `$25,000 - $49,999`,
    the row orphaned in `enforce_canonical_demo_buckets`, its ~17pp of
    mass was dropped + redistributed, and the schema back-fill
    re-created the bucket at an epsilon floor (0.31%). This fold makes
    `$`-and-punctuation variants collapse onto the canonical bucket so
    a cosmetic label defect can never destroy a bracket's mass.
    """
    import re as _re
    return _re.sub(r'[^A-Z0-9]', '', _norm(s))


# Precomputed norm-key sets for fast canonical membership check.
_CANONICAL_NORM: dict[str, dict[str, str]] = {
    cat: {_norm(v): v for v in vals}
    for cat, vals in PIPELINE_DEMO_SCHEMA.items()
}


def _build_loose_maps():
    """Loose-key lookup maps with collision guards.

    A loose key that maps to two DIFFERENT canonical buckets within the
    same category is ambiguous and excluded (strict matching only for
    those); same for aliases whose loose keys collide with a canonical
    loose key that resolves to a different target.
    """
    canon_loose: dict[str, dict[str, str]] = {}
    alias_loose: dict[str, dict[str, str | None]] = {}
    for cat, vals in PIPELINE_DEMO_SCHEMA.items():
        m: dict[str, str] = {}
        ambiguous: set[str] = set()
        for v in vals:
            k = _loose_norm(v)
            if not k:
                continue
            if k in m and m[k] != v:
                ambiguous.add(k)
            else:
                m[k] = v
        for k in ambiguous:
            m.pop(k, None)
        canon_loose[cat] = m
        am: dict[str, str | None] = {}
        a_ambiguous: set[str] = set()
        for alias_raw, target in _ALIASES.get(cat, {}).items():
            k = _loose_norm(alias_raw)
            if not k:
                continue
            if k in m and m[k] != target:
                # canonical loose key wins; skip conflicting alias key
                continue
            if k in am and am[k] != target:
                a_ambiguous.add(k)
            else:
                am[k] = target
        for k in a_ambiguous:
            am.pop(k, None)
        alias_loose[cat] = am
    return canon_loose, alias_loose


_CANONICAL_LOOSE, _ALIAS_LOOSE = _build_loose_maps()


def canonical_value(category: str, value: str) -> str | None:
    """Return the canonical form for ``value`` in ``category``.

    Returns:
        * The canonical bucket string if ``value`` matches (via norm-key
          or alias). Canonical form always no-apostrophe.
        * ``None`` if the value is aliased to DROP (e.g. Prefer Not to
          Say in INCOME/OCCUPATION).
        * The original value unchanged if the category isn't in the
          canonical schema.
    """
    cat = str(category or '').strip().upper()
    if cat not in _CANONICAL_NORM:
        return value
    nkey = _norm(value)
    if not nkey:
        return value
    # 1. Direct canonical match
    if nkey in _CANONICAL_NORM[cat]:
        return _CANONICAL_NORM[cat][nkey]
    # 2. Alias lookup (also normalized on both sides)
    aliases = _ALIASES.get(cat, {})
    for alias_raw, target in aliases.items():
        if _norm(alias_raw) == nkey:
            return target  # may be None -> DROP
    # 3. Loose (alphanumeric-only) fallback: catches punctuation-mangled
    #    labels like `25,000 - $49,999` (missing `$` - 2026-08-27 YMCA
    #    income crater). Collision-guarded maps built at module load.
    lkey = _loose_norm(value)
    if lkey:
        hit = _CANONICAL_LOOSE.get(cat, {}).get(lkey)
        if hit is not None:
            return hit
        am = _ALIAS_LOOSE.get(cat, {})
        if lkey in am:
            return am[lkey]  # may be None -> DROP
    # 4. Orphan: no canonical match and no alias. Caller decides.
    return _ORPHAN


class _Orphan:
    """Sentinel returned when a value is neither canonical nor aliased."""
    __slots__ = ()
    def __repr__(self): return '<ORPHAN>'


_ORPHAN = _Orphan()


def is_canonical(category: str, value: str) -> bool:
    """Return True iff `value` is already canonical for `category`."""
    cat = str(category or '').strip().upper()
    if cat not in _CANONICAL_NORM:
        return True  # non-demo, not our concern
    return _norm(value) in _CANONICAL_NORM[cat]


def is_orphan(category: str, value: str) -> bool:
    """Return True iff `value` is neither canonical nor aliased."""
    return canonical_value(category, value) is _ORPHAN


def orphan_fallback(category: str) -> str | None:
    """Return the canonical bucket that orphans should collapse to when
    no better mapping exists. For OCCUPATION and ETHNICITY this is
    'Other' / 'Another Race/Ethnicity'. For EDUCATION, 'High School or
    Less' as a conservative default. For everything else, None (=DROP
    the row and let the auditor flag it)."""
    cat = str(category or '').strip().upper()
    return {
        'ETHNICITY': 'Another Race/Ethnicity',
        'EDUCATION': 'High School or Less',
        'OCCUPATION': 'Other',
        'PRIMARY_LANGUAGE': 'Other',
        'AGE': 'Other',
        'AGE_OF_CHILDREN': 'No Kids',
    }.get(cat)
