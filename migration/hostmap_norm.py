"""Shared norm-group resolution for reference.host_mapping.

Jenna directive 2026-08-24: *"can you update so that case sensitivity is
never the issue."*

Context: the hostmap contains brand entries that exist twice under
different spellings with CONFLICTING visibility. As of 2026-08-24 there
are exactly six such spelling-twin groups:

    CNET (Media, visible)           vs C-Net (Hidden)
    GHOST (Hidden)                  vs Ghost (Media, visible)
    Remezcla (Hidden)               vs REMEZCLA (Media, visible)
    Farmstay (Hidden)               vs FarmStay (Travel, visible)
    FREENOW (Hidden)                vs Freenow (Europe, visible)
    LOOKFANTASTIC (Hidden)          vs Look Fantastic (UK - Where They
                                       Shop, visible)

Because most consumers matched case/punctuation-sensitively, the two
spellings behaved as different brands: one enforcer stripped the brand
(saw the Hidden spelling) while another inserted it (saw the visible
spelling). The data team will reconcile the hostmap rows themselves;
this module makes every consumer in OUR pipeline normalization-aware so
spelling twins can never produce inconsistent behavior, before or after
that cleanup.

The policy (decided 2026-08-24)
-------------------------------
Group hostmap entries by the standard norm (casefold + strip
punctuation + collapse whitespace). Per norm-group:

  * **Visibility**: the brand is Hidden ONLY if EVERY entry in its
    group is Hidden. If any spelling is visible, the brand is
    available. Rationale: hiding a junk-spelling duplicate is spelling
    cleanup, not brand suppression; when the team wants a brand gone
    they hide all its entries.
  * **Canonical casing**: the visible entry's BRAND string. When a
    group has multiple candidate entries the tiebreak is deterministic,
    in this exact order:
      1. non-Hidden entries beat Hidden entries;
      2. entries with a non-empty SECTION beat entries with an empty
         SECTION;
      3. lexicographically smallest BRAND string (Python str ordering)
         wins.
    (For an all-Hidden group the same tiebreak runs among the Hidden
    entries, so the canonical spelling is still deterministic.)
  * **Category/SECTION**: from the chosen canonical entry. The group
    also exposes the union of all non-Hidden SECTIONs so section-driven
    consumers (e.g. SECTION_TO_COLUMNS routing) see every visible
    placement.

Norm definition
---------------
`norm_key` uppercases and strips every character that is not A-Z or
0-9. This matches the dominant existing convention (`_norm_brand` in
migration/post_generation_enforcers.py and BG.py, `_norm_brand_key` in
migration/synth_hostmap_augment.py, the collapsed key in
bg-webapp/app.py). Verified against the live hostmap on 2026-08-24:
20,276 distinct (BRAND, SECTION) rows collapse to 20,139 norm groups
and the ONLY groups with mixed visibility across different spellings
are the six twins above, so this norm introduces no false merges with
visibility conflicts.

ClickHouse-side equivalent (for SQL consumers that cannot import this
module):

    replaceRegexpAll(upper(BRAND), '[^A-Z0-9]', '')

and the all-hidden-group set is:

    SELECT replaceRegexpAll(upper(BRAND), '[^A-Z0-9]', '') AS nk
    FROM reference.host_mapping
    WHERE BRAND != ''
    GROUP BY nk
    HAVING countIf(SECTION='Hidden') > 0
       AND countIf(SECTION != 'Hidden') = 0

Fail-safe: `load_groups()` returns {} when ClickHouse is unreachable.
Callers must treat that as "no group info" and keep their existing
text-cache fallbacks; never abort a build because this module could
not reach ClickHouse.
"""
from __future__ import annotations

import os
import re
import sys
import threading
from typing import Iterable, NamedTuple, Optional

HIDDEN_SECTION = 'Hidden'

_NORM_RE = re.compile(r'[^A-Z0-9]')


def norm_key(brand) -> str:
    """The standard hostmap brand norm: casefold + strip punctuation +
    collapse whitespace (implemented as uppercase, keep A-Z0-9 only)."""
    return _NORM_RE.sub('', str(brand or '').upper())


class HostmapGroup(NamedTuple):
    canonical: str            # canonical BRAND spelling (see tiebreak above)
    hidden: bool              # True ONLY if every entry in the group is Hidden
    section: str              # SECTION of the canonical entry
    sections: tuple           # sorted union of non-Hidden SECTIONs in the group
    spellings: tuple          # sorted set of all raw BRAND spellings seen


def _entry_sort_key(entry):
    """Deterministic canonical-entry tiebreak: (1) non-Hidden first,
    (2) non-empty SECTION first, (3) lexicographic BRAND."""
    brand, section = entry
    is_hidden = 1 if section == HIDDEN_SECTION else 0
    empty_section = 1 if not section else 0
    return (is_hidden, empty_section, brand)


