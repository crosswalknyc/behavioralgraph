"""Two-audience reads: the two sides are two families (2026-11-10).

Cara Dorfman (Amazon), 2026-11-10: "in October 2026 whats the overlap in
engagement of those who have used PRIME video and USED Tubi". The paired
read came back as 'TUBI - Avid Fan' x 'TUBI': the detector took every
library file whose tokens sat in her words, and the only two it found
were Tubi's Total Universe and Tubi's own Avid Fan cut, because "Prime
Video" never matched the library's "Amazon Prime Video". The read then
ran on the Tubi Avid Fan cut as its base.

Two pure rules, used by the legacy detector (prometheus.legacy.chat
_pm_two_base_detect) and testable without the catalog:

* ``alias_tokens(text)``: the library spelling of a platform the words
  name under another name ("prime video" -> Amazon Prime Video), as the
  extra tokens the token-subset match needs. prometheus.platform_cuts
  owns the alias table.
* ``two_families(found)``: at most one file per family (a Total Universe
  over its cuts, else the first seen), the two longest subjects first,
  as the detector orders them. A family is never paired with its own cut.
"""
from __future__ import annotations

import re


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()


# Aliases safe to read as the library's file: a phrase that names the
# service ("prime video", "amazon video", "apple tv plus") or a run-together
# spelling of it. A bare company or common word ("amazon", "max", "disney",
# "apple tv" as a device) is not expanded: "shopping at Amazon" is not a
# Prime Video ask.
_SAFE_SINGLE = frozenset(('primevideo', 'hbomax', 'appletv', 'vudu'))
_UNSAFE_PHRASES = frozenset(('amazon prime', 'apple tv'))


def alias_tokens(text):
    """Tokens of the library names for every platform alias the words
    carry; an empty set when none. Only aliases that are not already the
    library spelling add anything."""
    try:
        from .platform_cuts import PLATFORM_ALIASES as _AL
    except Exception:
        return set()
    low = ' ' + _norm(text) + ' '
    out = set()
    for alias, canon in _AL.items():
        a = _norm(alias)
        if not a or a in _UNSAFE_PHRASES:
            continue
        if ' ' not in a and a not in _SAFE_SINGLE:
            continue
        if f' {a} ' in low and _norm(canon) != a:
            out.update(_norm(canon).split())
    return out


def family_of(subject):
    return str(subject or '').split(' - ')[0].strip()


def is_total_universe(entry):
    name = str((entry or {}).get('display_name') or (entry or {}).get('subject') or '')
    return ' - ' not in name


def two_families(found):
    """From {key: {'subject', 's3_key', ...}} (the detector's hits), the
    files to pair: one per family, the Total Universe preferred over a
    cut, ordered longest subject first. Fewer than two families means no
    pair."""
    by_family = {}
    for v in (found or {}).values():
        if not isinstance(v, dict) or not v.get('s3_key'):
            continue
        fam = _norm(family_of(v.get('subject')))
        if not fam:
            continue
        cur = by_family.get(fam)
        if cur is None or (is_total_universe(v) and not is_total_universe(cur)):
            by_family[fam] = v
    pair = list(by_family.values())
    pair.sort(key=lambda v: -len(str(v.get('subject') or '')))
    return pair
