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

# Extra leading words a row value may carry before the subject token and
# still name the same property ("THE PAW PATROL MOVIE"). Content words
# in front (PEREZ Hilton, FIRST Citizens Bank, KELDON Johnson) name a
# DIFFERENT entity and must not match.
_LEAD_ARTICLES = {"THE", "A", "AN"}

# Device nouns that ride behind a brand in owner-universe subjects
# ("Vizio TV Owners" -> VIZIO TV -> VIZIO also matches bare "Vizio").
_DEVICE_TAIL_WORDS = {"TV", "TVS"}

# I16 scope: the anchor grid asserts franchise-level engagement; the
# checked grids are the property's own merch / games / media rows.
ANCHOR_CATS = {"FRANCHISE"}
MERCH_CATS = {
    "TOYS", "GAMES", "MOST PURCHASED BRANDS", "APPAREL/FOOTWEAR",
    "ACCESSORIES", "MEDIA", "CPG",
}
ANCHOR_MIN = 40.0     # below this the franchise row makes no strong claim
RATIO_FLOOR = 0.14    # own merch must be >= 14% of the franchise anchor

# ---------------------------------------------------------------------------
# Own-property / owner-platform pin convention (Jenna 2026-08-26,
# verbatim: "for paw patrol paramount+ should be 100% as should paw
# patrol ... if it is its own property it should be 100%. also please
# vet apple tv+ cuts to make sure apple tv+ is 100%").
#
# The subject's OWN PROPERTY row (FRANCHISE "Paw Patrol") and the
# OWNING / UNIVERSE-DEFINING platform row (Paramount+ on a Paw Patrol
# universe; Apple TV+ on an Apple TV+-scoped universe; the standing
# viewers-carriage precedent Jimmy Kimmel viewers -> Disney+/Hulu)
# read exactly 100.0000 in the BASE file AND every derived cut. This
# is the standard subject self-pin exception, extended through the
# audience-suffix-stripped matcher and a verified-ownership map.
# Merch grids (TOYS/GAMES/MPB) stay at reasoned levels - the pin is
# for the property row and the platform row only.
# ---------------------------------------------------------------------------

# Content categories where the subject's own row IS the property.
OWN_PROPERTY_PIN_CATS = {
    "FRANCHISE", "MOVIE", "TV SHOW", "PODCAST", "VERTICAL SHORTS",
    "CONTENT",
}

# Streaming/platform categories where a universe-defining platform row
# pins at exact 100 (the REQUIREMENT set - deliberately narrower than
# the exemption set below).
PLATFORM_PIN_CATS = {
    "STREAMING/PLATFORM", "STREAMING VIDEO", "STREAMING MUSIC",
    "VMVPD/FAST", "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD",
    "FAST PLATFORM", "FAST CHANNEL", "APP/PLATFORM", "PLATFORMS",
}

# Platform / carriage categories where an exact-100 row is EXEMPT from
# the I18 reach-pin flag when it is owner-verified, cut-defining, or
# the single carrier of a consumption-scoped universe (mirrors
# CARRIER_PIN_SOFT_CATS in post_generation_enforcers).
CARRIER_EXEMPT_CATS = PLATFORM_PIN_CATS | {
    "BROADCAST/CABLE", "MOVIE THEATER", "MEDIA", "SOCIAL MEDIA",
    "SEARCH ENGINE/AI",
}

# Curated verified-ownership map: own-property token -> normalized
# platform tokens whose row pins at exact 100 on that subject's files.
# Seeded per Jenna 2026-08-26 (Paw Patrol is a Paramount property).
# Extend only with VERIFIED ownership/carriage; the live source for
# future builds is migration/viewer_carriage.research_carriage.
OWNER_PLATFORM_MAP = {
    "PAWPATROL": {"PARAMOUNT", "PARAMOUNTPLUS"},
}


def is_own_property_pin_cat(cat_u) -> bool:
    cu = str(cat_u or "").strip().upper()
    return cu in OWN_PROPERTY_PIN_CATS or cu.startswith("SERIES")


def _value_parts(value):
    sval = str(value or "")
    parts = [sval]
    if "/" in sval:
        parts += [p for p in sval.split("/") if p.strip()]
    return parts


def is_owner_platform_row(subject, value) -> bool:
    """True when the row Value is a platform verified as OWNING /
    carrying the subject's property (curated OWNER_PLATFORM_MAP)."""
    owners = OWNER_PLATFORM_MAP.get(own_token(subject))
    if not owners:
        return False
    return any(_norm(p) in owners for p in _value_parts(value))


def must_pin_100(subject, cat_u, value) -> bool:
    """True when this row is REQUIRED at exactly 100.0000 (base file
    and every derived cut) by the own-property / owner-platform pin
    convention. Gate I17 flags rows where this is true and BP != 100;
    the pin_own_property_rows enforcer sets them to 100."""
    cu = str(cat_u or "").strip().upper()
    if is_own_property_pin_cat(cu) and is_subject_own_exact(subject, value):
        return True
    if cu in PLATFORM_PIN_CATS and (
            is_owner_platform_row(subject, value)
            or is_subject_own(subject, value)):
        return True
    return False