def resolve_groups(rows: Iterable[tuple]) -> dict:
    """Resolve an iterable of (BRAND, SECTION) hostmap rows into
    {norm_key: HostmapGroup} under the group semantics documented in
    the module docstring. Empty brands are skipped. Duplicate
    (BRAND, SECTION) rows are collapsed."""
    by_norm: dict = {}
    for brand, section in rows:
        b = str(brand or '').strip()
        s = str(section or '').strip()
        if not b:
            continue
        k = norm_key(b)
        if not k:
            continue
        by_norm.setdefault(k, set()).add((b, s))

    out: dict = {}
    for k, entries in by_norm.items():
        ordered = sorted(entries, key=_entry_sort_key)
        canonical, canon_section = ordered[0]
        hidden = all(s == HIDDEN_SECTION for _, s in entries)
        sections = tuple(sorted({s for _, s in entries
                                 if s and s != HIDDEN_SECTION}))
        spellings = tuple(sorted({b for b, _ in entries}))
        out[k] = HostmapGroup(
            canonical=canonical,
            hidden=hidden,
            section=canon_section,
            sections=sections,
            spellings=spellings,
        )
    return out


# =============================================================================
# ClickHouse-backed loader (cached per process)
# =============================================================================
_GROUPS_CACHE: Optional[dict] = None
_GROUPS_LOCK = threading.Lock()


def load_groups(verbose: bool = False, force_refresh: bool = False) -> dict:
    """Query reference.host_mapping once per process and return
    {norm_key: HostmapGroup}. Returns {} on any failure (fail-safe:
    callers keep their text-cache fallbacks)."""
    global _GROUPS_CACHE
    if _GROUPS_CACHE is not None and not force_refresh:
        return _GROUPS_CACHE
    with _GROUPS_LOCK:
        if _GROUPS_CACHE is not None and not force_refresh:
            return _GROUPS_CACHE
        try:
            try:
                from migration.clickhouse_connector import connect_clickhouse
            except ImportError:
                here = os.path.dirname(os.path.abspath(__file__))
                if here not in sys.path:
                    sys.path.insert(0, here)
                from clickhouse_connector import connect_clickhouse  # type: ignore
        except ImportError as e:
            if verbose:
                print(f'  [hostmap-norm] connector import failed: {e}')
            _GROUPS_CACHE = {}
            return _GROUPS_CACHE
        try:
            conn = connect_clickhouse()
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT BRAND, SECTION "
                "FROM reference.host_mapping "
                "WHERE BRAND != ''"
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            if verbose:
                print(f'  [hostmap-norm] ClickHouse query failed: {e}')
            _GROUPS_CACHE = {}
            return _GROUPS_CACHE
        _GROUPS_CACHE = resolve_groups(rows)
        if verbose:
            n_hidden = sum(1 for g in _GROUPS_CACHE.values() if g.hidden)
            print(f'  [hostmap-norm] resolved {len(_GROUPS_CACHE):,} norm '
                  f'groups ({n_hidden:,} all-hidden) from ClickHouse')
        return _GROUPS_CACHE


def clear_cache() -> None:
    """For tests only. Force the next load_groups() to re-query."""
    global _GROUPS_CACHE
    with _GROUPS_LOCK:
        _GROUPS_CACHE = None


def groups_from_tsv(path) -> dict:
    """Resolve groups from a hostmap TSV dump (BRAND<TAB>SECTION per
    line, e.g. /tmp/hostmap_dump.tsv)."""
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) >= 2:
                rows.append((parts[0], parts[1]))
    return resolve_groups(rows)


# =============================================================================
# Convenience lookups
# =============================================================================
def group_for(groups: dict, brand) -> Optional[HostmapGroup]:
    return groups.get(norm_key(brand))


def is_group_hidden(groups: dict, brand) -> bool:
    """True ONLY when the brand's entire norm-group is Hidden. Unknown
    brands return False (not-in-hostmap is a membership question, not a
    visibility one)."""
    g = groups.get(norm_key(brand))
    return bool(g and g.hidden)


def canonical_spelling(groups: dict, brand) -> Optional[str]:
    """Group-canonical BRAND spelling for any spelling of the brand, or
    None when the brand is not in the hostmap at all."""
    g = groups.get(norm_key(brand))
    return g.canonical if g else None


def all_hidden_canonicals(groups: dict) -> list:
    """Canonical spellings of groups whose EVERY entry is Hidden.
    This is the new content contract for
    reference/hostmap_hidden_brands.txt (one canonical spelling per
    all-hidden group; groups with any visible spelling never appear)."""
    return sorted(g.canonical for g in groups.values() if g.hidden)


def mpb_canonicals(groups: dict) -> list:
    """Canonical spellings of groups with at least one entry whose
    SECTION starts with 'Most Purchased' (and the group is not
    all-hidden). Content contract for reference/hostmap_mpb_brands.txt."""
    out = []
    for g in groups.values():
        if g.hidden:
            continue
        if any(s.startswith('Most Purchased') for s in g.sections):
            out.append(g.canonical)
    return sorted(out)


def all_canonicals(groups: dict) -> list:
    """One canonical spelling per norm-group, ALL groups (visible and
    all-hidden). Content contract for
    reference/hostmap_brands_canonical.txt: membership checks stay
    permissive for all-hidden brands (strip_hostmap_hidden_brands is
    the visibility gate), while canonical-casing lookups resolve every
    spelling to its group canonical."""
    return sorted(g.canonical for g in groups.values())


__all__ = [
    'HIDDEN_SECTION',
    'HostmapGroup',
    'norm_key',
    'resolve_groups',
    'load_groups',
    'clear_cache',
    'groups_from_tsv',
    'group_for',
    'is_group_hidden',
    'canonical_spelling',
    'all_hidden_canonicals',
    'mpb_canonicals',
    'all_canonicals',
]
