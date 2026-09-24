"""Add-on cut synthesis: generalized demo-pin cuts off a parent profile.

2026-08-19 (Jenna guided-flow directive): the chat's clarify step lets a
user attach add-on cuts to a build - "female only", "by generation",
"ages 18-34", "Los Angeles only" - at 3 credits per cut. Gender cuts
route to the proven `audience_cut_synthesis.synthesize_audience_cut`.
Everything else (age bands, generations, DMA cuts) routes here.

This module generalizes the gender-cut machine: instead of a hard
GENDER pin it pins ANY demo category to a SET of buckets:

    cut_def = {
        'cut_id': 'millennials',
        'label': 'Millennials (25-44)',
        'kind': 'generation' | 'age_band' | 'dma' | 'demo' | 'behavioral',
        'pin_category': 'AGE' | 'LOCATION' | ...,
        'pin_buckets': ['25-34', '35-44'],
    }

2026-08-25 (Jenna audience-operators mandate: requests carrying
operators the system can't express must be handled, never silently
flattened): two more spec shapes route here.

COMPOUND MULTI-PIN cuts - "male millennials in LA" as ONE audience:

    cut_def = {
        'compound': {
            'label': 'Male Millennials in Los Angeles',
            'pins': [
                {'category': 'GENDER',   'buckets': ['MALE']},
                {'category': 'AGE',      'buckets': ['25-34', '35-44']},
                {'category': 'LOCATION', 'buckets': ['Los Angeles Ca']},
            ],
        },
    }

  Sizing = TU sample x the PRODUCT of each pinned dimension's TU share
  (independence is the modeling assumption; each factor is read off
  the parent's own rows), then ensure_messy_sample_size. Every pinned
  category renders like an existing pin cut (~99.99 jittered dominance
  split proportional to TU ratios, non-targets near zero, sums to
  100). A LOCATION pin inside a compound behaves like a geo cut for
  that market: only that market elevated, other markets near zero,
  LOCATION sums to 100 (kept, not dropped). Non-pinned demos anchor to
  the parent via cut_demo_anchor. Naming: '{Subject} - {label}'.

REGION MULTI-DMA cuts - "the Southeast" as one geo cut:

    cut_def = {
        'region_label': 'Southeast',
        'region_dmas': ['Atlanta Ga', 'Miami Ft Lauderdale Fl', ...],
    }

  Normalizes to a LOCATION pin over the listed DMAs with
  kind='region'. Sizing = TU sample x SUM of the parent's LOCATION
  shares for the listed DMAs. The listed markets are elevated and
  renormalized to ~100 in proportion to their TU shares; every other
  market reads near zero (kept, NOT dropped - unlike single-DMA
  cuts). Naming: '{Subject} - Southeast'. When region_dmas is absent,
  the DMA list resolves from migration/us_regions.REGION_TO_DMAS
  (import-guarded; the cut fails loudly if neither is available).

2026-08-20 (Jenna intersect-cut directive): `kind: 'behavioral'` covers
cuts defined by BEHAVIOR rather than a demo bucket - e.g. "Spider-Man
Moviegoers -> Digital Purchasers" as a cut of "Vizio TV Owners". There
is no pin category (`pin_category: None`, `pin_buckets: []`) and no
deterministic cohort fraction to read off the parent, so the fraction
comes from the Phase 1 reasoning call (grounded in real-world
attendance / purchase / adoption data), clamped to sane bounds. Row-by-
row reasoning, no-collision enforcement, sample sizing, and naming
('{Parent} - {Label}.csv') are identical to demo cuts.

Invariants (same rulebook as every skin - see
.cursor/rules/avid-and-cut-skin-rules.mdc):
  * NO multipliers / flat lifts. Every brand BP comes from row-by-row
    Claude reasoning against the cut persona; undecided rows get
    source BP + subject-salted ±0.10pp jitter only.
  * ALL non-demo categories, ALL rows (chunked, no top-N).
  * Sample size is the ONLY calculation: new_sample = source_sample x
    cohort_fraction, where cohort_fraction is DETERMINISTIC from the
    source's own pin-category rows (sum of pinned bucket BPs / 100).
    Jenna 2026-08-24: the deterministic fraction ALWAYS WINS over any
    reasoned or spec-provided value. AGE cuts with a range that only
    partially overlaps a panel bucket allocate that bucket
    proportionally by years covered (see _age_range_fraction); pin
    buckets in the FILE stay whole panel breaks - only the sample
    fraction is proportional.
  * No value may 4dp-collide with the source (enforce_no_collisions).
  * Cuts ladder up: `enforce_partition_coherence` slides levels (never
    multiplies) when a requested cut set covers >= 90% of the parent.
  * Pipeline invariants: 4dp BPs, demo cats sum to 100, subject
    self-pin preserved, BP -> Raw -> Projection recomputed, messy
    (never round) sample sizes.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_ROOT = os.path.dirname(HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Same helper set the gender-cut module reuses.
from avid_fan_row_by_row import (  # noqa: E402
    BUCKET, REGION,
    _load_source_df, _seed_jitter, _fbp,
    enforce_no_collisions,
    DEMO_CATS_TF, SUBJECT_PIN_CATS_TF,
    _detect_cols,
    override_with_deterministic_fraction,
)
from super_fan_synthesis import (  # noqa: E402
    build_source_snapshot, _extract_json_block,
)
from audience_cut_synthesis import (  # noqa: E402
    _CAT_ROW_SYSTEM, SKIP_CATS,
)

try:
    from scripts._sample_size_jitter import ensure_messy_sample_size
except Exception:  # pragma: no cover - scripts/ not on path in odd envs
    def ensure_messy_sample_size(subj, v, **kw):
        v = int(v or 0) or 9873
        return v + 7 if v % 10 == 0 else v


# =============================================================================
# Deterministic cohort fraction from the parent's own pin-category rows
# (Jenna 2026-08-24, verbatim: "make sure the cut sasmple sizes match
# the total universe and update pipeline to ensure that. if 42.50% of
# the TU is male then the male file should have a sample size of
# 42.50% of the sample size of the TU. millinneal should be equal to
# the number that showed up in the TU")
# =============================================================================

# Canonical AGE panel breaks (PIPELINE_DEMO_SCHEMA labels) with their
# year spans, for proportional allocation of partial-overlap ranges.
# The open-ended 65+ bucket is spanned to 120 so a partial slice of it
# is deliberately conservative.
_AGE_BUCKET_SPANS = [
    ("17 AND UNDER", 0, 17),
    ("18-24", 18, 24), ("25-34", 25, 34), ("35-44", 35, 44),
    ("45-54", 45, 54), ("55-64", 55, 64), ("65 OR OLDER", 65, 120),
]

_AGE_RANGE_RE = re.compile(r"^\s*(\d{1,2})\s*(?:-|\bto\b)\s*(\d{1,3})\s*$",
                           re.IGNORECASE)
_AGE_PLUS_RE = re.compile(r"^\s*(\d{1,2})\s*\+\s*$")


def _norm_bucket(pin_category: str, label) -> str:
    """Canonical uppercased bucket label for alias-tolerant matching.

    Routes through migration.canonical_demos.canonical_value so parent
    files carrying legacy spellings ('65+', 'Under 18', 'Non Binary')
    still resolve to the PIPELINE_DEMO_SCHEMA bucket. Categories not
    in the schema (e.g. LOCATION) pass through as plain uppercase.
    """
    s = str(label or "").strip()
    try:
        try:
            from canonical_demos import canonical_value
        except ImportError:
            from migration.canonical_demos import canonical_value
        c = canonical_value(str(pin_category or "").upper(), s)
        if isinstance(c, str) and c:
            return c.upper()
    except Exception:
        pass
    return s.upper()


def _resolve_location_buckets(df_source, cut_def: dict) -> dict:
    """DMA pin labels resolve against the parent's ACTUAL market rows
    (2026-09-24 Jenna: 'tighten it up so that it realizes the dma name
    match against orlando').

    A cut asked as 'Orlando Fl' must bind the parent's row 'Orlando
    Daytona Beach Melbourne Fl' - market rows carry the full Nielsen
    DMA name while asks carry the city + state. Resolution per
    requested bucket:
      1. exact uppercase match (unchanged fast path);
      2. token containment: every requested token appears in exactly
         ONE market row's token set.
    Zero matches or an ambiguous containment RAISES loudly - a silent
    no-match shipped the chiropractic package without its Orlando cut
    on 2026-09-23. Non-LOCATION cuts pass through untouched."""
    try:
        if str(cut_def.get("pin_category") or "").strip().upper() \
                != "LOCATION":
            return cut_def
        buckets = [str(b).strip() for b in
                   (cut_def.get("pin_buckets") or []) if str(b).strip()]
        if not buckets:
            return cut_def
        cats = df_source["Column"].astype(str).str.upper().str.strip()
        rows = [str(v).strip() for v in
                df_source[cats == "LOCATION"]["Value"].astype(str)
                .tolist() if str(v).strip()]
    except Exception:
        return cut_def
    row_toks = {r: set(r.upper().replace("-", " ").split())
                for r in rows}
    resolved = []
    for want in buckets:
        wu = want.upper()
        exact = [r for r in rows if r.upper() == wu]
        if exact:
            resolved.append(exact[0])
            continue
        wt = set(wu.replace("-", " ").split())
        hits = [r for r, ts in row_toks.items() if wt and wt <= ts]
        if len(hits) == 1:
            print(f"[addon-cut] LOCATION pin {want!r} resolved to "
                  f"parent market row {hits[0]!r}")
            resolved.append(hits[0])
            continue
        raise RuntimeError(
            f"LOCATION pin {want!r} matches {len(hits)} market rows on "
            f"the parent"
            + (f" ({', '.join(hits[:4])})" if hits else "")
            + "; name the market the way the file carries it")
    out = dict(cut_def)
    out["pin_buckets"] = resolved
    return out


def _pin_shares_from_source(df_source, pin_category: str,
                            pin_buckets: list) -> tuple:
    """Returns (cohort_fraction, {bucket_label: source_bp}).

    cohort_fraction = sum of the pinned buckets' BPs / 100, read
    straight off the SOURCE file. This is the skin rule's "sample
    sizing is the only place we use a calculation" - math, not vibes.
    Bucket matching is alias-tolerant via _norm_bucket so canonical
    and legacy spellings on either side resolve to the same break.
    """
    cat_col, val_col = "Column", "Value"
    bp_col, _, _, _ = _detect_cols(df_source)
    cats = df_source[cat_col].astype(str).str.upper().str.strip()
    want = {_norm_bucket(pin_category, b) for b in pin_buckets}
    shares = {}
    for _, r in df_source[cats == str(pin_category).upper().strip()
                          ].iterrows():
        lbl = str(r.get(val_col, "")).strip()
        if _norm_bucket(pin_category, lbl) not in want:
            continue
        v = _fbp(r.get(bp_col, 0))
        if v is not None:
            shares[lbl] = v
    frac = sum(shares.values()) / 100.0
    return max(0.0, min(1.0, frac)), shares


def _parse_age_range(cut_def: dict):
    """(lo, hi) age range for an AGE cut, or None.

    Priority: explicit `age_range` on the cut_def, then a parseable
    range in name_label / label ('18-30', '25 to 44', '65+'). Catalog
    names ('Millennials', 'Gen Z') do not parse and fall back to the
    whole-bucket sum.
    """
    rng = cut_def.get("age_range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        try:
            lo, hi = int(rng[0]), int(rng[1])
            if 0 <= lo < hi <= 120:
                return lo, hi
        except (TypeError, ValueError):
            pass
    for k in ("name_label", "label"):
        s = str(cut_def.get(k) or "").strip()
        m = _AGE_RANGE_RE.match(s)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if 0 <= lo < hi <= 120:
                return lo, hi
        m = _AGE_PLUS_RE.match(s)
        if m:
            lo = int(m.group(1))
            if 0 <= lo < 120:
                return lo, 120
    return None


def _age_range_fraction(df_source, lo: int, hi: int) -> tuple:
    """(cohort_fraction, {bucket: source_bp}, note) for an age RANGE.

    fraction = sum over the parent's AGE buckets of
    bucket_bp x (years of the bucket covered by the range / bucket
    span). A range that fully covers its buckets (Millennials 25-44 =
    25-34 + 35-44) reduces to the plain bucket sum; a partial overlap
    (18-30 takes 6 of 25-34's 10 years) is allocated proportionally
    and documented in the returned note.
    """
    cat_col, val_col = "Column", "Value"
    bp_col, _, _, _ = _detect_cols(df_source)
    cats = df_source[cat_col].astype(str).str.upper().str.strip()
    spans = {lbl: (b_lo, b_hi) for lbl, b_lo, b_hi in _AGE_BUCKET_SPANS}
    total = 0.0
    shares = {}
    partial = []
    for _, r in df_source[cats == "AGE"].iterrows():
        lbl_raw = str(r.get(val_col, "")).strip()
        lbl = _norm_bucket("AGE", lbl_raw)
        if lbl not in spans:
            continue
        b_lo, b_hi = spans[lbl]
        overlap = min(hi, b_hi) - max(lo, b_lo) + 1
        if overlap <= 0:
            continue
        v = _fbp(r.get(bp_col, 0))
        if v is None:
            continue
        w = min(1.0, overlap / (b_hi - b_lo + 1))
        shares[lbl_raw] = v
        total += v * w
        if w < 1.0:
            partial.append(f"{lbl_raw} x {overlap}/{b_hi - b_lo + 1} years")
    note = ""
    if partial:
        note = (f"AGE range {lo}-{hi}: proportional allocation on "
                + ", ".join(partial))
    return max(0.0, min(1.0, total / 100.0)), shares, note


def deterministic_cut_fraction(df_source, cut_def: dict) -> tuple:
    """(cohort_fraction, {bucket: source_bp}, note), resolved
    DETERMINISTICALLY from the parent file's own rows at synthesis
    time. This value ALWAYS WINS over any reasoned or spec-provided
    cohort_fraction (Jenna 2026-08-24).

    Routing:
      * AGE cut carrying an explicit range -> proportional bucket
        allocation via _age_range_fraction (whole-bucket ranges reduce
        to the plain sum).
      * every other pin cut (AGE buckets, LOCATION/DMA, GENDER,
        ETHNICITY, INCOME, any demo category) -> sum of the pinned
        buckets' parent BPs / 100.
      * behavioral cuts (no pin_category) -> (None, {}, note): there
        is no parent row to read, the reasoned fraction applies.
    """
    if _is_behavioral_cut(cut_def):
        return None, {}, ("behavioral cut - no parent row to read; "
                          "reasoned fraction applies")
    pins = _cut_pins(cut_def)
    if _is_compound_cut(cut_def) or len(pins) > 1:
        # Compound multi-pin (2026-08-25): fraction = PRODUCT of each
        # pinned dimension's parent share (independence assumption,
        # every factor read straight off the parent's own rows).
        # Shares return NESTED by category: {CAT: {bucket: bp}}.
        frac = 1.0
        shares_by_cat = {}
        factors = []
        for cat, buckets in pins:
            f, sh = _pin_shares_from_source(df_source, cat, buckets)
            if f <= 0:
                return None, {}, (
                    f"compound pin {cat}={buckets} not found in "
                    f"source - cannot size deterministically")
            frac *= f
            shares_by_cat[cat] = sh
            factors.append(f"{cat}={f:.4f}")
        frac = max(0.0, min(1.0, frac))
        return ((frac if frac > 0 else None), shares_by_cat,
                "compound product of parent shares: "
                + " x ".join(factors))
    pin_cat = str(cut_def.get("pin_category") or "").upper().strip()
    if pin_cat == "AGE":
        rng = _parse_age_range(cut_def)
        if rng:
            frac, shares, note = _age_range_fraction(df_source, *rng)
            if frac > 0:
                return frac, shares, (
                    note or f"AGE range {rng[0]}-{rng[1]}: whole "
                            f"panel buckets, plain sum")
    frac, shares = _pin_shares_from_source(
        df_source, pin_cat, cut_def.get("pin_buckets") or [])
    return ((frac if frac > 0 else None), shares,
            f"sum of parent {pin_cat} bucket BPs / 100")


# =============================================================================
# Phase 1 - audience reasoning (demo targets for the NON-pinned demos)
# =============================================================================
def _cut_pins(cut_def: dict) -> list:
    """[(CATEGORY, [bucket, ...]), ...] for this cut.

    One entry for a plain pin cut, several for a compound multi-pin
    cut (2026-08-25), empty for behavioral cuts. Compound entries are
    read from cut_def['compound']['pins']; a plain cut falls back to
    the classic pin_category / pin_buckets pair.
    """
    comp = cut_def.get("compound")
    if isinstance(comp, dict) and comp.get("pins"):
        out = []
        for p in comp.get("pins") or []:
            if not isinstance(p, dict):
                continue
            cat = str(p.get("category") or "").upper().strip()
            buckets = [str(b).strip() for b in (p.get("buckets") or [])
                       if str(b).strip()]
            if cat and buckets:
                out.append((cat, buckets))
        return out
    pc = str(cut_def.get("pin_category") or "").upper().strip()
    if pc:
        return [(pc, [str(b).strip()
                      for b in (cut_def.get("pin_buckets") or [])
                      if str(b).strip()])]
    return []


def _is_compound_cut(cut_def: dict) -> bool:
    comp = cut_def.get("compound")
    return (isinstance(comp, dict) and bool(comp.get("pins"))) \
        or str(cut_def.get("kind") or "").strip().lower() == "compound"


def _is_region_cut(cut_def: dict) -> bool:
    return (str(cut_def.get("kind") or "").strip().lower() == "region"
            or bool(str(cut_def.get("region_label") or "").strip())
            or bool(cut_def.get("region_dmas")))


def _is_behavioral_cut(cut_def: dict) -> bool:
    if str(cut_def.get("kind") or "").strip().lower() == "behavioral":
        return True
    return not _cut_pins(cut_def)


def _pin_desc(cut_def: dict) -> str:
    """Human-readable pin description for prompts and logs, covering
    single pins, compound multi-pins, and behavioral cohorts."""
    if _is_behavioral_cut(cut_def):
        return f"behavioral cohort: {_cohort_desc(cut_def)}"
    return "; ".join(f"{c} pinned to {', '.join(bs)}"
                     for c, bs in _cut_pins(cut_def))


def _resolve_region_dmas(region_label: str, region_dmas) -> tuple:
    """(canonical_label, [dma, ...]) for a region cut. Explicit
    region_dmas wins (label passes through as given); otherwise the
    label resolves through migration/us_regions (alias-tolerant,
    import-guarded). Fails loudly when neither is available: a region
    cut with no market list cannot be sized or pinned, and silent
    flattening is banned."""
    label = str(region_label or "").strip()
    dmas = [str(d).strip() for d in (region_dmas or []) if str(d).strip()]
    if dmas:
        return label, dmas
    try:
        try:
            from us_regions import region_dmas as _region_dmas
        except ImportError:
            from migration.us_regions import (  # type: ignore
                region_dmas as _region_dmas,
            )
    except Exception:
        raise RuntimeError(
            f"region cut {label!r} carries no region_dmas and "
            f"migration/us_regions.py is unavailable - cannot resolve "
            f"the market list")
    _canon, _dmas = _region_dmas(label)
    if _canon and _dmas:
        return (str(_canon).strip(),
                [str(d).strip() for d in _dmas if str(d).strip()])
    raise RuntimeError(
        f"region cut {label!r} not found in us_regions.REGION_TO_DMAS "
        f"and no region_dmas provided")


def _normalize_cut_def(cut_def: dict) -> dict:
    """Fold the 2026-08-25 spec shapes into the classic cut_def form.

    * compound: label/name_label lifted from compound['label'];
      kind='compound'; the FIRST pin doubles as pin_category /
      pin_buckets so legacy single-pin readers stay coherent, while
      multi-pin consumers read _cut_pins().
    * region_label (+ optional region_dmas): normalizes to a LOCATION
      pin over the resolved DMA list with kind='region'.
    Plain cut_defs pass through untouched (same dict copy).
    """
    cut_def = dict(cut_def or {})
    comp = cut_def.get("compound")
    if isinstance(comp, dict) and comp.get("pins"):
        label = str(comp.get("label") or cut_def.get("label")
                    or "Compound Cut").strip()
        cut_def["label"] = str(cut_def.get("label") or label).strip()
        cut_def.setdefault("name_label", label)
        cut_def["kind"] = "compound"
        pins = _cut_pins(cut_def)
        if not pins:
            raise RuntimeError(
                f"compound cut {label!r} carries no usable pins")
        cut_def["pin_category"] = pins[0][0]
        cut_def["pin_buckets"] = list(pins[0][1])
        return cut_def
    if str(cut_def.get("region_label") or "").strip() \
            or cut_def.get("region_dmas"):
        label = str(cut_def.get("region_label") or cut_def.get("label")
                    or "Region").strip()
        canon_label, dmas = _resolve_region_dmas(
            label, cut_def.get("region_dmas"))
        label = canon_label or label
        cut_def["label"] = str(cut_def.get("label") or label).strip()
        cut_def.setdefault("name_label", label)
        cut_def["kind"] = "region"
        cut_def["pin_category"] = "LOCATION"
        cut_def["pin_buckets"] = dmas
        return cut_def
    return cut_def


# Words that mark a label as genuinely behavioral - a cut like
# "Miami Marathon Runners" must NOT be promoted to the Miami DMA pin.
_BEHAVIORAL_LABEL_WORDS = {
    "buyer", "buyers", "viewer", "viewers", "watcher", "watchers",
    "fan", "fans", "subscriber", "subscribers", "renter", "renters",
    "purchaser", "purchasers", "owner", "owners", "member", "members",
    "listener", "listeners", "player", "players", "gamer", "gamers",
    "switcher", "switchers", "shopper", "shoppers", "voter", "voters",
    "user", "users", "avid", "casual", "enthusiast", "enthusiasts",
    "moviegoer", "moviegoers", "streamer", "streamers", "runner",
    "runners", "attendee", "attendees", "visitor", "visitors",
    "drinker", "drinkers", "driver", "drivers", "traveler",
    "travelers", "seeker", "seekers", "intender", "intenders",
    "customer", "customers", "consumer", "consumers",
}


def _promote_unpinned_pin_cut(df_source, cut_def: dict) -> tuple:
    """Promote an unpinned cut whose label uniquely matches a parent
    demo bucket or LOCATION market into a proper pin cut.

    2026-08-25 (Joe & The Juice - Miami): the partner API minted
    derive_type='other' with the bare label 'Miami' and no
    pin_category, so the engine sized it as a reasoned behavioral
    cohort (0.18) instead of the parent's Miami Ft Lauderdale Fl
    LOCATION share (0.0327), and LOCATION stayed nationally spread on
    a market cut. Per the deterministic-sizing mandate (Jenna
    2026-08-24: the deterministic value always wins when computable),
    an unpinned label that resolves to exactly ONE parent bucket IS
    that pin.

    Matching is conservative:
      * labels carrying behavioral words never promote;
      * demo categories match on _norm_bucket equality only;
      * LOCATION additionally matches on normalized containment
        ('miami' inside 'miamiftlauderdalefl'), label >= 4 chars;
      * anything other than exactly one match across all categories
        leaves the cut behavioral (ambiguity is logged by the caller).

    Returns (cut_def, note) - cut_def is a promoted copy when a unique
    match was found, otherwise the original; note explains the outcome
    ('' when no promotion was attempted or found).
    """
    if not _is_behavioral_cut(cut_def):
        return cut_def, ""
    label = str(cut_def.get("name_label") or cut_def.get("label")
                or "").strip()
    if not label or len(label) > 40:
        return cut_def, ""

    def _flat(s):
        return re.sub(r"[^a-z0-9]", "", str(s).casefold())

    words = {w for w in re.split(r"[^a-z0-9]+", label.casefold()) if w}
    if words & _BEHAVIORAL_LABEL_WORDS:
        return cut_def, ""
    flat_label = _flat(label)
    if len(flat_label) < 3:
        return cut_def, ""

    cat_col, val_col = "Column", "Value"
    present = set(df_source[cat_col].astype(str).str.upper().str.strip())
    demo_cats = [c for c in (
        "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME",
        "OCCUPATION", "PARENTAL STATUS", "PARENTAL_STATUS",
        "RELATIONSHIP", "SEXUAL ORIENTATION", "SEXUAL_ORIENTATION",
    ) if c in present]

    matches = []  # (category, exact row Value)
    cats_upper = df_source[cat_col].astype(str).str.upper().str.strip()
    for cat in demo_cats:
        want = _norm_bucket(cat, label)
        for _, r in df_source[cats_upper == cat].iterrows():
            row_lbl = str(r.get(val_col, "")).strip()
            if _norm_bucket(cat, row_lbl) == want:
                matches.append((cat, row_lbl))
    if "LOCATION" in present and len(flat_label) >= 4:
        for _, r in df_source[cats_upper == "LOCATION"].iterrows():
            row_lbl = str(r.get(val_col, "")).strip()
            fl = _flat(row_lbl)
            if flat_label == fl or flat_label in fl:
                matches.append(("LOCATION", row_lbl))

    uniq = list(dict.fromkeys(matches))
    if len(uniq) != 1:
        if len(uniq) > 1:
            return cut_def, (f"ambiguous label {label!r} matches "
                             f"{uniq[:4]} - left behavioral")
        return cut_def, ""
    cat, row_lbl = uniq[0]
    promoted = dict(cut_def)
    promoted["kind"] = "dma" if cat == "LOCATION" else "demo"
    promoted["pin_category"] = cat
    promoted["pin_buckets"] = [row_lbl]
    return promoted, (f"promoted unpinned label {label!r} -> "
                      f"{cat} pin {row_lbl!r}")


# =============================================================================
# Behavioral cut ceilings (Jenna 2026-09-08)
#
# Verbatim: "and it would need to ensure the sample size never
# outnumbers the number of followers does that make sense" plus "and
# external like you cant say 6m for instagram cut but the actual
# account only has 5m followwers it cant be make sense".
#
# Two invariants that MUST both hold for a behavioral cut sized off a
# specific brand / platform (e.g. "TikTok users", "Instagram viewers",
# "Costco members"):
#
#   1. INTERNAL: the cut's people count can't exceed the parent's own
#      audience for that brand. If the parent reads 48% Instagram,
#      max cohort_fraction is 0.48 (48% of the parent).
#
#   2. EXTERNAL: the cut's US projection can't exceed the brand's
#      real-world US audience. Instagram creator with 5M followers
#      can't have a 6M-projection Instagram cut. Instagram platform
#      with ~130M US MAU can't have a 200M-projection cut of a
#      100M-parent.
#
# Internal is deterministic (read the parent's own BP for the matched
# row). External is a small Claude web_search call, fail-safe: if it
# fails or the brand can't be identified, only the internal ceiling
# applies. The final cohort_fraction is MIN of (Phase 1 reasoned,
# internal ceiling, external ceiling), with a subject-salted downward
# jitter so we sit strictly under the ceiling (no rule-1 pinning).
# =============================================================================
def _flat_norm(s) -> str:
    """Alphanumeric-only casefolded normalizer for brand matching."""
    return re.sub(r"[^a-z0-9]", "", str(s or "").casefold())


def _extract_brand_from_behavioral_label(cut_def: dict) -> str:
    """Return the brand token(s) from a behavioral cut label, or ''.

    Strips leading determiners ('the'), behavioral nouns ('users',
    'viewers', 'fans', ...), and joining prepositions ('who', 'that',
    'of', ...). Leftover content-bearing tokens are joined with a
    space and returned. Empty string when nothing looks like a brand
    (e.g. label was pure behavior words like 'Avid Fans').

    Examples:
        'TikTok users'                 -> 'tiktok'
        'Instagram Viewers'            -> 'instagram'
        'Costco members'               -> 'costco'
        'Netflix Subscribers'          -> 'netflix'
        'People who use TikTok'        -> 'tiktok'
        'Nike shoppers'                -> 'nike'
        'Digital Purchasers'           -> 'digital purchasers'  # no
                                        # brand; internal lookup will
                                        # miss and skip the ceiling
    """
    label = str(cut_def.get("name_label")
                or cut_def.get("label") or "").strip()
    if not label:
        return ""
    _STOP = {
        "the", "a", "an", "of", "for", "on", "in", "at", "to", "by",
        "and", "or", "who", "that", "which", "with", "using",
        "people", "audience", "audiences", "cohort", "cohorts",
        "cut", "cuts", "cutting", "segment", "segments",
        "just", "only",
        # common activity verbs used in "who X <brand>" phrasing.
        # These normalize "People who use TikTok" -> "tiktok".
        "use", "uses", "used",
        "watch", "watches", "watched",
        "stream", "streams", "streamed",
        "shop", "shops", "shopped",
        "buy", "buys", "bought",
        "listen", "listens", "listened",
        "rent", "rents", "rented",
        "read", "reads",
        "drive", "drives", "drove",
        "own", "owns", "owned",
        "subscribe", "subscribes", "subscribed",
        "follow", "follows", "followed",
        "attend", "attends", "attended",
        "visit", "visits", "visited",
    }
    toks = [t for t in re.split(r"[^a-z0-9]+", label.casefold()) if t]
    kept = [t for t in toks
            if t not in _STOP and t not in _BEHAVIORAL_LABEL_WORDS]
    return " ".join(kept).strip()


def _find_parent_brand_ceiling(df_source, brand_query: str,
                               subject: str) -> tuple:
    """(ceiling_cf, canonical_brand, category, matched_bp) from parent.

    Case + punctuation insensitive match of `brand_query` against
    every brand row in df_source (Column != BRAND INPUT / SAMPLE SIZE
    / SUBJECT / BRAND CATEGORY / demo categories). When the brand
    appears in multiple categories (rule 3b mirrors, or SPORTS TEAM +
    league), the highest BP is authoritative - a lower mirror value
    is a data drift, not a real second cap.

    ceiling_cf = matched_bp / 100 (cohort_fraction upper bound).
    Returns (None, '', '', 0.0) when no match.
    """
    if not brand_query:
        return None, "", "", 0.0
    q_flat = _flat_norm(brand_query)
    if len(q_flat) < 3:
        return None, "", "", 0.0

    cat_col, val_col = "Column", "Value"
    try:
        bp_col, _, _, _ = _detect_cols(df_source)
    except Exception:
        return None, "", "", 0.0

    _SKIP_CATS = {
        "", "BRAND INPUT", "SAMPLE SIZE", "SUBJECT", "BRAND CATEGORY",
        "INPUT_METADATA", "GENDER", "AGE", "ETHNICITY", "EDUCATION",
        "INCOME", "OCCUPATION", "PARENTAL STATUS", "PARENTAL_STATUS",
        "RELATIONSHIP", "SEXUAL ORIENTATION", "SEXUAL_ORIENTATION",
        "LOCATION", "AVID FAN",
    }
    subj_flat = _flat_norm(subject)

    best = (0.0, "", "", "")  # (bp, canonical_value, category, flat)
    cats_upper = df_source[cat_col].astype(str).str.upper().str.strip()
    for idx, r in df_source.iterrows():
        cat = str(cats_upper.iat[df_source.index.get_loc(idx)]).strip()
        if cat in _SKIP_CATS:
            continue
        val = str(r.get(val_col, "")).strip()
        if not val:
            continue
        val_flat = _flat_norm(val)
        if not val_flat:
            continue
        # Don't let the subject's own row (100% self-pin) satisfy the
        # ceiling - that's not a brand overlap, that's the parent
        # itself, and a 1.00 cf gives no ceiling protection.
        if val_flat == subj_flat:
            continue
        if val_flat == q_flat or q_flat in val_flat or val_flat in q_flat:
            v = _fbp(r.get(bp_col, 0))
            if v is None or v <= 0:
                continue
            # Prefer strict exact match; substring hits require the
            # query to be at least 4 chars to avoid short-token noise
            # (e.g. 'nba' matching 'nbacareers').
            exact = (val_flat == q_flat)
            if not exact and len(q_flat) < 4:
                continue
            score = float(v)
            if score > best[0]:
                best = (score, val, cat, val_flat)

    if best[0] <= 0:
        return None, "", "", 0.0
    bp, val, cat, _ = best
    # cohort_fraction ceiling from parent's BP (clamped to a floor of
    # 0.005 so a laughably low-BP brand doesn't zero the cut - the
    # phase 1 fraction still applies as an upper bound anyway).
    cf = max(0.005, min(1.0, bp / 100.0))
    return cf, val, cat, bp


# Cache researched US audience counts across a single process so a
# batch of cuts against the same brand doesn't refire the web_search
# call. Keyed by lowercased brand.
_EXTERNAL_BRAND_CACHE: dict = {}


def _research_brand_us_audience(brand: str, subject: str,
                                cut_label: str) -> tuple:
    """(us_audience_count, source_note) from one Claude web_search
    call, or (None, '') on any failure.

    Asks for the plausible US audience count for the brand in the
    trailing 12 months (the standing date window). For platforms this
    reads as US MAU / reach; for creator accounts / brand accounts on
    a platform, this reads as the account's US follower count.
    Approved sources match the vetting framework
    (crosswalk-audience-vetting-framework.mdc): SEC filings, earnings
    reports, Pew, Nielsen, Statista, eMarketer, YouGov, Sensor Tower /
    data.ai. Never-use: annual store visits, cable/satellite reach,
    total-brand-reach figures that mix offline.

    FAIL-SAFE: any exception (missing claude_client, missing tool,
    empty response, parse error, non-numeric value) returns (None, '')
    and the caller applies only the internal ceiling. NEVER raises.
    """
    if not brand:
        return None, ""
    key = brand.strip().lower()
    if key in _EXTERNAL_BRAND_CACHE:
        return _EXTERNAL_BRAND_CACHE[key]

    # Opt-out env for hermetic tests / offline dev laptops. Mirrors
    # the pattern in hostmap_gap_mapping and genpop_research_*.
    if os.environ.get("ADDON_CUT_EXTERNAL_CEILING", "").strip() == "0":
        _EXTERNAL_BRAND_CACHE[key] = (None, "")
        return None, ""

    try:
        from claude_client import claude_messages  # type: ignore
    except Exception:
        try:
            from migration.claude_client import claude_messages  # type: ignore # noqa
        except Exception:
            _EXTERNAL_BRAND_CACHE[key] = (None, "")
            return None, ""

    WEB_SEARCH_TOOL = {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 8,
    }
    WEB_SEARCH_TOOL_LEGACY = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 8,
    }

    system = (
        "You are a market-research specialist calibrating a US "
        "audience ceiling. For the given BRAND, return the plausible "
        "US audience count in the trailing 12 months. For platforms "
        "return US MAU or US penetration count. For a specific "
        "creator / talent / brand account, return the account's US "
        "follower count. For a retail brand return active US "
        "shoppers. Approved sources ONLY: SEC filings and earnings, "
        "Pew, Nielsen, Statista, eMarketer, YouGov, Sensor Tower / "
        "data.ai. Never-use: annual store visits, cable/satellite "
        "reach, total-brand-reach that mixes offline. If the brand "
        "is ambiguous or the value cannot be researched, return "
        "null. STRICT JSON only, no prose."
    )
    user = (
        f"BRAND: {brand}\n"
        f"PARENT SUBJECT: {subject}\n"
        f"CUT LABEL: {cut_label}\n\n"
        "Return the US audience count as an integer count of PEOPLE "
        "(not accounts, not households).\n\n"
        "Return STRICT JSON only:\n"
        "{\n"
        '  "brand": "...",\n'
        '  "us_audience_count": 129000000,\n'
        '  "metric_class": "us_users_mau" | "us_penetration_reach" | '
        '"us_subscribers" | "us_followers" | "us_active_shoppers" | '
        '"proxy",\n'
        '  "source_note": "one short sentence naming the source"\n'
        "}\n"
        "Return us_audience_count: null when the brand cannot be "
        "grounded in an approved source."
    )
    obj = None
    for attempt, tool in enumerate(
            (WEB_SEARCH_TOOL, WEB_SEARCH_TOOL_LEGACY)):
        try:
            resp = claude_messages(
                system=system, user=user, max_tokens=1500,
                temperature=0.2, tools=[tool],
            )
            if not resp:
                continue
            obj = _extract_json_block(resp)
            if isinstance(obj, dict):
                break
        except Exception:
            continue
    if not isinstance(obj, dict):
        _EXTERNAL_BRAND_CACHE[key] = (None, "")
        return None, ""
    val = obj.get("us_audience_count")
    try:
        n = int(float(val))
    except (TypeError, ValueError):
        _EXTERNAL_BRAND_CACHE[key] = (None, "")
        return None, ""
    if n <= 0 or n > 340_000_000:
        _EXTERNAL_BRAND_CACHE[key] = (None, "")
        return None, ""
    src = str(obj.get("source_note") or "").strip()
    _EXTERNAL_BRAND_CACHE[key] = (n, src)
    return n, src


def _apply_behavioral_ceilings(cf: float, df_source, cut_def: dict,
                               subject: str, old_sample: float,
                               old_uspop: float) -> tuple:
    """Apply the (INTERNAL, EXTERNAL) ceilings to a phase-1 cohort
    fraction. Returns (adjusted_cf, notes_dict). Never raises. Both
    ceilings are additive: whichever binds harder wins; when neither
    can be computed the phase-1 value passes through unchanged.
    """
    notes = {"phase1_cf": cf, "internal_ceiling": None,
             "external_ceiling": None, "final_cf": cf,
             "brand": "", "brand_source": "", "note": ""}
    brand_query = _extract_brand_from_behavioral_label(cut_def)
    if not brand_query:
        notes["note"] = "no brand token in label; ceilings skipped"
        return cf, notes

    # --- Internal ceiling (deterministic) ---
    try:
        int_cf, canonical, category, matched_bp = \
            _find_parent_brand_ceiling(df_source, brand_query, subject)
    except Exception as e:
        int_cf, canonical, category, matched_bp = None, "", "", 0.0
        notes["note"] = f"internal ceiling failed: {e}"
    notes["brand"] = canonical or brand_query
    if int_cf is not None:
        notes["internal_ceiling"] = int_cf
        notes["brand_source"] = f"{category} @ {matched_bp:.4f}%"

    # --- External ceiling (research, fail-safe) ---
    ext_cf = None
    ext_note = ""
    if old_uspop and old_uspop > 0:
        try:
            us_n, src = _research_brand_us_audience(
                canonical or brand_query, subject,
                str(cut_def.get("label") or ""))
        except Exception as e:
            us_n, src = None, f"failed: {e}"
        if us_n and us_n > 0:
            # cohort_fraction ceiling from external: the cut's US
            # projection can't exceed us_n. new_uspop = cf * old_uspop
            # (approx, since new_uspop scales linearly with new_sample
            # and new_sample = cf * old_sample). So cf <= us_n /
            # old_uspop.
            ext_cf = max(0.005, min(1.0, us_n / float(old_uspop)))
            ext_note = src
    notes["external_ceiling"] = ext_cf
    notes["external_source"] = ext_note

    # Apply the tighter of the two ceilings.
    candidates = [cf]
    if int_cf is not None:
        candidates.append(int_cf)
    if ext_cf is not None:
        candidates.append(ext_cf)
    tight = min(candidates)

    # Sit strictly under the ceiling with a subject-salted downward
    # jitter (no rule-1 pinning; if two cuts on different subjects
    # happen to hit the same ceiling brand they don't 4dp-collide).
    if tight < cf:
        salt = f"{subject}|{cut_def.get('label', '')}|behavioral_ceiling"
        # jitter in [0.90, 0.99] of the ceiling
        u = _seed_jitter(salt, 0.09)  # in [-0.045, +0.045]
        final = tight * (0.945 + u)
        final = max(0.005, min(tight, final))
    else:
        final = cf

    notes["final_cf"] = final
    return final, notes


def _cohort_desc(cut_def: dict) -> str:
    return str(cut_def.get("cohort_description")
               or cut_def.get("label") or "behavioral cohort").strip()


def reason_addon_audience(snap: dict, cut_def: dict,
                          source_label: str) -> dict:
    """One Claude call: how do the OTHER demo categories shift for this
    cohort, and what's the cut persona? For pin cuts cohort_fraction is
    overridden deterministically by the caller, so Claude's estimate is
    advisory. For BEHAVIORAL cuts there is no deterministic source -
    Claude's fraction (grounded in real adoption/attendance data) IS
    the sizing, so the prompt leans on it harder.
    """
    subject = snap.get("subject") or "Unknown"
    behavioral = _is_behavioral_cut(cut_def)
    pin_cats = (set() if behavioral
                else {c for c, _ in _cut_pins(cut_def)})
    pin_desc = _pin_desc(cut_def)
    fallback = {
        "cohort_fraction": 0.25,
        "us_pop_fraction": 0.05,
        "reasoning": f"fallback: {cut_def['label']} cut of {subject}",
        "audience_demo_targets": {},
        "claude_used": False,
    }
    try:
        from claude_client import claude_messages
    except Exception:
        return fallback

    demo_lines = []
    for cat, buckets in (snap.get("demo_snapshot") or {}).items():
        if str(cat).upper() in pin_cats:
            continue
        bs = ", ".join(f"{k}={v}" for k, v in list(buckets.items())[:8])
        demo_lines.append(f"  {cat}: {bs}")
    if behavioral:
        fraction_ask = (
            "1. cohort_fraction: what SHARE of the parent audience "
            "belongs to this behavioral cohort? Ground the estimate in "
            "real-world data you know - box-office attendance rates, "
            "digital purchase/rental adoption, subscription uptake, "
            "category incidence - applied to THIS parent's demo mix. "
            "This number sizes the deliverable, so reason carefully "
            "and avoid round guesses.\n"
            "   HARD INVARIANTS this fraction must respect:\n"
            "   (a) INTERNAL CEILING: if the cut is defined by a "
            "specific brand or platform (e.g. 'TikTok users', "
            "'Instagram viewers', 'Costco members'), cohort_fraction "
            "cannot exceed the parent's own Brand Penetration for "
            "that brand. If the parent reads 48% Instagram, the "
            "'Instagram users' cut cannot exceed 0.48.\n"
            "   (b) EXTERNAL CEILING: cohort_fraction times the "
            "parent's US Gen Pop Projection cannot exceed the "
            "brand's real-world US audience. A creator with 5M US "
            "followers cannot have a 6M-projection 'Instagram cut'. "
            "A platform with 130M US MAU cannot produce a "
            "200M-projection cut of a 100M-parent.\n"
            "   Take the MIN of your grounded estimate and both "
            "ceilings.\n"
            "2. How each demographic category realistically shifts "
            "inside this behavioral sub-cohort vs the parent.\n"
            "3. A 2-4 sentence persona: who these people are, how "
            "they shop, what media they live on.\n\n"
        )
        pin_exclusion = ""
    else:
        fraction_ask = (
            "1. How each OTHER demographic category realistically "
            "shifts inside this sub-cohort (e.g. an 18-24 cut skews "
            "less married, lower income, more urban; a Los Angeles "
            "cut skews more Hispanic and higher-income vs the "
            "national parent).\n"
            "2. A 2-4 sentence persona: who these people are, how "
            "they shop, what media they live on.\n\n"
        )
        pin_exclusion = (
            f"Do NOT include {', '.join(sorted(pin_cats))} in "
            "audience_demo_targets - "
            + ("they are" if len(pin_cats) > 1 else "it is")
            + " hard-pinned by the transform."
        )
    user = (
        f"SUBJECT: {subject}\n"
        f"SOURCE FILE: {source_label}\n"
        f"CUT: {cut_def['label']} ({pin_desc})\n\n"
        "SOURCE DEMOS (the parent audience today):\n"
        + "\n".join(demo_lines) +
        "\n\nYou are deriving the sub-cohort of this audience defined "
        f"by the {'behavioral definition' if behavioral else 'pin'} "
        "above. Reason about:\n"
        + fraction_ask +
        "Return STRICT JSON only:\n"
        "{\n"
        '  "cohort_fraction": 0.22,\n'
        '  "us_pop_fraction": 0.04,\n'
        '  "reasoning": "...",\n'
        '  "audience_demo_targets": {\n'
        '    "GENDER": {"FEMALE": 54.2, "MALE": 45.8},\n'
        '    "INCOME": {...}, "ETHNICITY": {...}\n'
        "  }\n"
        "}\n"
        + pin_exclusion
    )
    try:
        resp = claude_messages(
            system=("You derive audience sub-cohort demographics for a "
                    "US consumer panel. Ground every shift in real "
                    "demographic knowledge. STRICT JSON only."),
            user=user, max_tokens=3000, temperature=0.3,
        )
    except Exception as e:
        print(f"[addon-cut] phase1 claude failed: {e}")
        return fallback
    obj = _extract_json_block(resp) if resp else None
    if not isinstance(obj, dict):
        return fallback
    out = dict(fallback)
    out["claude_used"] = True
    try:
        cf = float(obj.get("cohort_fraction") or 0)
        if 0 < cf <= 1:
            out["cohort_fraction"] = cf
    except Exception:
        pass
    try:
        uf = float(obj.get("us_pop_fraction") or 0)
        if 0 < uf <= 1:
            out["us_pop_fraction"] = uf
    except Exception:
        pass
    if isinstance(obj.get("reasoning"), str):
        out["reasoning"] = obj["reasoning"]
    tgt = obj.get("audience_demo_targets")
    if isinstance(tgt, dict):
        clean = {}
        for cat, buckets in tgt.items():
            cu = str(cat).strip().upper()
            if cu in pin_cats:
                continue  # pins are ours, not Claude's
            if not isinstance(buckets, dict):
                continue
            cb = {}
            for k, v in buckets.items():
                try:
                    cb[str(k).strip().upper()] = float(v)
                except Exception:
                    continue
            if cb:
                clean[cu] = cb
        out["audience_demo_targets"] = clean
    return out


# =============================================================================
# Phase 2 - row-by-row reasoning (all rows, all non-demo categories)
# =============================================================================
def _audience_summary_generic(audience: dict, cut_def: dict) -> str:
    if _is_behavioral_cut(cut_def):
        head = (f"behavioral cohort: {cut_def['label']} "
                f"({_cohort_desc(cut_def)})")
    else:
        head = f"cohort pin: {cut_def['label']} ({_pin_desc(cut_def)})"
    L = [head,
         f"cohort_fraction (of source): "
         f"{audience.get('cohort_fraction', 0):.4f}",
         f"reasoning: {audience.get('reasoning', '')}",
         "",
         "Demo targets (this cut):"]
    for cat, buckets in (audience.get("audience_demo_targets") or {}).items():
        bs = ", ".join(f"{lbl}={v:.1f}" for lbl, v in
                       sorted(buckets.items(), key=lambda kv: -kv[1])[:5])
        L.append(f"  {cat}: {bs}")
    return "\n".join(L)


def _format_category_user_generic(subject: str, audience_summary: str,
                                  category: str, rows: list,
                                  cut_def: dict,
                                  persona_brief: str = "") -> str:
    if _is_behavioral_cut(cut_def):
        cohort_line = (f"BEHAVIORAL COHORT: {cut_def['label']} "
                       f"({_cohort_desc(cut_def)})")
    else:
        cohort_line = (f"COHORT PIN: {cut_def['label']} "
                       f"({_pin_desc(cut_def)})")
    L = [f"SUBJECT: {subject}",
         cohort_line]
    if _is_region_cut(cut_def):
        L.append(
            "REGION CUT: this cohort spans EVERY market listed in the "
            "LOCATION pin above as one region. Reason about the "
            "region's shared culture - regional chains, teams, "
            "climate-driven categories, retail footprints - not any "
            "single metro.")
    L.extend(["AUDIENCE PROFILE:",
              audience_summary,
              ""])
    if persona_brief:
        L.append(persona_brief)
        L.append("")
    L.extend([
        f"CATEGORY: {category}",
        f"ITEMS ({len(rows)} rows, source_bp = current value in "
        f"source profile):"])
    for label, bp in rows:
        L.append(f"  - {label} :: source_bp={bp:.4f}")
    L.append("")
    L.append('Return JSON: {"items":[{"label":"...","new_bp":<float>}, ...]}')
    L.append("Every item MUST appear in the response. Reason ROW BY "
             "ROW against THIS cohort (the cohort definition above "
             "changes who these people are - age changes platforms, "
             "spend, and culture; a market pin changes regional "
             "chains, teams, and climate-driven categories; a "
             "behavioral cohort changes purchase intent, fandom, and "
             "platform mix). Do not apply a uniform multiplier. Brands "
             "can go up, down, stay flat, OR drop to near-zero if this "
             "cohort genuinely wouldn't engage.")
    return "\n".join(L)


def reason_category_rows_addon(subject: str, audience: dict,
                               category: str, rows: list,
                               cut_def: dict, *,
                               chunk_size: int = 200,
                               df_source=None,
                               api_key: str = None) -> dict:
    """Chunked full-category row reasoning - mirror of
    audience_cut_synthesis.reason_category_rows_cut with a generic
    cohort header instead of the gender pin header."""
    if not rows:
        return {}
    audience_summary = _audience_summary_generic(audience, cut_def)

    persona_brief = ""
    if df_source is not None:
        try:
            from persona_briefs import build_category_persona_brief
            persona_brief = build_category_persona_brief(
                subject, category, df_source,
            )
        except Exception:
            persona_brief = ""

    try:
        from cut_parallel import cut_claude_call
    except Exception:
        try:
            from migration.cut_parallel import cut_claude_call
        except Exception:
            return {}

    rows_sorted = sorted(rows, key=lambda kv: -kv[1])
    decisions: dict = {}
    n_chunks = (len(rows_sorted) + chunk_size - 1) // chunk_size
    for i in range(n_chunks):
        chunk = rows_sorted[i * chunk_size:(i + 1) * chunk_size]
        chunk_label = (f"{category} (chunk {i + 1}/{n_chunks})"
                       if n_chunks > 1 else category)
        user = _format_category_user_generic(
            subject, audience_summary, chunk_label, chunk, cut_def,
            persona_brief=persona_brief,
        )
        try:
            resp = cut_claude_call(
                system=_CAT_ROW_SYSTEM, user=user, api_key=api_key,
                max_tokens=24000, temperature=0.3,
            )
        except Exception as e:
            print(f"[addon-cut] cat={category} chunk {i+1}/{n_chunks} "
                  f"claude failed: {e}")
            continue
        obj = _extract_json_block(resp) if resp else None
        if not isinstance(obj, dict):
            continue
        for it in (obj.get("items") or []):
            if not isinstance(it, dict):
                continue
            lbl = str(it.get("label", "")).strip().upper()
            nv = it.get("new_bp")
            if not lbl or not isinstance(nv, (int, float)):
                continue
            decisions[lbl] = max(0.0001, min(99.49, round(float(nv), 4)))
    return decisions


# =============================================================================
# Defining-brand pin for behavioral cuts (Jenna 2026-09-08)
# =============================================================================
def _find_defining_brand_rows(df, brand_query: str, subject: str):
    """Return every (idx, category, value) triple in df matching
    brand_query, case + punctuation insensitive.

    Mirrors the matching logic in _find_parent_brand_ceiling but
    returns EVERY match rather than just the highest-BP one, so a
    brand that lives across rule-3b mirrors (MOST PURCHASED BRANDS +
    CPG, APP/PLATFORM + SOCIAL MEDIA companions where the hostmap
    seats a brand in both, etc.) all pin together at 100.

    Excludes demo, subject, metadata, and AVID FAN rows.  Returns []
    when brand_query is empty, too short, or nothing matches.
    """
    if not brand_query:
        return []
    q_flat = _flat_norm(brand_query)
    if len(q_flat) < 3:
        return []
    _SKIP_CATS = {
        "", "BRAND INPUT", "SAMPLE SIZE", "SUBJECT", "BRAND CATEGORY",
        "INPUT_METADATA", "GENDER", "AGE", "ETHNICITY", "EDUCATION",
        "INCOME", "OCCUPATION", "PARENTAL STATUS", "PARENTAL_STATUS",
        "RELATIONSHIP", "SEXUAL ORIENTATION", "SEXUAL_ORIENTATION",
        "LOCATION", "AVID FAN",
    }
    subj_flat = _flat_norm(subject)
    matches = []
    cats_upper = df["Column"].astype(str).str.upper().str.strip()
    for idx in df.index:
        cat = cats_upper.at[idx]
        if cat in _SKIP_CATS:
            continue
        val = str(df.at[idx, "Value"]).strip()
        if not val:
            continue
        val_flat = _flat_norm(val)
        if not val_flat or val_flat == subj_flat:
            continue
        exact = (val_flat == q_flat)
        if not exact:
            # Same substring rule as _find_parent_brand_ceiling: >= 4
            # chars for substring matches to avoid short-token noise.
            if len(q_flat) < 4:
                continue
            if q_flat not in val_flat and val_flat not in q_flat:
                continue
        matches.append((idx, cat, val))
    return matches


def _pin_defining_brand_to_100(df, cut_def, subject, new_sample,
                                new_uspop, bp_col, cs_col, raw_col,
                                proj_col):
    """Pin the defining brand of a behavioral cut to exactly 100 in
    every category it appears.

    Jenna 2026-09-08 (verbatim): "this isn't a win. it needs to be
    100% not 99.anything Heavy Social Users Female-Skewed -
    Instagram Users.csv - Instagram BP 99.4938%, YouTube 96.16%,
    TikTok 68.42%". A cohort defined by "X users" IS 100% X by
    construction. Reasoned 99.something leaks the fact that the
    engine got there by row-by-row lift rather than by identity.

    Fail-safe skip conditions:
      - No brand token extractable from the cut label (generic
        behavioral like "Digital Purchasers", "Gift Givers").
      - No matching row exists in df (brand not in parent's grid).

    On a match, sets BP=100.0000 (%-suffixed string, matching the
    transform's own format) and Category Share=100.0000. Raw and
    Proj are pinned to the cohort's sample_size / us_pop so the
    row reads as the entire audience. Gen Pop Penetration and
    Index vs Gen Pop are blanked (NaN -> empty cell) since a
    self-pin row has no meaningful gen-pop comparison.

    Returns (n_pinned_rows, brand_canonical).
    """
    brand = _extract_brand_from_behavioral_label(cut_def)
    if not brand:
        return 0, ""
    rows = _find_defining_brand_rows(df, brand, subject)
    if not rows:
        return 0, brand
    for idx, _cat, _val in rows:
        # Format-match the transform: BP is written as "100.0000%"
        # (percent-suffixed string, same as line ~1466 in the row
        # transform), CS is written as a plain float (same as the
        # CS recompute at line ~1562).
        df.at[idx, bp_col] = "100.0000%"
        df.at[idx, cs_col] = 100.0000
        df.at[idx, raw_col] = float(new_sample)
        df.at[idx, proj_col] = float(new_uspop)
        # A self-pin row has no gen-pop-index meaning (the cohort IS
        # the audience). Blank both columns when they exist.
        for _blank_col in ("Gen Pop Penetration", "Index vs Gen Pop"):
            if _blank_col in df.columns:
                df.at[idx, _blank_col] = float("nan")
    return len(rows), brand


# =============================================================================
# Phase 3 - generalized pin transform
# =============================================================================
def apply_addon_cut_transform(df, cut_def: dict, audience: dict,
                              category_decisions: dict, subject: str,
                              pin_source_shares: dict):
    """Generalized apply_audience_cut_transform: pins every pinned
    dimension of the cut (one for classic cuts, several for compound
    multi-pin cuts) to its buckets (~99.99 split proportional to the
    buckets' SOURCE ratios), everything else identical to the gender
    machine. Single-DMA cuts DROP the non-pinned LOCATION rows (a Los
    Angeles-only profile has no meaningful mix across 209 other
    markets); region cuts and compound LOCATION pins KEEP them at
    hairline near-zero values so LOCATION still sums to ~100.

    pin_source_shares: {bucket: parent_bp} for single-pin cuts,
    {CATEGORY: {bucket: parent_bp}} (nested) for compound cuts."""
    df = df.copy()
    cat_col, val_col = "Column", "Value"
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)

    # At-birth ladder guard (2026-08-26 Liz QA, Bethenny avid): re-salt
    # model decision batches that reuse one fractional part across many
    # rows before they land in the frame. Integer parts preserved.
    try:
        try:
            from migration.fractional_ladders import deladder_decision_map
        except ImportError:
            from fractional_ladders import deladder_decision_map  # type: ignore
        category_decisions, _n_deladder = deladder_decision_map(
            category_decisions, subject)
    except Exception as _dl_err:
        print(f"    [deladder] guard skipped ({_dl_err})")

    # Behavioral cuts have no pins: an empty pins list disables the
    # pin transform below, and demo shifts come purely from
    # audience_demo_targets. Compound cuts (2026-08-25) carry SEVERAL
    # pins; each pinned category renders exactly like a single-pin
    # cut of that dimension.
    pins = _cut_pins(cut_def)
    is_compound = _is_compound_cut(cut_def) or len(pins) > 1
    is_region = _is_region_cut(cut_def)
    cut_salt = cut_def.get("cut_id") or cut_def.get("label") or "cut"

    # pin_source_shares arrives FLAT ({bucket: bp}) for single-pin
    # cuts, NESTED ({CAT: {bucket: bp}}) for compound cuts.
    shares_by_cat = {}
    if pins:
        if is_compound:
            for k, v in (pin_source_shares or {}).items():
                if isinstance(v, dict):
                    shares_by_cat[str(k).upper().strip()] = dict(v)
        else:
            shares_by_cat[pins[0][0]] = dict(pin_source_shares or {})

    demo_targets = audience.get("audience_demo_targets", {}) or {}
    cohort_fraction = float(audience.get("cohort_fraction", 0.20) or 0.20)

    cats_upper = df[cat_col].astype(str).str.upper().str.strip()
    ss_mask = cats_upper == "SAMPLE SIZE"
    old_sample, old_uspop = 0, 0
    if ss_mask.any():
        ss_row = df[ss_mask].iloc[0]
        try:
            old_sample = float(str(ss_row[raw_col]).replace(",", ""))
        except Exception:
            old_sample = 0
        try:
            old_uspop = float(str(ss_row[proj_col]).replace(",", ""))
        except Exception:
            old_uspop = 0
    new_sample = max(500, round(old_sample * cohort_fraction))
    # Workspace rule: no round sample sizes, last digit never zero.
    new_sample = ensure_messy_sample_size(
        f"{subject}|{cut_salt}", new_sample)
    if old_uspop > 0 and old_sample > 0:
        new_uspop = max(5000, round(new_sample * old_uspop / old_sample))
    else:
        new_uspop = max(5000, round(330_000_000 * float(
            audience.get("us_pop_fraction", 0.05) or 0.05)))

    for c in (bp_col, cs_col, raw_col, proj_col):
        if c in df.columns and df[c].dtype.name not in ("object", "O"):
            df[c] = df[c].astype(object)

    # Per-pin alloc: pinned-bucket share of ~99.99 in each pinned
    # category, split proportional to the SOURCE ratios of those
    # buckets (jittered so no exact-boundary values).
    #
    # EXCEPTION - AGE pins (Jenna 2026-08-20, Protein Enthusiasts):
    # the panel's age breaks are an exact partition, so an age pin is
    # physically 100% inside its bucket(s). "everything not 17 and
    # under should be 0% in that file." Target buckets carry exactly
    # 100.0000 total; every other AGE bucket is exactly 0.0000.
    #
    # LOCATION pins: single-DMA cuts DROP the other markets (an LA-
    # only profile has no meaningful mix across 209 other markets).
    # Region cuts and LOCATION pins inside compounds KEEP the other
    # markets at hairline values (0.0001-0.0009) so LOCATION still
    # renders as a full grid that sums to ~100.
    pin_info = {}
    for cat, buckets in pins:
        bucket_set = {str(b).strip().upper() for b in buckets}
        if not bucket_set:
            continue
        age_exact_c = cat == "AGE"
        loc_pin = cat == "LOCATION"
        drop_others = loc_pin and not (is_region or is_compound)
        cat_shares = shares_by_cat.get(cat, {})
        if age_exact_c:
            pin_total = 100.0
        else:
            pin_total = 99.99 + _seed_jitter(
                f"{subject}|{cat}|pin-total|{cut_salt}", span=0.012)
            pin_total = max(99.50, min(99.997, pin_total))
        src_sum = sum(cat_shares.get(b, 0.0) for b in cat_shares) or 1.0
        pin_alloc = {}
        if len(bucket_set) == 1:
            only = next(iter(bucket_set))
            pin_alloc[only] = pin_total
        else:
            remaining = pin_total
            labels = sorted(bucket_set)
            for j, lbl in enumerate(labels):
                src_bp = 0.0
                for k, v in cat_shares.items():
                    if str(k).strip().upper() == lbl:
                        src_bp = v
                        break
                if j == len(labels) - 1:
                    share = remaining
                else:
                    share = pin_total * (src_bp / src_sum if src_sum
                                         else 1.0 / len(labels))
                    share += _seed_jitter(
                        f"{subject}|{cat}|{lbl}|pin-split|{cut_salt}",
                        span=0.05)
                    share = max(0.05, min(remaining - 0.05 * (
                        len(labels) - j - 1), share))
                    remaining -= share
                pin_alloc[lbl] = round(share, 4)
        pin_info[cat] = {
            "buckets": bucket_set, "alloc": pin_alloc,
            "total": pin_total, "age_exact": age_exact_c,
            "loc": loc_pin, "drop_others": drop_others,
        }

    drop_idx = []
    n_pin_rows = n_demo = n_brand = n_unchanged = n_subject_pin = 0

    for idx in df.index:
        cat = str(df.at[idx, cat_col]).strip().upper()
        val = str(df.at[idx, val_col]).strip()
        val_u = val.upper()

        if cat == "SAMPLE SIZE":
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            df.at[idx, cs_col] = float(new_sample)
            continue
        if cat == "BRAND INPUT":
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            continue
        if cat in {"BRAND CATEGORY", "INPUT_METADATA",
                   "BRAND ID", "REPORT INPUT"}:
            continue

        old_bp = _fbp(df.at[idx, bp_col])
        if old_bp is None:
            continue

        # Subject self-pin stays 100 (fandom level differs, identity
        # doesn't).
        if cat in SUBJECT_PIN_CATS_TF or cat == "SUBJECT":
            if abs(old_bp - 100.0) < 0.01 and val_u == subject.upper():
                df.at[idx, raw_col] = float(new_sample)
                df.at[idx, proj_col] = float(new_uspop)
                n_subject_pin += 1
                continue

        # Universal 100-pin keep (2026-08-24 Furious audit D6a): ANY
        # non-demo, non-pinned-dimension row the parent holds at
        # exactly 100 is a pin by construction - the subject self-pin
        # in a native grid outside SUBJECT_PIN_CATS_TF (SERIES
        # 'Furious', which the exact-name check above also misses when
        # the caller passes a deliverable label), viewers-scope
        # platform pins (STREAMING/PLATFORM 'Disney+/Hulu'), companion
        # pins. BP stays untouched; only Raw/Proj rescale. Without this
        # the row fell to the brand path and min(99.49, ...) eroded it.
        if (cat not in DEMO_CATS_TF and cat != "LOCATION"
                and cat not in pin_info
                and old_bp >= 99.995):
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            n_subject_pin += 1
            continue

        # A pinned category (behavioral cuts have no pins, so this
        # never matches for them). Compound cuts hit this branch once
        # per pinned dimension.
        if cat in pin_info:
            info = pin_info[cat]
            if val_u in info["buckets"]:
                pv = info["alloc"].get(
                    val_u, info["total"] / max(1, len(info["buckets"])))
                df.at[idx, bp_col] = f"{pv:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * pv / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * pv / 100.0))
            elif info["drop_others"]:
                drop_idx.append(idx)
            elif info["age_exact"]:
                # Age breaks partition the panel: outside the pinned
                # bucket(s) the cohort is exactly 0.
                df.at[idx, bp_col] = "0.0000%"
                df.at[idx, raw_col] = 0.0
                df.at[idx, proj_col] = 0.0
            elif info["loc"]:
                # Kept non-target market (region / compound LOCATION
                # pin): hairline value so ~200 markets together stay
                # well under 0.2pp and the pin renorm barely moves.
                tiny = 0.0005 + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|pin-other|{cut_salt}",
                    span=0.0004)
                tiny = max(0.0001, min(0.0009, round(tiny, 4)))
                df.at[idx, bp_col] = f"{tiny:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * tiny / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * tiny / 100.0))
            else:
                tiny = 0.005 + abs(_seed_jitter(
                    f"{subject}|{cat}|{val_u}|pin-other|{cut_salt}",
                    span=0.008))
                tiny = max(0.0010, min(0.0490, round(tiny, 4)))
                df.at[idx, bp_col] = f"{tiny:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * tiny / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * tiny / 100.0))
            n_pin_rows += 1
            continue

        # Other demos: Claude targets else source + jitter.
        if cat in DEMO_CATS_TF or cat == "LOCATION":
            buckets = demo_targets.get(cat, {})
            new_bp = None
            for k, v in buckets.items():
                if str(k).strip().upper() == val_u:
                    new_bp = float(v)
                    break
            if new_bp is None:
                new_bp = old_bp + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|demo-fb|{cut_salt}",
                    span=2.0)
                new_bp = max(0.05, min(99.0, round(new_bp, 4)))
            new_bp = round(new_bp, 4)
            if abs(new_bp - old_bp) < 0.01:
                new_bp = round(new_bp + 0.01 + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|demo-coll|{cut_salt}",
                    span=0.05), 4)
            df.at[idx, bp_col] = f"{new_bp:.4f}%"
            df.at[idx, raw_col] = float(round(new_sample * new_bp / 100.0))
            df.at[idx, proj_col] = float(round(new_uspop * new_bp / 100.0))
            n_demo += 1
            continue

        # Brand rows: Claude decision or ±0.10pp no-collision jitter.
        cat_dec = category_decisions.get(cat, {})
        claude_val = cat_dec.get(val_u)
        is_placeholder = claude_val is not None and any(
            abs(float(claude_val) - p) < 0.0005
            for p in (12.3456, 0.4271, 47.8312))
        if val_u in cat_dec and not is_placeholder:
            new_bp = max(0.0001, min(99.49, round(float(cat_dec[val_u]), 4)))
            n_brand += 1
        else:
            new_bp = round(old_bp + _seed_jitter(
                f"{subject}|{cat}|{val_u}|no-claude|{cut_salt}",
                span=0.10), 4)
            new_bp = max(0.0001, min(99.49, new_bp))
            n_unchanged += 1
        df.at[idx, bp_col] = f"{new_bp:.4f}%"
        df.at[idx, raw_col] = float(round(new_sample * new_bp / 100.0))
        df.at[idx, proj_col] = float(round(new_uspop * new_bp / 100.0))

    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)

    # Renormalize non-pinned demo categories to 100 (pins stay put).
    cats_upper = df[cat_col].astype(str).str.upper().str.strip()
    renorm_cats = set(DEMO_CATS_TF)
    if "LOCATION" not in pin_info:
        renorm_cats.add("LOCATION")
    for cat in renorm_cats:
        if cat in pin_info:
            continue
        mask = cats_upper == cat
        if not mask.any():
            continue
        rows = []
        for idx in df.index[mask]:
            v = _fbp(df.at[idx, bp_col])
            if v is None:
                continue
            label = str(df.at[idx, val_col]).strip()
            rows.append((idx, max(0.01, v + _seed_jitter(
                f"{subject}|{cat}|{label}|renorm|{cut_salt}",
                span=0.10))))
        total = sum(v for _, v in rows)
        if total <= 0:
            continue
        for idx, v in rows:
            normed = round(v * 100.0 / total, 4)
            df.at[idx, bp_col] = f"{normed:.4f}%"
            df.at[idx, raw_col] = float(round(new_sample * normed / 100.0))
            df.at[idx, proj_col] = float(round(new_uspop * normed / 100.0))

    # Pinned categories: force exact-100 sum (buckets keep their
    # ratio). Skipped for drop-mode DMA cuts, whose single surviving
    # market keeps its raw allocation (existing single-DMA behavior).
    for p_cat, info in pin_info.items():
        if info["drop_others"]:
            continue
        p_mask = cats_upper == p_cat
        if not p_mask.any():
            continue
        p_rows = [(idx, _fbp(df.at[idx, bp_col]))
                  for idx in df.index[p_mask]]
        p_rows = [(i, v) for i, v in p_rows if v is not None]
        p_total = sum(v for _, v in p_rows)
        if p_total > 0 and abs(p_total - 100.0) > 0.001:
            scale = 100.0 / p_total
            for idx, v in p_rows:
                normed = round(v * scale, 4)
                df.at[idx, bp_col] = f"{normed:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * normed / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * normed / 100.0))

    # Category Share for non-demo categories.
    for cat, grp in df.groupby(cat_col):
        cu = str(cat).strip().upper()
        if cu in {"BRAND INPUT", "BRAND CATEGORY", "SAMPLE SIZE",
                  "INPUT_METADATA", "BRAND ID", "REPORT INPUT"}:
            continue
        if cu in DEMO_CATS_TF or cu == "LOCATION":
            continue
        bp_sum = 0.0
        for i in grp.index:
            v = _fbp(df.at[i, bp_col])
            if v is not None:
                bp_sum += v
        if bp_sum <= 0:
            continue
        for i in grp.index:
            v = _fbp(df.at[i, bp_col])
            if v is not None:
                df.at[i, cs_col] = round(v / bp_sum * 100.0, 4)

    # Defining-brand pin for behavioral cuts (Jenna 2026-09-08). Runs
    # AFTER the Category Share recompute so the pin isn't overwritten,
    # and gated on `not pins` because pinned dimensions (age / gender
    # / geo / compound) size deterministically off the parent - their
    # bucket handling is already correct and no brand identity pin is
    # implied. enforce_no_collisions (Phase 4 in synthesize_addon_cut)
    # skips rows at exactly 100 so the pin survives collision fixup.
    n_defining_pin = 0
    defining_brand = ""
    if not pins:
        try:
            n_defining_pin, defining_brand = _pin_defining_brand_to_100(
                df, cut_def, subject, new_sample, new_uspop,
                bp_col, cs_col, raw_col, proj_col,
            )
        except Exception as _pin_err:
            # Never let a pin failure kill the transform. The row-by-row
            # reasoning still landed a valid frame; a 99.something on
            # the defining brand is a display defect, not a data
            # integrity one.
            print(f"    defining-brand pin skipped ({_pin_err})")

    return df, {
        "new_sample_size": new_sample,
        "new_us_pop": new_uspop,
        "n_pin_rows": n_pin_rows,
        "n_demo_rows": n_demo,
        "n_brand_rows": n_brand,
        "n_no_claude_jitter_rows": n_unchanged,
        "n_subject_pin_rows": n_subject_pin,
        "n_location_dropped": len(drop_idx),
        "n_defining_brand_pin_rows": n_defining_pin,
        "defining_brand": defining_brand,
    }


# =============================================================================
# Ladder-up: generalized partition coherence
# =============================================================================
def enforce_partition_coherence(df_source, cut_frames: list,
                                subject: str, *,
                                tolerance_pp: float = 2.0,
                                min_coverage: float = 0.90):
    """Generalization of gender_split_coherence to N cuts.

    cut_frames: list of (cut_def, df_cut, cohort_fraction). When the
    cuts partition the parent (disjoint pins covering >= min_coverage
    of the source), enforce for every shared brand row:

        sum(p_i * cut_i[brand]) / sum(p_i)  ~=  source[brand]

    Correction preserves each cut's relative tilt and slides the level
    (pure additive shift - NOT a multiplier). Subject self-pins are
    exempt. Returns (corrected_frames, stats).
    """
    fracs = [f for _, _, f in cut_frames]
    coverage = sum(fracs)
    stats = {"coverage": round(coverage, 4), "applied": False,
             "n_corrected": 0}
    if coverage < min_coverage or len(cut_frames) < 2:
        return cut_frames, stats

    # Disjointness check on the pins - overlapping cuts (e.g. two age
    # bands sharing a bucket) can't form a partition.
    seen_buckets = set()
    for cd, _, _ in cut_frames:
        pb = {(cd["pin_category"].upper(), str(b).strip().upper())
              for b in cd["pin_buckets"]}
        if pb & seen_buckets:
            return cut_frames, stats
        seen_buckets |= pb

    def _index(df):
        bp_col, _, _, _ = _detect_cols(df)
        out = {}
        for i in df.index:
            cat = str(df.at[i, "Column"]).strip().upper()
            val = str(df.at[i, "Value"]).strip().upper()
            if cat in SKIP_CATS or cat in DEMO_CATS_TF or \
                    cat == "LOCATION":
                continue
            v = _fbp(df.at[i, bp_col])
            if v is not None:
                out[(cat, val)] = (i, v)
        return out

    src_idx = _index(df_source)
    cut_idxs = [_index(df) for _, df, _ in cut_frames]
    n_corrected = 0

    for key, (_, src_bp) in src_idx.items():
        if abs(src_bp - 100.0) < 0.01:
            continue  # subject self-pin family
        entries = []
        for ci, cidx in enumerate(cut_idxs):
            if key in cidx:
                entries.append((ci, cidx[key]))
        if len(entries) != len(cut_frames):
            continue  # brand must exist in every cut to enforce
        wavg = sum(fracs[ci] * v for ci, (_, v) in entries) / coverage
        delta = src_bp - wavg
        if abs(delta) <= tolerance_pp:
            continue
        # Slide every cut by the same delta (keeps tilts), clamp, and
        # break residual 4dp identity with a salted micro-jitter.
        for ci, (row_i, v) in entries:
            cd, dfc, _f = cut_frames[ci]
            bp_col, cs_col, raw_col, proj_col = _detect_cols(dfc)
            nv = v + delta + _seed_jitter(
                f"{subject}|{key[0]}|{key[1]}|coh|{cd.get('cut_id')}",
                span=0.02)
            nv = max(0.0001, min(99.49, round(nv, 4)))
            dfc.at[row_i, bp_col] = f"{nv:.4f}%"
        n_corrected += 1

    # Recompute raw/proj/category share on corrected frames from their
    # own sample sizes.
    fixed_frames = []
    for cd, dfc, f in cut_frames:
        bp_col, cs_col, raw_col, proj_col = _detect_cols(dfc)
        cats = dfc["Column"].astype(str).str.upper().str.strip()
        ssm = cats == "SAMPLE SIZE"
        smp, usp = 0, 0
        if ssm.any():
            try:
                smp = float(str(dfc[ssm].iloc[0][raw_col]).replace(",", ""))
                usp = float(str(dfc[ssm].iloc[0][proj_col]).replace(",", ""))
            except Exception:
                pass
        if smp > 0:
            for i in dfc.index:
                cu = str(dfc.at[i, "Column"]).strip().upper()
                if cu in {"SAMPLE SIZE", "BRAND INPUT", "BRAND CATEGORY",
                          "INPUT_METADATA", "BRAND ID", "REPORT INPUT"}:
                    continue
                v = _fbp(dfc.at[i, bp_col])
                if v is None:
                    continue
                dfc.at[i, raw_col] = float(round(smp * v / 100.0))
                dfc.at[i, proj_col] = float(round(usp * v / 100.0))
        fixed_frames.append((cd, dfc, f))

    stats.update({"applied": True, "n_corrected": n_corrected})
    return fixed_frames, stats


# =============================================================================
# Orchestrator
# =============================================================================
def synthesize_demo_cut(
    source: str,
    cut_def: dict,
    *,
    source_kind: str = "s3_key",
    dry_run: bool = False,
    register_in_dashboard: bool = True,
    subject_override: Optional[str] = None,
    api_key_pool: Optional[list] = None,
    max_workers: Optional[int] = None,
    ship_gate: bool = True,
) -> dict:
    """End-to-end add-on cut off a parent profile. Mirrors
    audience_cut_synthesis.synthesize_audience_cut phase-for-phase with
    the generalized pin."""
    import boto3
    s3 = boto3.client("s3", region_name=REGION)

    df_source, kind = _load_source_df(source, source_kind=source_kind)
    snap = build_source_snapshot(df_source)
    subject = (str(subject_override).strip() if subject_override
               else snap["subject"])
    source_label = (os.path.basename(source) if kind == "local_path"
                    else source)
    # 2026-08-25: fold compound / region spec shapes into the classic
    # cut_def form (raises loudly when a region's market list can't
    # resolve - silent flattening is banned).
    cut_def = _normalize_cut_def(cut_def)
    cut_def, promo_note = _promote_unpinned_pin_cut(df_source, cut_def)
    if promo_note:
        print(f"[addon-cut] {promo_note}")
    # DMA labels bind to the parent's real market rows (city + state
    # asks resolve to the full Nielsen name; no-match raises loudly).
    cut_def = _resolve_location_buckets(df_source, cut_def)
    behavioral = _is_behavioral_cut(cut_def)
    print(f"[addon-cut] subject={subject!r} cut={cut_def.get('label')!r} "
          + (f"behavioral={_cohort_desc(cut_def)!r} " if behavioral else
             f"pins=[{_pin_desc(cut_def)}] ")
          + f"source={source_label}")

    if behavioral:
        # No demo pin to read the fraction off - Phase 1 reasoning IS
        # the sizing (grounded prompt, clamped to sane subset bounds).
        pin_shares = {}
        print(f"[addon-cut] -> Phase 1: audience reasoning ...")
        audience = reason_addon_audience(snap, cut_def, source_label)
        cf = float(audience.get("cohort_fraction") or 0.0)
        cf = max(0.005, min(0.75, cf if cf > 0 else 0.25))

        # 2026-09-08 (Jenna): behavioral cuts sized off a specific
        # brand / platform must respect two invariants:
        #   1. INTERNAL: cf <= parent's own BP for the matched brand.
        #      If the parent reads 48% Instagram, the "Instagram
        #      users" cut can't exceed 48% of the parent.
        #   2. EXTERNAL: new_uspop <= real-world US audience for the
        #      brand. A 5M-follower creator's Instagram cut can't
        #      project 6M people.
        # Both fail-safe: no brand token in label OR no parent match
        # OR external research fails means the phase-1 cf passes
        # through. See _apply_behavioral_ceilings.
        try:
            _bp_col_src, _, _raw_col_src, _proj_col_src = \
                _detect_cols(df_source)
            _cats_upper = (df_source["Column"].astype(str)
                           .str.upper().str.strip())
            _ss_mask = _cats_upper == "SAMPLE SIZE"
            _old_sample = 0.0
            _old_uspop = 0.0
            if _ss_mask.any():
                _ss_row = df_source[_ss_mask].iloc[0]
                try:
                    _old_sample = float(str(_ss_row[_raw_col_src])
                                        .replace(",", ""))
                except Exception:
                    _old_sample = 0.0
                try:
                    _old_uspop = float(str(_ss_row[_proj_col_src])
                                       .replace(",", ""))
                except Exception:
                    _old_uspop = 0.0
        except Exception:
            _old_sample, _old_uspop = 0.0, 0.0
        try:
            cf_new, ceiling_notes = _apply_behavioral_ceilings(
                cf, df_source, cut_def, subject,
                _old_sample, _old_uspop)
        except Exception as e:
            print(f"[addon-cut]    ceiling application failed "
                  f"(fail-safe pass-through): {e}")
            cf_new, ceiling_notes = cf, {}
        if ceiling_notes:
            audience["ceiling_notes"] = ceiling_notes
            if cf_new < cf - 1e-9:
                brand = ceiling_notes.get("brand") or "?"
                int_c = ceiling_notes.get("internal_ceiling")
                ext_c = ceiling_notes.get("external_ceiling")
                src = ceiling_notes.get("brand_source") or ""
                ext_src = ceiling_notes.get("external_source") or ""
                bits = [f"phase1={cf:.4f}"]
                if int_c is not None:
                    bits.append(f"internal={int_c:.4f} "
                                f"[{src}]" if src
                                else f"internal={int_c:.4f}")
                if ext_c is not None:
                    bits.append(f"external={ext_c:.4f}"
                                + (f" [{ext_src}]" if ext_src else ""))
                bits.append(f"final={cf_new:.4f}")
                print(f"[addon-cut]    brand ceiling applied for "
                      f"{brand!r}: " + " | ".join(bits))
            else:
                nb = ceiling_notes.get("brand") or ""
                nn = ceiling_notes.get("note") or ""
                if nb:
                    int_c = ceiling_notes.get("internal_ceiling")
                    ext_c = ceiling_notes.get("external_ceiling")
                    print(f"[addon-cut]    brand ceiling check for "
                          f"{nb!r}: phase1 already tightest "
                          f"(internal="
                          f"{'n/a' if int_c is None else f'{int_c:.4f}'}, "
                          f"external="
                          f"{'n/a' if ext_c is None else f'{ext_c:.4f}'})"
                          )
                elif nn:
                    # Non-brand behavioral (e.g. Digital Purchasers),
                    # ceiling path deliberately skipped.
                    pass
        cf = cf_new

        audience["cohort_fraction"] = cf
        audience["deterministic_cf"] = False
        print(f"[addon-cut]    reasoned cohort_fraction={cf:.4f} "
              f"(behavioral - no deterministic source)")
    else:
        det_cf, pin_shares, det_note = deterministic_cut_fraction(
            df_source, cut_def)
        if not det_cf or det_cf <= 0:
            raise RuntimeError(
                f"pins [{_pin_desc(cut_def)}] not found in source - "
                f"cannot size the cut deterministically"
                + (f" ({det_note})" if det_note else ""))

        print(f"[addon-cut] -> Phase 1: audience reasoning ...")
        audience = reason_addon_audience(snap, cut_def, source_label)
        old_cf = float(audience.get("cohort_fraction") or 0)
        override_with_deterministic_fraction(audience, det_cf,
                                             note=det_note)
        print(f"[addon-cut]    deterministic cohort_fraction={det_cf:.4f} "
              f"(claude said {old_cf:.4f})  pin_shares={pin_shares}"
              + (f"  [{det_note}]" if det_note else ""))

    cat_col, val_col = "Column", "Value"
    bp_col, _, _, _ = _detect_cols(df_source)
    cats_upper = df_source[cat_col].astype(str).str.upper().str.strip()
    all_non_demo = []
    for cat, _ in df_source.groupby(cats_upper):
        cu = str(cat).strip().upper()
        if cu in DEMO_CATS_TF or cu in SKIP_CATS or cu == "":
            continue
        all_non_demo.append(cu)
    cat_sizes = {c: int((cats_upper == c).sum()) for c in all_non_demo}
    cats_to_call = sorted(all_non_demo, key=lambda c: -cat_sizes[c])
    total_rows = sum(cat_sizes.values())
    print(f"[addon-cut] -> Phase 2: {len(cats_to_call)} categories "
          f"({total_rows} rows) row-by-row ...")
    # 2026-08-20 (Jenna: "make the parallelization change for
    # cuts"): categories are independent, so they run concurrently on
    # the key pool exactly like the fresh-build engine. Chunks WITHIN
    # a category stay sequential (no-truncation rule); reasoning is
    # unchanged.
    try:
        from cut_parallel import load_cut_key_pool, resolve_cut_workers
    except Exception:
        from migration.cut_parallel import (
            load_cut_key_pool, resolve_cut_workers,
        )
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading as _th
    _keys = list(api_key_pool) if api_key_pool else load_cut_key_pool()
    _n_workers = (int(max_workers) if max_workers
                  else resolve_cut_workers(_keys))
    cat_rows = {}
    for cat in cats_to_call:
        rows = []
        for _, r in df_source[cats_upper == cat].iterrows():
            v = _fbp(r.get(bp_col, 0))
            if v is None:
                continue
            rows.append((str(r.get(val_col, "")).strip(), v))
        if rows:
            cat_rows[cat] = rows
    print(f"[addon-cut]    parallel: {_n_workers} workers over "
          f"{max(len(_keys), 1)} key(s)")
    cat_decisions = {}
    _done = {"n": 0}
    _plock = _th.Lock()

    def _reason_one(_idx, _cat):
        _key = _keys[_idx % len(_keys)] if _keys else None
        return reason_category_rows_addon(
            subject, audience, _cat, cat_rows[_cat], cut_def,
            df_source=df_source, api_key=_key)

    with ThreadPoolExecutor(max_workers=_n_workers) as _ex:
        _futs = {_ex.submit(_reason_one, _i, _c): _c
                 for _i, _c in enumerate(cat_rows)}
        for _fut in as_completed(_futs):
            _c = _futs[_fut]
            try:
                decisions = _fut.result()
            except Exception as _e:
                print(f"[addon-cut]    {_c} FAILED: {_e}", flush=True)
                decisions = {}
            if decisions:
                cat_decisions[_c] = decisions
            with _plock:
                _done["n"] += 1
                print(f"[addon-cut]    [{_done['n']:>2d}/{len(cat_rows)}] "
                      f"{_c:32s} rows={len(cat_rows[_c]):>4d} "
                      f"decided={len(decisions):>4d}", flush=True)

    print(f"[addon-cut] -> Phase 3: transform ...")
    df_cut, stats = apply_addon_cut_transform(
        df_source, cut_def, audience, cat_decisions, subject, pin_shares)
    print(f"[addon-cut]    {stats}")
    # Log the defining-brand pin outcome. On a match it reads
    # "defining-brand pin: 'Instagram' -> 100.0000 in 1 row(s)".
    # On a skip (generic behavioral / no parent match / non-behavioral
    # cut) it stays silent - the stats dict already carries
    # n_defining_brand_pin_rows for diagnostics.
    _n_dpin = int(stats.get("n_defining_brand_pin_rows") or 0)
    if _n_dpin > 0:
        print(f"[addon-cut]    defining-brand pin: "
              f"{stats.get('defining_brand')!r} -> 100.0000 in "
              f"{_n_dpin} row(s)")

    # Phase 3b (2026-08-24 Furious audit D1): non-pinned demo categories
    # stay anchored to the parent's shape. The Millennials cut and Los
    # Angeles cut shipped male-leaning GENDER against a 55.4%-female
    # parent because the demo re-reasoning was unconstrained. Only the
    # cut's own pinned dimension (AGE for an age cut, LOCATION for a
    # geo cut) is reshaped; everything else clamps to parent +/-4pp,
    # never inverting the parent's majority bucket. Behavioral cuts
    # (no pin_category) reason their whole demo shape and are exempt.
    _anchor_pin_cats = {c for c, _ in _cut_pins(cut_def)}
    if _anchor_pin_cats:
        try:
            try:
                from cut_demo_anchor import anchor_nonpinned_demos_to_parent
            except ImportError:
                from migration.cut_demo_anchor import (  # type: ignore
                    anchor_nonpinned_demos_to_parent,
                )
            df_cut, _anchor_stats = anchor_nonpinned_demos_to_parent(
                df_cut, df_source, subject,
                pin_category=_anchor_pin_cats,
                cut_salt=str(cut_def.get("cut_id")
                             or cut_def.get("label") or "cut"),
            )
            print(f"[addon-cut]    demo anchor: {_anchor_stats}")
        except Exception as _anchor_err:
            print(f"[addon-cut] demo anchor failed (non-fatal): "
                  f"{_anchor_err}")

    print(f"[addon-cut] -> Phase 4: no-collision enforcement ...")
    df_cut, n_fixed = enforce_no_collisions(df_cut, df_source, subject)
    print(f"[addon-cut]    re-jittered {n_fixed} rows")

    subj_clean = re.sub(r"\s+", " ", subject).strip()
    # Naming convention (Jenna 2026-08-20): '{Subject} - {Cut}', where
    # the cut part is the clean name_label ('18-24', 'Gen Z',
    # 'Los Angeles Ca') - not the chat-facing label ('Ages 18-24',
    # 'Gen Z (18-24)', 'Los Angeles Ca only').
    label_clean = re.sub(r"[()]", "", str(cut_def.get("name_label")
                                          or cut_def.get("label")
                                          or cut_def.get("cut_id"))).strip()
    label_clean = re.sub(r"\s+", " ", label_clean)
    out_key = f"{subj_clean} - {label_clean}.csv"

    # BRAND CATEGORY inherit safeguard (same as the gender module).
    try:
        col_u_bc = df_cut["Column"].astype(str).str.strip().str.upper()
        bc_mask = col_u_bc == "BRAND CATEGORY"
        bc_value = ""
        if bc_mask.any():
            bc_value = str(df_cut.loc[bc_mask, "Value"].iloc[0]).strip()
        if not bc_value or bc_value.upper() in ("UNKNOWN", "NAN", "NONE"):
            src_col_u = df_source["Column"].astype(str).str.strip().str.upper()
            src_mask = src_col_u == "BRAND CATEGORY"
            if src_mask.any():
                bc_value = str(df_source.loc[src_mask, "Value"].iloc[0]).strip()
            if bc_value and not bc_mask.any():
                import pandas as _pd
                new_row = {c: "" for c in df_cut.columns}
                new_row[df_cut.columns[0]] = "BRAND CATEGORY"
                new_row[df_cut.columns[1]] = bc_value
                ss_idx = df_cut.index[col_u_bc == "SAMPLE SIZE"].tolist()
                insert_at = ss_idx[0] + 1 if ss_idx else 2
                df_cut = _pd.concat(
                    [df_cut.iloc[:insert_at], _pd.DataFrame([new_row]),
                     df_cut.iloc[insert_at:]], ignore_index=True)
    except Exception as _bc_err:
        print(f"[addon-cut] BRAND CATEGORY safeguard skipped: {_bc_err}")

    # Write-time safety net (BP/Raw/Proj/CS canonicalization).
    try:
        from post_generation_enforcers import run_write_safety_net
        df_cut, _ = run_write_safety_net(df_cut, subject, verbose=False)
    except Exception as _sn_err:
        print(f"[addon-cut] write-safety-net raised (non-fatal): {_sn_err}")

    # Terminal invariant polish (2026-08-20 EST Buyers batch): echo-row
    # strip, cohort self-pin to exactly 100, depin exact-2dp rows,
    # Raw/Proj + CS recompute - the cut's SUBJECT is the cohort name
    # ("{Parent} - {Label}"), so pin that.
    _cut_subject = f"{subj_clean} - {label_clean}"

    # Cohort self-pin row insert (2026-08-21, Amazon TVOD Renters;
    # REWORKED 2026-08-24 Furious audit D5): the pinned row must carry
    # the CLEAN PARENT SUBJECT ('Furious'), never the deliverable label
    # ('Furious Viewers - Millennials 25-44') and never a dash-orphan
    # ('- Millennials') when `subject` arrives empty. Derived cuts copy
    # the parent's rows, so the clean subject row usually already
    # exists at ~100 in the native grid - keep it and skip the insert
    # (the polish re-pins it to exactly 100). Only when NO clean-named
    # row exists anywhere do we insert one, always under the clean
    # name resolved off the file itself.
    try:
        import pandas as _pd_pin
        try:
            from post_generation_enforcers import (
                _clean_subject_from_bi as _csfb,
            )
        except ImportError:
            from migration.post_generation_enforcers import (  # type: ignore
                _clean_subject_from_bi as _csfb,
            )

        def _norm_id_pin(s):
            return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())

        _col_u = df_cut["Column"].astype(str).str.strip().str.upper()
        _bi_rows = df_cut.loc[_col_u == "BRAND INPUT", "Value"]
        _bi_val = str(_bi_rows.iloc[0]).strip() if len(_bi_rows) else ""
        _pin_name = str(_csfb(_bi_val, df=df_cut, col_u=_col_u,
                              subject_arg=subject) or "").strip()
        _skip_pin = {"BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY",
                     "SUBJECT", "INPUT_METADATA"}
        if not _pin_name:
            print("[addon-cut] cohort self-pin skipped: no resolvable "
                  "clean subject (empty subject + no BRAND INPUT/SUBJECT "
                  "anchor) - never inserting a bare cut label")
        else:
            _vals_norm = df_cut["Value"].astype(str).map(_norm_id_pin)
            _have = ((_vals_norm == _norm_id_pin(_pin_name))
                     & ~_col_u.isin(_skip_pin))
            if _have.any():
                # Parent self-pin row inherited; polish re-pins it to
                # exactly 100 - nothing to insert.
                pass
            else:
                # Native grid: prefer the BRAND CATEGORY grid; fall back
                # to the highest-BP grid of any clean-subject row (none
                # here by construction) or the first non-meta category.
                _grid = None
                _bc = df_cut.loc[_col_u == "BRAND CATEGORY", "Value"]
                if len(_bc):
                    _bc_val = str(_bc.iloc[0]).strip()
                    if _bc_val and (_col_u == _bc_val.upper()).any():
                        _grid = _bc_val
                if _grid:
                    _bi = df_cut.loc[_col_u == "BRAND INPUT"]
                    _raw_c = next((c for c in df_cut.columns
                                   if "raw" in c.lower()), None)
                    _proj_c = next((c for c in df_cut.columns
                                    if "projection" in c.lower()), None)
                    _sample = float(str(_bi.iloc[0][_raw_c])
                                    .replace(",", "")
                                    ) if len(_bi) and _raw_c else 0.0
                    _uspop = float(str(_bi.iloc[0][_proj_c])
                                   .replace(",", "")
                                   ) if len(_bi) and _proj_c else 0.0
                    _new = {c: "" for c in df_cut.columns}
                    _new["Column"] = _grid
                    _new["Value"] = _pin_name
                    for _c in df_cut.columns:
                        _cl = _c.lower()
                        if "penetration" in _cl:
                            _new[_c] = "100.0000"
                        elif "raw" in _cl:
                            _new[_c] = _sample
                        elif "projection" in _cl:
                            _new[_c] = _uspop
                    _at = df_cut.index[_col_u == _grid.upper()]
                    _at = int(_at[0]) if len(_at) else len(df_cut)
                    df_cut = _pd_pin.concat(
                        [df_cut.iloc[:_at], _pd_pin.DataFrame([_new]),
                         df_cut.iloc[_at:]], ignore_index=True)
                    print(f"[addon-cut] cohort self-pin row inserted: "
                          f"{_grid} | {_pin_name}")
    except Exception as _pin_err:
        print(f"[addon-cut] cohort self-pin insert skipped: {_pin_err}")

    # Shared terminal cut write gate (2026-08-24 Furious audit D2/D5/
    # D6): final invariant polish (cohort-label guard + subject re-pin
    # + depin + SUBJECT-row backstop) -> parent no-collision recheck ->
    # numeric-artifact normalize -> canonical sort -> loud pre-upload
    # audit. Replaces the inline polish + sort blocks this module used
    # to carry so every derived-cut path runs the identical chain.
    try:
        try:
            from migration.cut_write_gate import finalize_cut_for_upload
        except ImportError:
            from cut_write_gate import finalize_cut_for_upload  # type: ignore
        df_cut, _gate_report = finalize_cut_for_upload(
            df_cut, _cut_subject, parent_df=df_source, out_key=out_key,
            verbose=True,
            # Final ship gate (2026-08-24 Jenna mandate): blocking on
            # real uploads, report-only on dry runs and on the local
            # ops override (ship_gate kwarg).
            ship_gate=(bool(ship_gate) and not dry_run),
        )
    except Exception as _cwg_err:
        # ShipGateError is the blocking verdict - never swallow it.
        try:
            from migration.final_ship_gate import ShipGateError
        except ImportError:
            from final_ship_gate import ShipGateError  # type: ignore
        if isinstance(_cwg_err, ShipGateError):
            raise
        print(f"[addon-cut] cut write gate raised (non-fatal): {_cwg_err}")

    # Gen Pop baseline columns (Jenna 2026-08-22): terminal append after
    # every enforcer / safety net / sort so the raw file ships with the
    # current Gen Pop value + index per matched row. Non-fatal.
    try:
        try:
            from migration.genpop_baseline import append_genpop_columns
        except ImportError:
            from genpop_baseline import append_genpop_columns  # type: ignore
        df_cut = append_genpop_columns(df_cut)
    except Exception as _gp_err:
        print(f"   [genpop_baseline] append skipped: {_gp_err}")

    if dry_run:
        return {"out_key": out_key, "status": "dry-run",
                "audience": audience, "stats": stats,
                "n_collisions_fixed": n_fixed, "df_cut": df_cut}

    body = df_cut.to_csv(index=False).encode("utf-8")
    try:
        s3.head_object(Bucket=BUCKET, Key=out_key)
        s3.copy_object(
            Bucket=BUCKET,
            Key=(f"_backups/{out_key}.pre_cut_overwrite_"
                 f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                 f".csv"),
            CopySource={"Bucket": BUCKET, "Key": out_key})
    except Exception:
        pass
    s3.put_object(Bucket=BUCKET, Key=out_key, Body=body,
                  ContentType="text/csv")

    register_status = None
    if register_in_dashboard:
        try:
            from dashboard_register import register_profile_in_dashboard
            register_status = register_profile_in_dashboard(
                out_key,
                display_name=f"{subj_clean} - {label_clean}",
                source_key=(source if kind == "s3_key" else None),
                s3_client=s3)
            print(f"[addon-cut] registered in dashboard: {out_key}")
        except Exception as e:
            print(f"[addon-cut] dashboard register skipped: {e}")

    return {"out_key": out_key, "status": "uploaded",
            "audience": audience, "stats": stats,
            "n_collisions_fixed": n_fixed,
            "register_status": register_status}


__all__ = [
    "synthesize_demo_cut",
    "apply_addon_cut_transform",
    "_normalize_cut_def",
    "_cut_pins",
    "_resolve_region_dmas",
    "reason_addon_audience",
    "reason_category_rows_addon",
    "enforce_partition_coherence",
    "_pin_shares_from_source",
    "deterministic_cut_fraction",
]