def exact_100_exempt(subject, cat_u, value, cut_label=None,
                     carrier_domains=None) -> bool:
    """True when an exact-100 reading on this row is LEGITIMATE (so
    I18 must not flag it and the de-pin enforcer must not touch it):
    required pins, owner-verified platform rows in any carriage
    category, the cut-defining platform of a platform-scoped cut, and
    the single carrier of a single-platform viewer universe.

    `carrier_domains` is the list of platform domains found in the
    file's BRAND INPUT; when exactly one is present AND its root
    matches this row's value, the row IS the universe's one carrier
    and its exact 100 stands (a Landman viewer by definition reached
    Paramount+). A different platform at 100 on the same file is NOT
    exempt."""
    cu = str(cat_u or "").strip().upper()
    if must_pin_100(subject, cu, value):
        return True
    if is_owner_platform_row(subject, value):
        return True
    if cu in CARRIER_EXEMPT_CATS:
        doms = list(carrier_domains or [])
        if len(doms) == 1:
            root = re.sub(r"[^a-z0-9]", "",
                          str(doms[0]).lower().split("/")[0].split(".")[0])
            if root:
                for p in _value_parts(value):
                    pn = _norm(p).lower()
                    if pn and (root in pn or pn in root):
                        return True
        cn = _norm(cut_label)
        if cn and len(cn) >= 3:
            vn = _norm(value)
            if cn in vn or vn in cn:
                return True
    return False


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _words(s) -> list:
    return [w for w in re.split(r"[^A-Za-z0-9]+", str(s or "").upper()) if w]


def own_token_words(subject) -> list:
    """Subject word list with trailing audience nouns stripped."""
    words = _words(subject)
    if not words:
        return []
    if "".join(words) in REAL_TITLE_EXCEPTIONS:
        return words
    while len(words) > 1 and words[-1] in AUDIENCE_SUFFIX_WORDS:
        words = words[:-1]
        if "".join(words) in REAL_TITLE_EXCEPTIONS:
            break
    return words


def own_token(subject) -> str:
    """Normalized subject token with trailing audience nouns stripped.

    "Paw Patrol Series Viewers" -> "PAWPATROL"
    "Bethenny Frankel"          -> "BETHENNYFRANKEL"
    "Steven Universe"           -> "STEVENUNIVERSE" (real-title exception)
    """
    return "".join(own_token_words(subject))


def _token_variants(subject) -> list:
    base = own_token_words(subject)
    variants = [base] if base else []
    if len(base) >= 2 and base[-1] in _DEVICE_TAIL_WORDS:
        variants.append(base[:-1])
    return variants


def _match_words(tok_words, val_words) -> bool:
    """Word-level own-property match (2026-08-26 precision pass).

    The first cut of this matcher used joined-char containment and the
    corpus scan showed why that cannot ship: LEXUS matched inside
    ALEXUS, CELINE inside PRICELINE, HOMES inside MAHOMES, MICHAELS
    against AL MICHAELS - all different entities that an auto-lift
    would have corrupted. Matching is word-level with three forms:

      1. exact join equality        (PAW PATROL == PAW.Patrol)
      2. tok consecutive in value, extra LEADING words limited to
         articles                   (THE PAW PATROL MOVIE yes;
                                     PEREZ HILTON / FIRST CITIZENS
                                     BANK no)
      3. value (>= 2 words) an ordered subsequence of tok anchored on
         tok's first word           (DWAYNE JOHNSON matches subject
                                     Dwayne The Rock Johnson; JIMMY
                                     KIMMEL matches Jimmy Kimmel Live
                                     Viewers; CITIZENS BANK does NOT
                                     match First Citizens Bank)
    """
    if not tok_words or not val_words:
        return False
    tok_join = "".join(tok_words)
    if len(tok_join) < 2:
        return False
    if "".join(val_words) == tok_join:
        return True
    n, m = len(tok_words), len(val_words)
    # Single very-short tokens (BET, NBC) extend into co-brand names
    # that are NOT the subject's own (BET MGM, BET RIVERS): prefix
    # extension needs a token of at least 4 chars.
    if n == 1 and len(tok_join) < 4:
        return False
    if n <= m:
        for i in range(m - n + 1):
            if (val_words[i:i + n] == tok_words
                    and all(w in _LEAD_ARTICLES for w in val_words[:i])):
                return True
    if m >= 2 and val_words[0] == tok_words[0]:
        ti = 1
        for v in val_words[1:]:
            while ti < n and tok_words[ti] != v:
                ti += 1
            if ti >= n:
                return False
            ti += 1
        return True
    return False


def is_subject_own(subject, value) -> bool:
    """True when a row Value names the subject's own property.

    Word-level match against the audience-suffix-stripped token, so
    "PAW PATROL" matches subject "Paw Patrol Series Viewers" but
    "SERIES", "PARAMOUNT+", and different-entity near-names (LEXUS vs
    ALEXUS, MICHAELS vs AL MICHAELS) do not. Slash-separated values
    match on any part (Disney+/Hulu is Hulu's own row).
    """
    variants = _token_variants(subject)
    if not variants:
        return False
    sval = str(value or "")
    parts = [sval]
    if "/" in sval:
        parts += [p for p in sval.split("/") if p.strip()]
    for tw in variants:
        for p in parts:
            if _match_words(tw, _words(p)):
                return True
    return False


def is_subject_own_exact(subject, value) -> bool:
    """Strict form: the value IS the subject token (no extensions).

    Used where remediation moves numbers on the matched row itself
    (I16 merch rows): "PAW PATROL" qualifies, "DISNEY ARIEL" on
    subject Disney+ does not - a sub-line SKU can legitimately sit
    low without contradicting the franchise anchor.
    """
    vn = _norm(value)
    if not vn:
        return False
    return any("".join(tw) == vn for tw in _token_variants(subject))


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
        # Exact-token rows only: the band asserts the property's own
        # FLAGSHIP merch can't sit at noise. Extension rows (DISNEY
        # ARIEL on subject Disney+) are sub-lines that may sit low.
        if not is_subject_own_exact(subject, val):
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
