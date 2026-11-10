"""Account-holder universes are adults: the 17 AND UNDER age band is zero.

Grace Yin (Amazon), 2026-11-10, on the Hazbin Hotel Season 1 Prime
sign-ups profile: "Is age taken from the panelist's own profile, or is
it a household-level guess? We saw minors counted as 'sign-ups,' so it
matters for an 18 to 34 cut."

A universe defined by an action an adult takes on an account (sign-ups,
new subscribers, subscribers, members, paid, purchasers, buyers,
customers, shoppers, renters, bettors, account holders, cardholders,
bank customers) is adults. The canonical AGE schema carries a
17 AND UNDER bucket (19.3% in Gen Pop, right for a full-population
baseline), so a reasoned AGE split on such a universe shipped an
under-18 band. On these universes the bucket is 0.0000 and the adult
buckets renormalize to 100 with subject-salted micro-jitter (no
boundary values, no two equal values, the standing demo-sum
convention). A viewers / listeners / players / fans universe keeps the
bucket: minors watch.

Detection: the display name / subject label / file stem / BRAND INPUT
persona of the universe (the words above, as whole words), plus the
explicit ``account_holder: true`` spec flag the interpret step sets
when the ask says sign-ups / subscribers / buyers. The flag forces the
rule on; its absence falls back to the words, so a universe the
interpret step missed is still caught by its own name.

Single maintained implementation; ``bg-webapp/migration/`` carries a
byte-identical twin (scripts/module_twins.json). No pandas import here
so the dashboard, the engine and the enforcers can all share it.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional

__all__ = [
    "ACCOUNT_HOLDER_RX",
    "MINOR_AGE_BUCKET",
    "is_account_holder_text",
    "is_account_holder_universe",
    "is_minor_age_label",
    "zero_minor_age_distribution",
    "adult_age_rows",
    "apply_to_spec_demos",
    "adults_only_prompt_block",
    "account_holder_hint",
]

# The canonical bucket label the pipeline emits (PIPELINE_DEMO_SCHEMA).
MINOR_AGE_BUCKET = "17 and Under"

# A universe DEFINED by minors (a children's or teen cut: 'Potential Toy
# Purchasers Children' at 99.5 under 18, 'Tween Teen Yogurt Shopper' at
# 78.5) keeps its shape even when its name carries an account word; the
# rule is for adult account universes, never for a cut that pins the
# under-18 bucket. At or above this share the bucket is the universe.
MINOR_DEFINED_UNIVERSE_SHARE = 50.0

# An action an adult takes on an account. Whole words only, so 'Subs'
# (a sandwich) or 'Membership' inside a brand name never trip it; the
# plural and the hyphen / space spellings of sign-ups all count.
ACCOUNT_HOLDER_RX = re.compile(
    r"\b(?:"
    r"sign[\s-]?ups?|signups?|signed[\s-]up|"
    r"(?:new\s+)?subscribers?|"
    r"members?|"
    r"paid|"
    r"purchasers?|buyers?|customers?|shoppers?|renters?|"
    r"bettors?|"
    r"account[\s-]?holders?|accountholders?|"
    r"card[\s-]?holders?|cardholders?|"
    r"bank\s+customers?"
    r")\b",
    re.IGNORECASE,
)

# Age labels that name people under 18, in the spellings the pipeline,
# Gen Pop, the interpret step and the read worker have produced.
_MINOR_LABEL_RX = re.compile(
    r"^(?:"
    r"17\s*(?:and|&|or)\s*(?:under|younger|below)|"
    r"under\s*-?\s*18s?|"
    r"<\s*18|"
    r"17\s*or\s*less|"
    r"minors?|kids|children"
    r")$",
    re.IGNORECASE,
)
_RANGE_RX = re.compile(r"^\s*(\d{1,2})\s*(?:-|to|\u2013|\u2014)\s*(\d{1,2})\s*$", re.IGNORECASE)


def _clean(text) -> str:
    return " ".join(str(text or "").replace("_", " ").split())


def is_account_holder_text(text) -> bool:
    """True when the words name an account-holder universe."""
    s = _clean(text)
    if not s:
        return False
    return bool(ACCOUNT_HOLDER_RX.search(s))


def is_account_holder_universe(*labels, spec: Optional[dict] = None,
                               brand_input=None) -> bool:
    """The universe is adults: the spec says so (``account_holder:
    true``) or any of its labels (display name, subject label, file
    stem, SUBJECT row) or its BRAND INPUT persona carries one of the
    account-action words as a whole word. Explicit ``True`` forces the
    rule on; any other flag value defers to the words."""
    if isinstance(spec, dict):
        flag = spec.get("account_holder")
        if flag is True or (isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "1")):
            return True
    for lab in labels:
        if isinstance(lab, (list, tuple, set)):
            if any(is_account_holder_text(x) for x in lab):
                return True
        elif is_account_holder_text(lab):
            return True
    if brand_input is not None:
        if isinstance(brand_input, (list, tuple, set)):
            if any(is_account_holder_text(x) for x in brand_input):
                return True
        elif is_account_holder_text(brand_input):
            return True
    return False


def account_holder_hint(spec: Optional[dict]) -> bool:
    """The spec's own words: name, file stem, subject, universe
    description, BRAND INPUT terms, plus the explicit flag."""
    if not isinstance(spec, dict):
        return False
    labels = [spec.get("name"), spec.get("file_stem"), spec.get("subject"),
              spec.get("display_name"), spec.get("universe_description"),
              spec.get("subject_label")]
    bi = spec.get("brand_input") or spec.get("brand_input_value") or spec.get("universe_terms")
    return is_account_holder_universe(*labels, spec=spec, brand_input=bi)


def is_minor_age_label(label) -> bool:
    """True for an AGE bucket that names people under 18: the canonical
    '17 and Under', 'Under 18', '13-17', '0-17', 'minors'."""
    s = _clean(label).strip().strip(".").lower()
    if not s:
        return False
    if _MINOR_LABEL_RX.match(s):
        return True
    m = _RANGE_RX.match(s)
    if m:
        try:
            lo, hi = int(m.group(1)), int(m.group(2))
        except ValueError:
            return False
        return hi <= 17 and lo <= hi
    return False


def _salted_int(subject: str, salt: str, lo: int, hi: int) -> int:
    """Deterministic integer in [lo, hi] from blake2b(subject|salt)."""
    h = hashlib.blake2b(f"{subject}|adult-universe|{salt}".encode("utf-8"), digest_size=8).hexdigest()
    span = hi - lo + 1
    return lo + (int(h, 16) % span) if span > 0 else lo


def _renormalize_units(adults: dict, subject: str) -> dict:
    """Adult buckets rescaled to exactly 100.0000 in 1e-4 units, with a
    salted nudge of 1 to 9 units per bucket (zero-sum against the
    largest bucket), no bucket on a 2dp boundary, no two buckets equal,
    none at or below zero."""
    labels = list(adults)
    tot = float(sum(max(0.0, float(v)) for v in adults.values()))
    if tot <= 0 or not labels:
        return {k: float(v) for k, v in adults.items()}
    units = {k: int(round(max(0.0, float(adults[k])) * 100.0 / tot * 10000)) for k in labels}
    for k in labels:
        units[k] += _salted_int(subject, k, -9, 9) or 3
    order = sorted(labels, key=lambda k: -units[k])
    big = order[0]
    partner_for = {k: (big if k != big else (order[1] if len(order) > 1 else big)) for k in labels}
    units[big] += 1_000_000 - sum(units.values())

    def _bad(k):
        if units[k] <= 0 or units[k] % 100 == 0:
            return True
        return any(units[o] == units[k] for o in labels if o != k)

    for _round in range(6):
        moved = False
        for k in labels:
            guard = 0
            while _bad(k) and guard < 40:
                step = 1 + _salted_int(subject, f"{k}|{_round}|{guard}", 0, 6)
                partner = partner_for[k]
                if partner == k:
                    break
                units[k] += step
                units[partner] -= step
                guard += 1
                moved = True
        if not moved:
            break
    assert sum(units.values()) == 1_000_000
    return {k: units[k] / 10000.0 for k in labels}


def zero_minor_age_distribution(dist: dict, subject: str, tolerance: float = 0.0005):
    """({label: pct} with every under-18 bucket at 0.0 and the adult
    buckets renormalized to exactly 100.0000, changed).

    Idempotent: when no under-18 bucket carries mass and the adult
    buckets already sum to 100 within ``tolerance`` the input comes
    back untouched (changed False). A universe defined by minors (the
    under-18 bucket at or above MINOR_DEFINED_UNIVERSE_SHARE) is a
    children's or teen cut and comes back untouched too. Labels keep
    their spelling and order."""
    if not isinstance(dist, dict) or not dist:
        return dict(dist or {}), False
    minors = [k for k in dist if is_minor_age_label(k)]
    adults = {}
    for k, v in dist.items():
        if k in minors:
            continue
        try:
            adults[k] = float(v)
        except (TypeError, ValueError):
            adults[k] = 0.0
    if not adults:
        return dict(dist), False
    minor_mass = 0.0
    for k in minors:
        try:
            minor_mass += max(0.0, float(dist.get(k) or 0.0))
        except (TypeError, ValueError):
            pass
    adult_sum = sum(adults.values())
    if minor_mass <= tolerance and abs(adult_sum - 100.0) <= tolerance:
        return dict(dist), False
    if minor_mass >= MINOR_DEFINED_UNIVERSE_SHARE:
        # a children's / teen cut: the under-18 bucket IS the universe
        return dict(dist), False
    fixed = _renormalize_units(adults, subject)
    out = {}
    for k in dist:
        out[k] = 0.0 if k in minors else fixed[k]
    return out, True


def adult_age_rows(rows: Iterable, subject: str, *, label_key: str = "label", pct_key: str = "pct"):
    """Row dicts ({label, pct}) for an account-holder universe: the
    under-18 rows leave and the rest renormalize to 100 (same rule as
    the file). Rows keep their other keys. Input order is kept."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []
    dist = {}
    for r in rows:
        try:
            dist[str(r.get(label_key))] = float(r.get(pct_key) or 0.0)
        except (TypeError, ValueError):
            dist[str(r.get(label_key))] = 0.0
    fixed, _changed = zero_minor_age_distribution(dist, subject)
    out = []
    for r in rows:
        lab = str(r.get(label_key))
        if is_minor_age_label(lab):
            continue
        q = dict(r)
        q[pct_key] = fixed.get(lab, r.get(pct_key))
        out.append(q)
    return out


