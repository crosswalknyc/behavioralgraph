"""Canonical BRAND CATEGORY normalization at spec time.

2026-08-27 (Toca Boca hold, run -NeqRO8p7-Igow): the final ship gate's
I2 check is the LAST line of defense against a non-canonical BRAND
CATEGORY value. This module is the FIRST line: it normalizes the
category where it is chosen (the chatbot interpret's draft -> spec
conversion on Render, and the queue worker's build entry point) so a
label like 'TOYS' resolves to the canonical 'TOY' before a single
Claude call is spent on the build.

Resolution order (per Jenna's 2026-08-27 directive):
  1. Exact match against the flattened iq_rankers.MASTER_CATEGORIES
     (case-insensitive, whitespace-stripped), plus the documented
     JS-side aliases and any 'SERIES - *' variant.
  2. Alias normalization: known synonyms (STREAMING SERVICE ->
     STREAMING/PLATFORM), separator drift (SEARCH ENGINE AI ->
     SEARCH ENGINE/AI via punctuation-insensitive keys), and
     singular/plural drift on the last word (TOYS -> TOY).
  3. Closest-canonical resolution (difflib, high cutoff) with a
     logged note.
  4. Still unknown: the value passes through UNCHANGED with a note -
     the ship gate stays the enforcement point. Never silently invent
     a category.

Fail-safe: if the canonical list cannot be loaded, the input passes
through unchanged (a list-load failure must never mutate a spec).

Shared by bg-webapp/app.py::_spec_from_draft (dashboard chatbot AND
partner API v1 - both build specs through that choke point) and
migration/synth_queue_worker.py::_run_new_build (covers direct queue
posts and the local override CLI).

Regression test: scripts/test_brand_category_canonicalization.py.
"""

from __future__ import annotations

import difflib
import re

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")

# Known synonyms that punctuation-insensitive matching alone cannot
# resolve. Keys are UPPER, whitespace-collapsed. Keep this map tight
# and documented - it inherits the entries that previously lived
# inline in bg-webapp/app.py::_spec_from_draft (P-Valley class,
# 2026-08-19) so both surfaces share one vocabulary.
_SYNONYMS = {
    "STREAMING SERVICE": "STREAMING/PLATFORM",
    "STREAMING SERVICES": "STREAMING/PLATFORM",
    "SVOD": "STREAMING/PLATFORM",
    "STREAMER": "STREAMING/PLATFORM",
    "TV SERIES": "SERIES",
    "TV SHOW": "SERIES",
    "MOVIES": "MOVIE",
    "FILM": "MOVIE",
    "VIDEO GAMES": "GAMES",
    "MOBILE GAME": "GAMES",
    "MOBILE GAMES": "GAMES",
    "CREATOR": "INFLUENCER/CREATOR",
    "INFLUENCER": "INFLUENCER/CREATOR",
}

_CANON_STATE = {"set": None, "norm_map": None}


def _norm_key(s):
    return _NON_ALNUM_RE.sub("", str(s or "").upper())


def _load_canonical():
    """(canonical_set_upper, norm_key -> canonical) or (None, None) when
    the list cannot be loaded. Cached for the process lifetime."""
    if _CANON_STATE["set"] is not None:
        return _CANON_STATE["set"], _CANON_STATE["norm_map"]
    try:
        try:
            from migration.final_ship_gate import (
                _load_master_categories,
                _FALLBACK_ALIAS_EXTRAS,
            )
        except ImportError:
            from final_ship_gate import (  # type: ignore
                _load_master_categories,
                _FALLBACK_ALIAS_EXTRAS,
            )
        cats = _load_master_categories(verbose=False)
        if not cats:
            return None, None
        canon = {str(c).strip().upper() for c in cats}
        canon |= {str(a).strip().upper() for a in _FALLBACK_ALIAS_EXTRAS}
        norm_map = {}
        for c in sorted(canon):
            norm_map.setdefault(_norm_key(c), c)
        _CANON_STATE["set"] = canon
        _CANON_STATE["norm_map"] = norm_map
        return canon, norm_map
    except Exception:
        return None, None


def _plural_variants(label):
    """Singular/plural variants of the LAST word (>=4 chars), the same
    morphology drift the Toca Boca defect rode (TOYS vs TOY)."""
    words = label.split()
    if not words or len(words[-1]) < 3:
        return []
    last = words[-1]
    out = []
    if last.endswith("S") and len(last) >= 4:
        out.append(" ".join(words[:-1] + [last[:-1]]))
    out.append(" ".join(words[:-1] + [last + "S"]))
    return out


def canonicalize_brand_category(raw):
    """Resolve a BRAND CATEGORY label to its canonical form.

    Returns (value, note). `note` is '' when the input was already
    canonical (or empty); otherwise a one-line description of the
    normalization applied, or of why the value was left for the ship
    gate. The returned value is UPPER whenever a resolution happened;
    an unresolved value passes through stripped but otherwise as-is.
    """
    label = str(raw or "").strip()
    if not label:
        return label, ""
    upper = re.sub(r"\s+", " ", label.upper())

    # 'SERIES - *' variants (and bare SERIES) are canonical per rule.
    if upper == "SERIES" or upper.startswith("SERIES -") \
            or upper.startswith("SERIES-"):
        return upper, "" if upper == label else f"case/space-normalized {label!r}"

    canon, norm_map = _load_canonical()
    if not canon:
        return label, "canonical list unavailable; left as-is for ship gate"

    if upper in canon:
        return upper, "" if upper == label else f"case-normalized {label!r}"

    # Known synonyms.
    if upper in _SYNONYMS:
        target = _SYNONYMS[upper]
        return target, f"synonym {label!r} -> {target!r}"

    # Punctuation / separator drift (SEARCH ENGINE AI -> SEARCH ENGINE/AI).
    hit = norm_map.get(_norm_key(upper))
    if hit:
        return hit, f"separator-normalized {label!r} -> {hit!r}"

    # Singular/plural drift on the last word (TOYS -> TOY).
    for var in _plural_variants(upper):
        if var in canon:
            return var, f"singular/plural {label!r} -> {var!r}"
        hit = norm_map.get(_norm_key(var))
        if hit:
            return hit, f"singular/plural {label!r} -> {hit!r}"

    # Closest canonical, high cutoff so junk never false-matches
    # ('BRAND' must NOT resolve to 'BANK').
    close = difflib.get_close_matches(upper, sorted(canon), n=1, cutoff=0.87)
    if close:
        return close[0], f"closest-canonical {label!r} -> {close[0]!r}"

    return label, (f"no canonical resolution for {label!r}; "
                   f"ship gate remains the enforcement point")
