"""Self-property coherence primitives (2026-08-26, Liz QA, Paw Patrol).

Shared by the post-generation enforcers, the final ship gate (I16/I17/
I18), the avid direction pass, and the corpus scanners, so detection
and remediation stay byte-identical everywhere.

The Paw Patrol Series Viewers escalation surfaced four cross-grid
contradictions rooted in one shared blind spot: nothing in the
pipeline knew which rows are the SUBJECT'S OWN PROPERTY once the
subject name carries an audience suffix ("Paw Patrol Series Viewers"
vs the brand rows "PAW PATROL"). This module owns that mapping:

  own_token(subject)         audience-suffix-stripped normalized token
  is_subject_own(subject, v) bidirectional containment vs that token
  check_self_property_coherence(...)   the I16 ratio-band detection
  coherence_target(...)      the documented arithmetic reconciliation

Ratio band (I16): on a content/franchise subject whose own FRANCHISE
row reads >= ANCHOR_MIN, every own-property merch/games/media row must
sit at >= RATIO_FLOOR of the franchise anchor. A preschool viewer base
at 83% franchise engagement cannot sit at 6% on the property's own
toys (Paw Patrol pre-fix state: FRANCHISE 82.7367, TOYS/GAMES 6.1959).

Remediation arithmetic (documented, deterministic, no multipliers on
reasoned rows - this only fires on rows already flagged incoherent):

  target = max(peer_max_in_category * 1.03, anchor * 0.30)
  target = min(target, anchor * 0.80, 99.0)
  + subject-salted 4dp jitter, kept off 2dp boundaries

Mirror-set handling honors profile-iq-pipeline-rules #3b: all flagged
own rows for the same brand across subcategory grids receive the SAME
target (one jitter per brand group), so TOYS/GAMES/MPB never drift
apart on the fix itself.
"""

from __future__ import annotations

import hashlib
import re

# Audience-noun suffix words that name the AUDIENCE, not the property.
# Stripped iteratively from the tail of the subject when deriving the
# own-property token. "Paw Patrol Series Viewers" -> "PAWPATROL".
AUDIENCE_SUFFIX_WORDS = {
    "VIEWERS", "VIEWER", "WATCHERS", "WATCHER", "FANS", "FAN",
    "AUDIENCE", "AUDIENCES", "UNIVERSE", "SERIES", "MOVIE", "FILM",
    "SHOW", "PODCAST", "ENTHUSIASTS", "ENTHUSIAST", "CUSTOMERS",
    "CUSTOMER", "BUYERS", "BUYER", "RENTERS", "RENTER", "SUBSCRIBERS",
    "SUBSCRIBER", "MEMBERS", "MEMBER", "OWNERS", "OWNER", "SHOPPERS",
    "SHOPPER", "EATERS", "DRINKERS", "LISTENERS", "LISTENER",
    "PLAYERS", "PLAYER", "USERS", "USER", "SWITCHERS", "SWITCHER",
    "AVID", "CASUAL", "TOTAL", "STREAMERS", "STREAMER", "READERS",
    "READER", "COLLECTORS", "COLLECTOR", "HOUSEHOLDS",
}

# Real titles whose final word collides with a suffix noun and must
# never be stripped (rule 4c-i precedent: Steven Universe).
REAL_TITLE_EXCEPTIONS = {
    "STEVENUNIVERSE", "THETWILIGHTSAGA", "AMERICANIDOL",
    "STRANGERTHINGS",
}