def apply_to_spec_demos(spec: dict, *, force: Optional[bool] = None) -> int:
    """On an account-holder spec, zero the under-18 AGE bucket in
    ``tu_demos`` / ``avid_demos`` and stamp ``account_holder: True``.
    Returns the number of demo blocks changed. ``force`` overrides the
    detection (True runs the rule, False skips it)."""
    if not isinstance(spec, dict):
        return 0
    hit = account_holder_hint(spec) if force is None else bool(force)
    if not hit:
        return 0
    spec["account_holder"] = True
    subject = str(spec.get("name") or spec.get("file_stem") or "")
    changed = 0
    for key in ("tu_demos", "avid_demos"):
        demos = spec.get(key)
        if not isinstance(demos, dict):
            continue
        for cat in list(demos):
            if str(cat).strip().upper() != "AGE" or not isinstance(demos[cat], dict):
                continue
            fixed, did = zero_minor_age_distribution(demos[cat], subject)
            if did:
                demos[cat] = fixed
                changed += 1
    return changed


def adults_only_prompt_block(subject: str = "") -> str:
    """The reasoning note for an account-holder universe: adults only."""
    who = f"'{subject}'" if subject else "this universe"
    return (
        "ACCOUNT-HOLDER UNIVERSE (ADULTS ONLY):\n"
        f"  Everyone in {who} took an action on an account an adult\n"
        "  holds (a sign-up, a subscription, a membership, a purchase, a\n"
        "  rental, a bet, a card). The universe is adults, 18 and older.\n"
        "  The AGE shape carries no under-18 share: 17 AND UNDER is\n"
        "  0.0000 and the adult buckets sum to 100. Reason every row for\n"
        "  adults only; never attribute a value to minors."
    )
