"""Talent content verticals: the third component of a Talent hostmap SECTION.

Jessie (data team), 2026-10-12: 'Talent, Creator/Influencer' and
'Talent, Host/Personality' now carry a content sub-category as a third
SECTION component, e.g. ``Talent, Creator/Influencer, Travel Content``.
Nineteen labels; today only those two roles carry one (extending to
every Talent role is a data-team decision, and the parser below already
accepts any role).

One source of truth for everything that reads or writes these labels:

* the hostmap -> Column mapping (``synth_hostmap_augment.SECTION_TO_COLUMNS``
  entries + the generic ``talent_section_columns`` parser),
* the Gen Pop daily sync and the per-build hostmap gap-fill,
* the row-by-row engine (content columns are DERIVED mirrors of the
  role column, never reasoned separately),
* the enforcers' talent-family sets (mirror alignment, person-profile
  category gates),
* ``BG.py`` / ``bg-webapp/bg.py`` ``RECLASSIFY_SECTIONS`` tokens (kept
  literal there; ``scripts/test_talent_content_verticals.py`` asserts
  the lists agree with this module),
* the hostmap-gap mapping proposals (a creator / host proposal carries
  the researched vertical as the third component),
* the dashboard Talent card (content verticals are a slice).

A content column is named by the label in upper case: ``TRAVEL CONTENT``,
``EDUCATION/HOW-TO CONTENT``, ``NEWS/POLITICS CONTENT``. One column per
label, shared by every role that carries it (a travel creator and a
travel host both sit in ``TRAVEL CONTENT``). Rule #3b discipline: a
person carries ONE value across TALENT, the role column and the
content column.

Twin: ``bg-webapp/migration/talent_content_verticals.py`` is generated
from this file by ``scripts/sync_module_twins.py``. Never edit the twin.
"""
from __future__ import annotations

import re

# The 19 labels exactly as they appear in reference.host_mapping.SECTION.
CONTENT_LABELS: tuple[str, ...] = (
    'Education/How-To Content',
    'Entertainment Content',
    'Faith/Spirituality Content',
    'Fashion Content',
    'Business/Finance Content',
    'Fitness/Wellness Content',
    'Food Content',
    'Gaming Content',
    'Home/DIY Content',
    'Lifestyle Content',
    'Music/Dance Content',
    'News/Politics Content',
    'Family Content',
    'Pets Content',
    'Sports Content',
    'Tech Content',
    'Travel Content',
    'Art Content',
    'Beauty Content',
)

# Profile / Gen Pop Column per label (upper case of the label).
CONTENT_COLUMNS: tuple[str, ...] = tuple(lbl.upper() for lbl in CONTENT_LABELS)
CONTENT_COLUMN_SET: frozenset = frozenset(CONTENT_COLUMNS)

# Talent roles that carry a content vertical today (Jessie 2026-10-12),
# with the role Columns each one maps to. Order inside a list: specific
# column first, aliases after; TALENT is appended by the parser.
CONTENT_ROLES: dict[str, tuple[str, ...]] = {
    'Creator/Influencer': ('CREATOR/INFLUENCER', 'INFLUENCER/CREATOR'),
    'Influencer/Creator': ('CREATOR/INFLUENCER', 'INFLUENCER/CREATOR'),
    'Host/Personality': ('HOST/PERSONALITY',),
}
# Role Columns a content row mirrors (the engine derives a content row
# from the first of these that carries the person, then TALENT).
ROLE_COLUMNS_FOR_CONTENT: tuple[str, ...] = (
    'CREATOR/INFLUENCER', 'INFLUENCER/CREATOR', 'HOST/PERSONALITY', 'TALENT')

# Interest CATEGORY hint per vertical for the hostmap-gap mapping CSV
# (only labels already in the interest vocabulary the data team uses;
# the rest stay blank for them to set).
CONTENT_INTERESTS: dict[str, str] = {
    'Travel Content': 'Travel',
    'Gaming Content': 'Gaming',
    'Sports Content': 'Sports',
    'Fashion Content': 'Fashion',
    'Business/Finance Content': 'Business',
    'Tech Content': 'Technology',
    'Fitness/Wellness Content': 'Health & Wellness',
    'News/Politics Content': 'Politics, News - General',
    'Music/Dance Content': 'Music',
    'Entertainment Content': 'Entertainment',
}

# Research vocabulary -> label (the classifier answers with the key).
_RESEARCH_KEYS: dict[str, str] = {
    'education': 'Education/How-To Content',
    'how-to': 'Education/How-To Content',
    'howto': 'Education/How-To Content',
    'entertainment': 'Entertainment Content',
    'comedy': 'Entertainment Content',
    'faith': 'Faith/Spirituality Content',
    'spirituality': 'Faith/Spirituality Content',
    'fashion': 'Fashion Content',
    'business': 'Business/Finance Content',
    'finance': 'Business/Finance Content',
    'fitness': 'Fitness/Wellness Content',
    'wellness': 'Fitness/Wellness Content',
    'food': 'Food Content',
    'cooking': 'Food Content',
    'gaming': 'Gaming Content',
    'home': 'Home/DIY Content',
    'diy': 'Home/DIY Content',
    'lifestyle': 'Lifestyle Content',
    'music': 'Music/Dance Content',
    'dance': 'Music/Dance Content',
    'news': 'News/Politics Content',
    'politics': 'News/Politics Content',
    'family': 'Family Content',
    'parenting': 'Family Content',
    'pets': 'Pets Content',
    'sports': 'Sports Content',
    'tech': 'Tech Content',
    'technology': 'Tech Content',
    'travel': 'Travel Content',
    'art': 'Art Content',
    'beauty': 'Beauty Content',
}