# I16 scope: the anchor grid asserts franchise-level engagement; the
# checked grids are the property's own merch / games / media rows.
ANCHOR_CATS = {"FRANCHISE"}
MERCH_CATS = {
    "TOYS", "GAMES", "MOST PURCHASED BRANDS", "APPAREL/FOOTWEAR",
    "ACCESSORIES", "MEDIA", "CPG",
}
ANCHOR_MIN = 40.0     # below this the franchise row makes no strong claim
RATIO_FLOOR = 0.14    # own merch must be >= 14% of the franchise anchor


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def own_token(subject) -> str:
    """Normalized subject token with trailing audience nouns stripped.

    "Paw Patrol Series Viewers" -> "PAWPATROL"
    "Bethenny Frankel"          -> "BETHENNYFRANKEL"
    "Steven Universe"           -> "STEVENUNIVERSE" (real-title exception)
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(subject or "").upper())
             if w]
    if not words:
        return ""
    if "".join(words) in REAL_TITLE_EXCEPTIONS:
        return "".join(words)
    while len(words) > 1 and words[-1] in AUDIENCE_SUFFIX_WORDS:
        words = words[:-1]
        if "".join(words) in REAL_TITLE_EXCEPTIONS:
            break
    return "".join(words)


def is_subject_own(subject, value) -> bool:
    """True when a row Value names the subject's own property.

    Containment runs against the audience-suffix-stripped token in
    both directions with a 5-char guard on the contained side, so
    "PAW PATROL" matches subject "Paw Patrol Series Viewers" but
    "SERIES" and "PARAMOUNT+" do not.
    """
    tok = own_token(subject)
    vn = _norm(value)
    if not tok or not vn:
        return False
    if vn == tok:
        return True
    if len(tok) >= 5 and tok in vn:
        return True
    if len(vn) >= 5 and vn in tok:
        return True
    return False


def _salted(subject, value, salt, lo, hi) -> float:
    h = int(hashlib.sha256(
        f"{subject}|{value}|{salt}".encode()).hexdigest()[:8], 16)
    return lo + (h % 10_000) / 10_000.0 * (hi - lo)


def _off_boundary(subject, value, bp, salt="spc-b") -> float:
    """4dp value nudged off exact 2dp boundaries."""
    bp = round(float(bp), 4)
    if int(round(bp * 10_000)) % 100 == 0:
        h = int(hashlib.sha256(
            f"{subject}|{value}|{salt}".encode()).hexdigest()[:8], 16)
        bp = round(bp + (1 + h % 89) / 10_000.0, 4)
    return bp


def coherence_target(subject, brand_value, anchor_bp, peer_max) -> float:
    """Documented arithmetic reconciliation for an incoherent own-merch
    row: position it just above the strongest peer in its grid (the
    property's own merch plausibly leads its own viewer base) with the
    franchise anchor bounding both sides, subject-salted to 4dp.
    """
    anchor_bp = float(anchor_bp)
    peer_max = float(peer_max or 0.0)
    target = max(peer_max * 1.03, anchor_bp * 0.30,
                 anchor_bp * RATIO_FLOOR * 1.15)
    target = min(target, anchor_bp * 0.80, 99.0)
    target += _salted(subject, brand_value, "spc-target", -0.35, 0.35)
    target = min(max(target, anchor_bp * RATIO_FLOOR * 1.05), 99.0)
    return _off_boundary(subject, brand_value, target)


def check_self_property_coherence(items, subject):
    """Detection shared by the enforcer, the gate (I16) and scanners.

    `items` is an iterable of (category_upper, value, bp_float). Returns
    (anchor_bp, violations) where violations is a list of dicts:
    {cat, val, bp, floor}. anchor_bp is None when the subject has no
    qualifying franchise anchor (check does not apply).
    """
    items = [(str(c or "").strip().upper(), v, b) for c, v, b in items]
    anchor_bp = None
    for cu, val, bp in items:
        if cu in ANCHOR_CATS and bp is not None and is_subject_own(
                subject, val):
            if anchor_bp is None or bp > anchor_bp:
                anchor_bp = bp
    if anchor_bp is None or anchor_bp < ANCHOR_MIN:
        return None, []
    floor = round(anchor_bp * RATIO_FLOOR, 4)
    out = []
    for cu, val, bp in items:
        if cu not in MERCH_CATS or bp is None:
            continue
        if not is_subject_own(subject, val):
            continue
        if bp < floor:
            out.append({"cat": cu, "val": val, "bp": bp, "floor": floor})
    return anchor_bp, out


def peer_max_in_category(items, category_upper, subject) -> float:
    """Strongest non-own, non-pin row in a grid (for target anchoring)."""
    best = 0.0
    cu_want = str(category_upper or "").strip().upper()
    for cu, val, bp in items:
        if str(cu or "").strip().upper() != cu_want or bp is None:
            continue
        if bp >= 99.5:          # pins / near-pins are not peers
            continue
        if is_subject_own(subject, val):
            continue
        if bp > best:
            best = float(bp)
    return best