def _key(s) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


_LABEL_BY_KEY: dict[str, str] = {_key(lbl): lbl for lbl in CONTENT_LABELS}
_LABEL_BY_COLUMN: dict[str, str] = {lbl.upper(): lbl for lbl in CONTENT_LABELS}


def content_label(value) -> str:
    """Canonical label for a SECTION token / Column / loose spelling, or ''.
    ``'travel content'``, ``'TRAVEL CONTENT'`` and ``'Travel  Content'`` all
    resolve to ``'Travel Content'``; ``'Travel'`` alone does not."""
    return _LABEL_BY_KEY.get(_key(value), '')


def content_column(value) -> str:
    """Column name for a label / token, or ''."""
    lbl = content_label(value)
    return lbl.upper() if lbl else ''


def is_content_column(column) -> bool:
    return str(column or '').strip().upper() in CONTENT_COLUMN_SET


def label_for_column(column) -> str:
    return _LABEL_BY_COLUMN.get(str(column or '').strip().upper(), '')


def label_from_research(answer) -> str:
    """Map a classifier answer ('travel', 'Travel Content', 'how-to') to
    a label; '' when it names none of the nineteen."""
    a = str(answer or '').strip()
    if not a:
        return ''
    direct = content_label(a)
    if direct:
        return direct
    low = a.lower()
    for k, lbl in _RESEARCH_KEYS.items():
        if re.search(r'(?<![a-z])' + re.escape(k) + r'(?![a-z])', low):
            return lbl
    return ''


def split_section(section) -> list[str]:
    return [p.strip() for p in str(section or '').split(',') if p.strip()]


def talent_section_parts(section):
    """('Creator/Influencer', 'Travel Content') for a three-part Talent
    SECTION whose third component is a known label; None otherwise."""
    parts = split_section(section)
    if len(parts) != 3 or parts[0].lower() != 'talent':
        return None
    lbl = content_label(parts[2])
    if not lbl:
        return None
    return parts[1], lbl


def talent_section_columns(section, role_columns=None) -> list[str] | None:
    """Columns for ``Talent, <Role>, <Content>``: the content column first,
    then the role's columns, then TALENT. ``role_columns`` maps a role
    token to its columns (defaults to CONTENT_ROLES; a two-part lookup
    table such as SECTION_TO_COLUMNS may be passed as
    ``{'Talent, Actor': [...]}`` and is consulted by prefix). None when
    the section is not that shape or the role is unknown."""
    parsed = talent_section_parts(section)
    if not parsed:
        return None
    role, lbl = parsed
    cols = None
    if role in CONTENT_ROLES:
        cols = list(CONTENT_ROLES[role])
    elif isinstance(role_columns, dict):
        two = role_columns.get(f'Talent, {role}')
        if two:
            cols = [c for c in two if str(c).upper() != 'TALENT']
    if cols is None:
        return None
    out = [lbl.upper()] + cols + ['TALENT']
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def content_sections() -> list[str]:
    """Every three-part SECTION the roles above can carry today."""
    out = []
    for role in CONTENT_ROLES:
        for lbl in CONTENT_LABELS:
            out.append(f'Talent, {role}, {lbl}')
    return out


def with_content(section, label) -> str:
    """Append a vertical to a two-part Talent SECTION ('Talent,
    Creator/Influencer' + 'Travel Content'); the section unchanged when
    it already carries three parts or the label is unknown."""
    lbl = content_label(label)
    parts = split_section(section)
    if not lbl or len(parts) != 2 or parts[0].lower() != 'talent':
        return str(section or '')
    return f'{parts[0]}, {parts[1]}, {lbl}'


def base_section(section) -> str:
    """'Talent, Creator/Influencer' for the three-part form; unchanged
    otherwise."""
    parsed = talent_section_parts(section)
    if not parsed:
        return str(section or '')
    return f'Talent, {parsed[0]}'


__all__ = [
    'CONTENT_LABELS', 'CONTENT_COLUMNS', 'CONTENT_COLUMN_SET', 'CONTENT_ROLES',
    'ROLE_COLUMNS_FOR_CONTENT', 'CONTENT_INTERESTS', 'content_label',
    'content_column', 'is_content_column', 'label_for_column',
    'label_from_research', 'split_section', 'talent_section_parts',
    'talent_section_columns', 'content_sections', 'with_content',
    'base_section',
]
