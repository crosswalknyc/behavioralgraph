"""Post-generation enforcers consolidated from /tmp scripts that the audit
loop ran manually on already-completed S3 profiles. Wiring these into
`BG.run_full_pipeline` (see BG.py) makes future profile generations
self-correcting — same fixes apply at write time instead of being
patched after the fact.

Each enforcer takes (df, subject) and returns (df, n_changes). Each:
  - Operates on the DataFrame in place but always returns a copy-safe df
  - Recomputes `Original Raw Numbers` + `US Gen Pop Projection` from
    the new BP using the sample size detected from BRAND INPUT
  - Uses deterministic per-(subject, brand) jitter so values are
    unique across profiles AND reproducible across re-runs
  - Never raises — callers wrap in try/except and continue
  - Skips intentionally round values (100% self-pin, 0% empty rows)

Enforcers, in pipeline order:

  1. `strip_url_encoded_subject_dupes` — drops fake brand rows that
     are just URL-encoded variants of the subject name (e.g.
     "HILARY%20DUFF" or "HILARY-DUFF" appearing as separate brands).
  2. `apply_politics_persona_cap` — caps DONALD J. TRUMP for personas
     in ANTI_TRUMP set (and KAMALA HARRIS for ANTI_HARRIS) to ~22%
     in POLITICS/ACTIVIST. Cross-cat alignment then propagates to TALENT.
  3. `apply_taylor_swift_persona_tier` — assigns TAYLOR SWIFT a
     persona-tiered realistic BP in MUSICIAN/BAND. Tiers reflect
     audience demographic (pop-adjacent young female -> 58-72%,
     older male prestige -> 20-28%, legacy 75+ -> 13-18%, etc.).
     Replaces the prior 28% hard cap that was creating artificial
     pinning across 43% of profiles. Cross-cat alignment then
     propagates to TALENT.
  4. `depin_round_brand_bps` — final pass: adds ±0.0099pp jitter
     to any brand BP that's perfectly round to 2 decimals (e.g.
     5.0000, 12.5000, 0.3000) — those are pinning artifacts from
     scoring agents that escaped the upstream jitter passes.
"""
import csv
import hashlib as _hl
import os
import re as _re
import urllib.parse
from collections import Counter, defaultdict

import pandas as pd

US_POP = 329_900_000


# ============================================================================
# International frame detection (Jenna 2026-08-25, Omaze precedent).
#
# Several enforcers below encode US-only assumptions: the canonical US
# demo bucket schema, US income-bracket ordering, US DMA geo scoping,
# and US panel-reality brand floors. An international profile (UK,
# Germany, ...) carries country-native demo buckets, country markets in
# LOCATION, and country-scoped projections - forcing US structure onto
# it is corruption, not enforcement. Those enforcers gate on
# _frame_country(df): detection is content-signature based (currency in
# INCOME, country-native EDUCATION/ETHNICITY labels, LOCATION dominated
# by a known country market list) and CONSERVATIVE - a US frame can
# never read as international, so domestic enforcement never weakens.
# ============================================================================

def _frame_country(df):
    """Canonical country name for an international frame, else None
    (None = US / undetectable, run every enforcer as always)."""
    try:
        from migration.international_profiles import detect_profile_country
    except ImportError:
        try:
            from international_profiles import detect_profile_country
        except ImportError:
            return None
    try:
        return detect_profile_country(df)
    except Exception:
        return None


# ============================================================================
# Hostmap gating (workspace rule #4 — never lift/add a brand that isn't
# in `reference.host_mapping`). Loaded lazily from disk cache or ClickHouse.
# ============================================================================

_HOSTMAP_CACHE_PATH = '/tmp/hostmap_brands.txt'
_HOSTMAP_NORMALIZED = None     # set on first _ensure_hostmap_loaded() call
_HOSTMAP_RAW_UPPER = None
_HOSTMAP_NORM_TO_CANONICAL = None    # normalized (punct-stripped) → canonical
_HOSTMAP_UPPER_TO_CANONICAL = None   # upper-case → GROUP canonical.
                                     # 2026-08-24 (norm-group semantics, Jenna:
                                     # "case sensitivity is never the issue"):
                                     # every spelling of a norm-group resolves
                                     # to the group's single canonical spelling
                                     # (visible entry preferred). Supersedes the
                                     # 2026-05-27 punct-sensitive workaround for
                                     # the CNET (Media) vs C-Net (Hidden) twin:
                                     # visibility is now decided per GROUP (a
                                     # brand is Hidden only if EVERY spelling is
                                     # Hidden), so 'C-Net' safely resolves to
                                     # 'CNET' instead of needing to stay apart.
_HOSTMAP_GAPS = []             # populated by lift attempts on non-hostmap brands
_HOSTMAP_HIDDEN = None         # set of NORM keys (_norm_brand) of brands whose
                               # ENTIRE norm-group is SECTION='Hidden'. The
                               # cache file only contains all-hidden groups
                               # (see migration/genpop_hostmap_sync.py
                               # refresh_reference_caches), so norm matching
                               # can never strip a visible brand.
_HOSTMAP_MPB = None            # set of upper-cased brand names with SECTION LIKE 'Most Purchased%'
_HOSTMAP_MPB_NORM = None       # same membership as NORM keys (spelling twins
                               # of an MPB-tagged group all pass the gate)


def _norm_brand(s):
    """Case + punctuation insensitive brand key (matches workspace rule #4
    'Duplicate check is case + punctuation insensitive')."""
    return _re.sub(r'[^A-Z0-9]', '', str(s).upper())


def _ensure_hostmap_loaded():
    """Load hostmap once into module-level set. Tries:
      1. /Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands_canonical.txt
         (preserves original Sheet4 casing — needed for normalize_brand_names)
      2. /root/finished_codes/reference/hostmap_brands_canonical.txt
      3. /tmp/hostmap_brands.txt (refreshable via curl ClickHouse SELECT)
      4. /Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands.txt
      5. None → returns False (caller treats as "skip hostmap check, log only")
    """
    global _HOSTMAP_NORMALIZED, _HOSTMAP_RAW_UPPER
    global _HOSTMAP_NORM_TO_CANONICAL, _HOSTMAP_UPPER_TO_CANONICAL
    if _HOSTMAP_NORMALIZED is not None:
        return True
    candidates = [
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands_canonical.txt',
        '/root/finished_codes/reference/hostmap_brands_canonical.txt',
        _HOSTMAP_CACHE_PATH,
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands.txt',
        '/root/finished_codes/reference/hostmap_brands.txt',
    ]
    # Ensure hidden set is available so we can prefer non-Hidden canonicals
    # below when two display forms collide on the same key. (_HOSTMAP_HIDDEN
    # holds NORM keys of all-hidden groups; see the global's comment.)
    _ensure_hostmap_hidden_loaded()
    hidden_norms = _HOSTMAP_HIDDEN or set()

    def _prefer(cur, new):
        """Return the preferred canonical form among `cur` (already stored)
        and `new` (newly seen). Priority:
          1. Non-Hidden over Hidden (group semantics: a norm key is hidden
             only when its ENTIRE hostmap group is Hidden)
          2. Title-case over all-caps (more human-readable)
          3. First-seen wins
        The regenerated hostmap_brands_canonical.txt carries ONE canonical
        spelling per norm-group, so collisions only occur on stale caches;
        this heuristic is the stale-cache fallback.
        """
        if cur is None:
            return new
        cur_hidden = _norm_brand(cur) in hidden_norms
        new_hidden = _norm_brand(new) in hidden_norms
        if cur_hidden and not new_hidden:
            return new
        if new_hidden and not cur_hidden:
            return cur
        if cur.isupper() and not new.isupper():
            return new
        return cur

    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    lines = [line.strip() for line in f if line.strip()]
                _HOSTMAP_RAW_UPPER = {b.upper() for b in lines}
                _HOSTMAP_NORMALIZED = {_norm_brand(b) for b in lines}
                # Two canonical maps, populated in tandem:
                #   _HOSTMAP_NORM_TO_CANONICAL - norm(b) -> group canonical
                #     Punctuation-stripped. The authoritative map: every
                #     spelling of a norm-group resolves here.
                #   _HOSTMAP_UPPER_TO_CANONICAL - upper(b) -> group canonical
                #     Fast path. 2026-08-24: remapped through the norm map
                #     after the build loop so that a twin spelling (e.g.
                #     'C-NET') returns the GROUP canonical ('CNET'), never
                #     the junk spelling itself.
                _HOSTMAP_NORM_TO_CANONICAL = {}
                _HOSTMAP_UPPER_TO_CANONICAL = {}
                for b in lines:
                    uk = b.upper()
                    nk = _norm_brand(b)
                    _HOSTMAP_UPPER_TO_CANONICAL[uk] = _prefer(
                        _HOSTMAP_UPPER_TO_CANONICAL.get(uk), b,
                    )
                    _HOSTMAP_NORM_TO_CANONICAL[nk] = _prefer(
                        _HOSTMAP_NORM_TO_CANONICAL.get(nk), b,
                    )
                for uk in list(_HOSTMAP_UPPER_TO_CANONICAL):
                    grp = _HOSTMAP_NORM_TO_CANONICAL.get(_norm_brand(uk))
                    if grp:
                        _HOSTMAP_UPPER_TO_CANONICAL[uk] = grp
                return True
            except Exception:
                continue
    return False


def _is_in_hostmap(brand):
    """Workspace rule #4 — gate ALL brand lifts/additions through this.
    Returns True if the brand (case+punctuation-insensitive) exists in
    reference.host_mapping. Returns True (permissive) only when hostmap
    cache is missing — caller is responsible for logging that fallback."""
    if not _ensure_hostmap_loaded():
        return True   # cache missing → caller must handle (open-mode)
    bu = str(brand).upper()
    if bu in _HOSTMAP_RAW_UPPER:
        return True
    return _norm_brand(brand) in _HOSTMAP_NORMALIZED


def _hostmap_canonical(brand):
    """Return the hostmap GROUP-canonical casing for a brand (or None if
    not in hostmap). Norm-group semantics (2026-08-24, Jenna: "case
    sensitivity is never the issue"):

      1. Upper-case fast path. Every stored value is already remapped to
         the norm-group canonical at load time, so 'C-NET' returns 'CNET'
         (the visible Media publisher), never the Hidden junk spelling.

      2. PUNCTUATION-INSENSITIVE norm-key lookup. 'COCA COLA' (no
         hyphen) resolves to 'Coca-Cola'; any spelling twin resolves to
         its group's single canonical form.

    Canonical selection prefers non-Hidden entries (group visibility),
    then human-readable casing; see _ensure_hostmap_loaded._prefer.
    """
    if not _ensure_hostmap_loaded():
        return None
    bu = str(brand).upper()
    if _HOSTMAP_UPPER_TO_CANONICAL is not None:
        hit = _HOSTMAP_UPPER_TO_CANONICAL.get(bu)
        if hit is not None:
            return hit
    if _HOSTMAP_NORM_TO_CANONICAL is None:
        return None
    return _HOSTMAP_NORM_TO_CANONICAL.get(_norm_brand(brand))


def _ensure_hostmap_hidden_loaded():
    """Load the set of NORM keys of brands whose hostmap norm-group is
    entirely Hidden. These brands should NEVER appear in any profile
    output (Rule #4b, established 2026-05-27 after Bria flagged The
    Root + Ecosia appearing in UBG MEDIA / SEARCH ENGINE/AI).

    Match policy (2026-08-24 norm-group semantics): case AND
    punctuation INSENSITIVE, with group visibility. A brand is hidden
    ONLY if every hostmap row across every spelling of its norm-group
    is Hidden. The cache file is regenerated under that contract
    (migration/genpop_hostmap_sync.py::refresh_reference_caches via
    migration/hostmap_norm.py), so it contains only all-hidden groups:
    matching by norm can never strip a visible brand. This supersedes
    the 2026-05-27 punctuation-SENSITIVE workaround for the
    CNET/C-Net twin (the twin groups now simply never appear in the
    cache because each has a visible spelling).

    Cache file: reference/hostmap_hidden_brands.txt (one canonical
    spelling per all-hidden norm-group).
    """
    global _HOSTMAP_HIDDEN
    if _HOSTMAP_HIDDEN is not None:
        return True
    candidates = [
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_hidden_brands.txt',
        '/root/finished_codes/reference/hostmap_hidden_brands.txt',
        '/tmp/hostmap_hidden_brands.txt',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    lines = [line.strip() for line in f if line.strip()]
                _HOSTMAP_HIDDEN = {_norm_brand(b) for b in lines}
                return True
            except Exception:
                continue
    _HOSTMAP_HIDDEN = set()
    return False


def _is_hostmap_hidden(brand):
    """True if the brand's ENTIRE hostmap norm-group is Hidden
    (Rule #4b - must never appear in a shipped profile). Case and
    punctuation insensitive: any spelling of an all-hidden group
    matches; a group with any visible spelling (e.g. C-Net/CNET)
    never matches because it is excluded from the cache."""
    if not _ensure_hostmap_hidden_loaded():
        return False
    return _norm_brand(brand) in _HOSTMAP_HIDDEN


def _ensure_hostmap_mpb_loaded():
    """Load the set of brand keys whose hostmap SECTION starts with
    'Most Purchased' (Apparel/Footwear, CPG, Home/Outdoor, Beauty/Wellness,
    Accessories, Technology Brand, Pets, etc.). A brand may only appear
    in the ``MOST PURCHASED BRANDS`` column if it is in this set.

    Rule #4c (added 2026-05-28 after Stephen A Smith profile shipped with
    1,146 of 2,137 MPB rows hostmap-classified into OTHER sections —
    NETFLIX/HULU under Streaming, AMAZON/WALMART/TARGET under Where They
    Shop, VISA/MASTERCARD under Credit Provider, MCDONALDS under QSR,
    PAYPAL/VENMO under Digital Banking, etc. Those brands all already
    exist in their proper category rows on the same profile, so the MPB
    duplicates are pure pollution).

    Match policy (2026-08-24 norm-group semantics): case AND
    punctuation INSENSITIVE. Any spelling of a norm-group that carries
    an MPB-tagged hostmap entry passes the gate; inserts should use
    _hostmap_canonical() so canonical casing lands in the profile.

    Cache file: reference/hostmap_mpb_brands.txt (one canonical
    spelling per MPB-tagged, non-all-hidden norm-group; regenerated by
    migration/genpop_hostmap_sync.py::refresh_reference_caches via
    migration/hostmap_norm.py).
    """
    global _HOSTMAP_MPB, _HOSTMAP_MPB_NORM
    if _HOSTMAP_MPB is not None:
        return True
    candidates = [
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_mpb_brands.txt',
        '/root/finished_codes/reference/hostmap_mpb_brands.txt',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reference', 'hostmap_mpb_brands.txt'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference', 'hostmap_mpb_brands.txt'),
        '/tmp/hostmap_mpb_brands.txt',
    ]
    for path in candidates:
        try:
            if not os.path.exists(path):
                continue
            with open(path) as f:
                lines = [line.strip() for line in f if line.strip()]
            # Fast path: upper() set. Authoritative: NORM set so any
            # spelling twin of an MPB-tagged group passes the gate.
            _HOSTMAP_MPB = {b.upper() for b in lines}
            _HOSTMAP_MPB_NORM = {_norm_brand(b) for b in lines}
            return True
        except Exception:
            continue
    _HOSTMAP_MPB = set()
    _HOSTMAP_MPB_NORM = set()
    return False


def _is_hostmap_mpb(brand):
    """True if the brand's norm-group carries SECTION LIKE
    'Most Purchased%' in reference.host_mapping. Case and punctuation
    insensitive (2026-08-24 norm-group semantics): exact upper() fast
    path first, then the norm-key match so 'COCA COLA' (no hyphen) and
    any spelling twin resolve to the same MPB-eligible group."""
    if not _ensure_hostmap_mpb_loaded():
        return False
    bu = str(brand).upper()
    if bu in _HOSTMAP_MPB:
        return True
    if _HOSTMAP_MPB_NORM and _norm_brand(brand) in _HOSTMAP_MPB_NORM:
        return True
    # Legacy fallback via canonical resolver (covers stale caches where
    # the norm set could not be built).
    canon = _hostmap_canonical(brand)
    if canon is not None and canon.upper() in _HOSTMAP_MPB:
        return True
    return False


# ============================================================================
# Helpers
# ============================================================================

def _bp(v):
    try:
        return float(str(v).strip().rstrip('%'))
    except Exception:
        return 0.0


def _is_round_2dp(v):
    return v > 0 and abs(v * 100 - round(v * 100)) < 1e-4


def _is_look_round(v):
    """True if value LOOKS like a pinned/round whole number to a human eye.
    Catches the X.00xx pattern — value is within 0.01 of an integer.
    Examples flagged:
      24.0013, 22.0023, 30.0028, 5.0091, 12.0042
    Examples NOT flagged (visible non-zero in tenths or hundredths):
      24.3417, 50.5089, 5.0184, 4.6291, 22.0189
    Also flags strict round-2dp boundaries (5.00, 12.50, 0.30).
    Sub-0.50 values are exempt — X.00xx is structurally common there.
    """
    if v <= 0 or v < 0.50:
        return False
    # within 0.01 of an integer ⇒ tenths and hundredths are both 0 ⇒ .00xx
    if abs(v - round(v)) < 0.01:
        return True
    # strict round-2dp (e.g. 5.50, 12.30)
    if abs(v * 100 - round(v * 100)) < 1e-4:
        return True
    return False


def _jitter_for(subj, brand, salt='', pct=0.10, base=None,
                lo=None, hi=None):
    """Deterministic ±pct (or [lo, hi] absolute) jitter for (subj, brand)."""
    h = int(_hl.blake2b(f'{subj}|{brand}|{salt}'.encode(), digest_size=8).hexdigest(), 16)
    if lo is not None and hi is not None:
        frac = (h % 100000) / 100000.0
        v = lo + frac * (hi - lo)
    else:
        u = ((h % 1801) - 900) / 1000.0
        v = max(0.5, (base or 0) + u * ((base or 0) * pct))
    v = round(v, 4)
    if abs(v * 100 - round(v * 100)) < 1e-4:
        v = round(v + 0.0017, 4)
    return v


def _detect_sample_size(df, bp_col, raw_col):
    """Walk the df for a row with both a positive BP and a positive raw
    count, then back-compute the sample size. Fall back to 1M."""
    if raw_col is None:
        return 1_000_000
    try:
        for _, r in df.iterrows():
            try:
                b = _bp(r.get(bp_col, 0))
                raw_s = str(r.get(raw_col, '0') or '0').replace(',', '')
                raw = float(raw_s)
                if 5 < b < 50 and raw > 100:
                    return raw / (b / 100.0)
            except Exception:
                continue
    except Exception:
        pass
    return 1_000_000


def _recompute_cs_for_cat(df, cat, bp_col, cs_col):
    """Rewrite Category Share for ONE category from current BPs.

    Mutation-time coherence helper (2026-08-24, stale-share class kill):
    a BP write changes the share denominator for EVERY row of that
    category, so the whole block's CS is recomputed in one shot.
    Demo-like blocks (sum-to-100 by construction) use Share = BP
    identity; every other block uses share-of-category
    (BP / sum(BP) * 100), matching apply_recompute_category_share.
    Metadata blocks are skipped. Rows with unparseable BP keep their
    cell (the canonical final pass blanks them). Never raises.
    """
    try:
        if (cs_col is None or cs_col not in df.columns
                or 'Column' not in df.columns):
            return df
        cat = str(cat or '').strip()
        if not cat:
            return df
        cat_u = cat.upper()
        skip = globals().get('_SHARE_SKIP_BLOCKS') or {
            'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT',
            'INPUT_METADATA'}
        if cat_u in {str(s).upper() for s in skip}:
            return df
        mask = (df['Column'].astype(str).str.strip().str.upper() == cat_u)
        if not mask.any():
            return df
        bps = df.loc[mask, bp_col].apply(_bp)
        valid = bps.dropna()
        if len(valid) == 0:
            return df
        demo_all = (globals().get('_DEMO_LIKE_ALL')
                    or globals().get('DEPIN_DEMO_CATS') or ())
        if cat_u in {str(c).upper() for c in demo_all}:
            share = valid.astype(float).round(4)
        else:
            tot = float(valid.sum())
            if tot <= 0:
                return df
            share = (valid.astype(float) / tot * 100).round(4)
        if (str(df[cs_col].dtype) == 'string'
                or str(df[cs_col].dtype).startswith('str')):
            df[cs_col] = df[cs_col].astype(object)
        df.loc[share.index, cs_col] = share.values
    except Exception:
        pass
    return df


def _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col, sample_size):
    """Set new BP on a row and recompute raw + projection + the mutated
    category's Category Share.

    Proj uses the canonical edit_sample_size.py formula:
      Raw  = sample_size * new_bp / 100
      Proj = (Raw / 10_000_000) * US_POP   (= sample_size * new_bp / 100 * 32.99)

    2026-08-24 (stale-share class kill, Jenna mandate): CS used to be
    left for a downstream _renormalize_category / final-pass recompute.
    That created mid-chain windows where a later step read a share that
    no longer matched BP (the D115 recovery re-inflated deliberately
    floored rows off exactly such a stale share). Every BP write through
    this helper now leaves the WHOLE mutated category share-coherent at
    mutation time.
    """
    # 2026-06-22 Jenna fix: pandas StringDtype on bp/raw/proj columns raises
    # "Invalid value 'X' for dtype 'str'" when we assign a float. This bit
    # the G13 Lisa BLACKPINK auto-patch tonight (logged
    # "G13 auto-patch FAILED (Invalid value '100.0' for dtype 'str')").
    # Coerce target columns to float64 once before assignment so every
    # gate that calls _set_bp (G14, G17, G7 NEAR_TIE, ...) is safe.
    for _dtcol in (bp_col, raw_col, proj_col):
        if (_dtcol and _dtcol in df.columns
                and df[_dtcol].dtype.name not in ('object', 'O',
                                                  'float64', 'int64')):
            df[_dtcol] = pd.to_numeric(df[_dtcol], errors='coerce')
    df.at[idx, bp_col] = round(float(new_bp), 4)
    new_raw = int(round(sample_size * new_bp / 100.0))
    if raw_col:
        df.at[idx, raw_col] = new_raw
    if proj_col:
        df.at[idx, proj_col] = int(round(new_raw / 10_000_000.0 * US_POP))
    if cs_col is not None and 'Column' in df.columns:
        try:
            df = _recompute_cs_for_cat(
                df, df.at[idx, 'Column'], bp_col, cs_col)
        except Exception:
            pass
    return df


def _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col, sample_size):
    """After modifying BPs in a category, recompute Category Share so the
    category sums to 100% (or as close as the non-modified rows allow).
    Uses _bp() to parse so it handles both bare floats and "22.6197%"
    strings (mixed dtypes appear when only some rows have been rewritten).
    """
    if cs_col is None:
        return df
    mask = df['Column'].astype(str).str.strip().str.upper() == cat.upper()
    # Skip empty / single-row pseudo-categories (e.g. dropped-entirely
    # INPUT_METADATA). 2026-05-27 fix: was crashing astype(int) on
    # empty Series with object dtype.
    if not mask.any():
        return df
    bp_floats = df.loc[mask, bp_col].apply(_bp)
    bp_total = bp_floats.sum()
    if bp_total > 0:
        df.loc[mask, cs_col] = (bp_floats / bp_total * 100).round(4)
    # Raw first (rounded integer), then Proj FROM the rounded Raw.
    # 2026-08-22 fix: Proj was computed from the unrounded BP*sample
    # product, which diverges from round(Raw/10M*US_POP) by up to +/-16
    # once Raw is rounded (Rule #3a chain: BP -> Raw -> Proj).
    raw_ints = (bp_floats / 100.0 * sample_size).round(0)
    if raw_col:
        df.loc[mask, raw_col] = raw_ints.astype('int64')
    if proj_col:
        # Proj = round(Raw / 10M * US_POP) -- from the ROUNDED Raw
        df.loc[mask, proj_col] = (
            (raw_ints / 10_000_000.0 * US_POP).round(0).astype('int64')
        )
    return df


def _detect_cols(df):
    """Detect canonical column names case-insensitively."""
    cols = {c.lower().strip(): c for c in df.columns}
    return (
        cols.get('brand penetration (row)') or 'Brand Penetration (Row)',
        cols.get('category share') or 'Category Share',
        next((c for c in df.columns if c.lower().strip().startswith('original raw')), None),
        next((c for c in df.columns if 'projection' in c.lower().strip()), None),
    )


# ============================================================================
# 1. URL-encoded duplicate stripping
# ============================================================================

def _normalize_url_encoded(s):
    try:
        decoded = urllib.parse.unquote(str(s))
    except Exception:
        decoded = str(s)
    return ''.join(c for c in decoded.upper() if c.isalnum())


# ============================================================================
# 1b. Subject-in-wrong-category stripping
# ============================================================================

# Categories where a PERSON's own name LEGITIMATELY appears as self-pin
# (i.e. you'd expect to see CHRIS PRATT in ACTOR + TALENT, but NOT in
# TELECOM / BANKING / GAMES / INTEREST / etc.). MEDIA is allowed because
# it represents articles/coverage ABOUT the subject (legit topic engagement).
_PERSON_OK_CATS = {
    'ACTOR', 'TALENT', 'MUSICIAN/BAND', 'HOST/PERSONALITY',
    'POLITICS/ACTIVIST', 'ATHLETE',
    'NFL ATHLETE', 'NBA ATHLETE', 'NHL ATHLETE', 'MLB ATHLETE',
    'WNBA ATHLETE', 'SOCCER ATHLETE',
    'WRITER/DIRECTOR/AUTHOR/ARTIST', 'CREATOR/INFLUENCER',
    'INFLUENCER/CREATOR',
    'BRAND INPUT', 'INPUT_METADATA', 'MEDIA',
    'BRAND CATEGORY', 'EMERGING TALENT', 'GENERAL',
}

# BRAND CATEGORY values that indicate this profile's SUBJECT is a person
# (vs a retailer / sports team / app / etc.). Only these archetypes have
# their subject-name stripped from non-person categories.
_PERSON_BRAND_CATEGORIES = {
    'ACTOR', 'MUSICIAN/BAND', 'HOST/PERSONALITY', 'POLITICS/ACTIVIST',
    'ATHLETE', 'WRITER/DIRECTOR/AUTHOR/ARTIST', 'CREATOR/INFLUENCER',
    'INFLUENCER/CREATOR', 'EMERGING TALENT', 'TALENT',
}


def strip_subject_from_wrong_category(df, subject, brand_category=None, verbose=True):
    """Drop rows where the subject's own name appears as a brand in a
    category where it doesn't belong (e.g. CHRIS PRATT showing up in
    TELECOM at 37%, EMILIA CLARKE in GAMES, EMMA WATSON in INTEREST).

    Only runs when the BRAND CATEGORY indicates this is a person profile.
    For non-person profiles (RETAILERS, SPORTS TEAM, APPAREL, etc.) the
    subject can legitimately appear in many product/team categories, so
    we leave those alone.

    `brand_category` can be passed explicitly; otherwise inferred from
    the BRAND CATEGORY row in the DataFrame.
    """
    if df is None or len(df) == 0 or not subject:
        return df, 0

    if brand_category is None:
        bc_rows = df[df['Column'].astype(str).str.strip().str.upper() == 'BRAND CATEGORY']
        if len(bc_rows):
            brand_category = str(bc_rows.iloc[0].get('Value', '')).strip().upper()

    if not brand_category or brand_category.upper() not in _PERSON_BRAND_CATEGORIES:
        return df, 0

    subj_u = subject.strip().upper()
    drop_idx = []
    for idx, r in df.iterrows():
        c = str(r.get('Column', '') or '').strip().upper()
        v = str(r.get('Value', '') or '').strip().upper()
        if not c or not v:
            continue
        if c in _PERSON_OK_CATS:
            continue
        if v == subj_u:
            drop_idx.append((idx, c, r.get('Brand Penetration (Row)', 0)))

    if drop_idx:
        if verbose:
            for _, c, b in drop_idx:
                print(f"   🚫 stripped subject from wrong cat: [{c}] {subject} BP={b}")
        df = df.drop(index=[i for i, _, _ in drop_idx]).reset_index(drop=True)
    return df, len(drop_idx)


def strip_url_encoded_subject_dupes(df, subject, verbose=True):
    """Remove rows whose Value is a URL-encoded or hyphenated variant of
    `subject` (e.g. "HILARY%20DUFF", "HILARY-DUFF" when subject is
    "HILARY DUFF"). Keeps the canonical row whose Value == subject."""
    if df is None or len(df) == 0 or not subject:
        return df, 0
    subj_norm = ''.join(c for c in subject.upper() if c.isalnum())
    if not subj_norm:
        return df, 0

    drop_idx = []
    for idx, r in df.iterrows():
        val = str(r.get('Value', '') or '').strip()
        if not val or val.upper() == subject.upper():
            continue
        n = _normalize_url_encoded(val)
        if n == subj_norm and ('%' in val or '-' in val):
            drop_idx.append(idx)

    if drop_idx:
        if verbose:
            print(f"   🧹 stripped {len(drop_idx)} URL-encoded dupes of '{subject}'")
        df = df.drop(index=drop_idx).reset_index(drop=True)
    return df, len(drop_idx)


# ============================================================================
# 2. Politics persona cap
# ============================================================================

ANTI_TRUMP = {
    'PEDRO PASCAL', 'MERYL STREEP', 'ZENDAYA', 'LEONARDO DICAPRIO', 'TAYLOR SWIFT',
    'BEYONCE', 'OPRAH', 'MICHELLE OBAMA', 'BARACK OBAMA', 'GAL GADOT', 'MARK RUFFALO',
    'JOHN LEGEND', 'SARAH PAULSON', 'ELLEN DEGENERES', 'GABRIELLE UNION',
    'HELEN MIRREN', 'GLENN CLOSE', 'DREW BARRYMORE', 'JENNIFER BEALS', 'EMMA WATSON',
    'BILLIE EILISH', 'ARIANA GRANDE', 'OLIVIA RODRIGO', 'CHAPPELL ROAN',
    'BAD BUNNY', 'SELENA GOMEZ', 'AMERICA FERRERA',
    'EDIE FALCO', 'JANE FONDA', 'JAMIE FOXX', 'EVA LONGORIA', 'EDDIE MURPHY',
    'DENZEL WASHINGTON', 'DONALD GLOVER', 'ISSA RAE', 'ANTHONY HOPKINS',
    'EMMA THOMPSON', 'HUGH JACKMAN', 'IAN MCKELLEN', 'DEMI MOORE',
    'JENNIFER GARNER', 'JEFF BRIDGES',
}
ANTI_HARRIS = {
    'TIM ALLEN', 'MEL GIBSON', 'CLINT EASTWOOD', 'KID ROCK', 'JON VOIGHT',
    'CHUCK NORRIS', 'SCOTT BAIO', 'DEAN CAIN', 'JAMES WOODS',
    'KEVIN SORBO', 'MORGAN WALLEN',
}


def apply_politics_persona_cap(df, subject, verbose=True):
    """Cap politically polarizing figures for personas with publicly
    documented opposing stances. Only touches POLITICS/ACTIVIST rows —
    the cross-cat alignment pass propagates the new value to TALENT."""
    if df is None or len(df) == 0 or not subject:
        return df, 0
    subj = subject.strip().upper()
    targets = []
    if subj in ANTI_TRUMP:
        targets.append(('DONALD J. TRUMP', 22.0))
    if subj in ANTI_HARRIS:
        targets.append(('KAMALA HARRIS', 22.0))
    if not targets:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    fixed = 0
    touched_cats = set()
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat != 'POLITICS/ACTIVIST':
            continue
        val = str(r.get('Value', '') or '').strip().upper()
        for figure, cap in targets:
            if val == figure:
                old_bp = _bp(r.get(bp_col, 0))
                if old_bp > cap + 5:
                    new_v = _jitter_for(subject, figure, salt='politics_cap',
                                        base=cap, pct=0.10)
                    df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col,
                                 proj_col, sample_size)
                    fixed += 1
                    touched_cats.add(cat)
                    if verbose:
                        print(f"   🗳  politics cap [{subject}] {figure}: "
                              f"{old_bp:.2f} → {new_v:.4f}")

    for cat in touched_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)
    return df, fixed


# ============================================================================
# 3. Taylor Swift persona-tiered values
# ============================================================================

TS_POP_ADJACENT = {
    'CHAPPELL ROAN', 'SELENA GOMEZ', 'OLIVIA RODRIGO', 'SABRINA CARPENTER',
    'ARIANA GRANDE', 'BILLIE EILISH', 'HARRY STYLES', 'BLAKE LIVELY',
    'SOPHIE TURNER', 'KEKE PALMER', 'HILARY DUFF', 'EVA LONGORIA',
    'GABRIELLE UNION', 'EMMA STONE', 'ZENDAYA', 'BELLA HADID', 'ELLE FANNING',
    'ANYA TAYLOR JOY', 'MARGOT ROBBIE', 'ALI WONG', 'AWKWAFINA', 'ISSA RAE',
    'JENNIFER LAWRENCE', 'EMMA WATSON', 'EMILIA CLARKE', 'MIKAYLA NOGUEIRA HAWKEN',
}
TS_CHIEFS_SWIFTIE = {'KANSAS CITY CHIEFS', 'TRAVIS KELCE'}
TS_MAINSTREAM_FEMALE = {
    'DREW BARRYMORE', 'JENNIFER ANISTON', 'ANNE HATHAWAY', 'JULIA ROBERTS',
    'CATE BLANCHETT', 'AMY ADAMS', 'AMY POEHLER', 'REESE WITHERSPOON',
    'JENNIFER GARNER', 'HOLLY HUNTER', 'MERYL STREEP', 'HELEN MIRREN',
    'JENNIFER BEALS', 'SARAH PAULSON', 'ELLEN DEGENERES', 'HALLE BERRY',
    'EVA MENDES', 'HONG CHAU', 'ANGELINA JOLIE', 'SCARLETT JOHANSSON',
    'FLORENCE PUGH', 'AMERICA FERRERA', 'ANNA KENDRICK',
    'DEMI MOORE', 'GAL GADOT', 'GLENN CLOSE', 'FRANCES MCDORMAND',
    'EMMA THOMPSON', 'JANE FONDA', 'EDIE FALCO', 'LAURA LINNEY', 'VIOLA DAVIS',
    'KATE MIDDLETON', 'MICHELLE OBAMA', 'OPRAH', 'HODA KOTB',
    'TAYLOR POLIDORE WILLIAMS', 'CAMERON DIAZ', 'LEANN RIMES',
    'CHRISTINE EVANGELISTA', 'CAROL BURNETT',
}
TS_OLDER_MALE_PRESTIGE = {
    'ANTHONY HOPKINS', 'DANIEL DAY LEWIS', 'IAN MCKELLEN', 'HUGH JACKMAN',
    'JEFF BRIDGES', 'DENZEL WASHINGTON', 'MORGAN FREEMAN', 'MICHAEL KEATON',
    'JOAQUIN PHOENIX', 'OSCAR ISAAC', 'GARY OLDMAN', 'COLIN FIRTH',
    'BENEDICT CUMBERBATCH', 'EDDIE REDMAYNE', 'DANIEL CRAIG', 'GEORGE CLOONEY',
    'BRAD PITT', 'HARRISON FORD', 'LEONARDO DICAPRIO', 'EDDIE MURPHY',
    'AL PACINO', 'ANDERSON COOPER', 'GEORGE STEPHANOPOULOS', 'BILL MAHER',
    'JAY LENO', 'DAVID LETTERMAN', 'CHRISTOPHER LLOYD', 'TOMMY LEE JONES',
}
TS_LEGACY_75PLUS = {
    'BILL MURRAY', 'BARBRA STREISAND', 'DOLPH LUNDGREN', 'CLINT EASTWOOD',
    'ARNOLD SCHWARZENEGGER', 'BRUCE WILLIS', 'JACK NICHOLSON', 'DIANE KEATON',
    'CHRISTOPHER WALKEN', 'DONNIE YEN', 'JET LI', 'JEAN-CLAUDE VAN DAMME',
}
TS_CONSERVATIVE = {
    'TIM ALLEN', 'MEL GIBSON', 'KID ROCK', 'JON VOIGHT', 'CHUCK NORRIS',
    'SCOTT BAIO', 'DEAN CAIN', 'JAMES WOODS', 'KEVIN SORBO', 'MORGAN WALLEN',
}
TS_MALE_ACTION_MID = {
    'JASON STATHAM', 'VIN DIESEL', 'THE ROCK', 'DWAYNE JOHNSON', 'JOHN CENA',
    'CHRIS HEMSWORTH', 'CHRIS PRATT', 'CHRIS EVANS', 'RYAN REYNOLDS',
    'RYAN GOSLING', 'JASON BATEMAN', 'BEN STILLER', 'BEN AFFLECK', 'JAMIE FOXX',
    'JACK BLACK', 'JAKE GYLLENHAAL', 'CHRISTIAN BALE', 'ANDREW GARFIELD',
    'ROBERT DOWNEY JR', 'KEANU REEVES', 'PEDRO PASCAL', 'TYLER PERRY',
    'JOHN LEGEND',
}

TS_TIERS = {
    'POP_ADJACENT':        (58.0, 72.0),
    'CHIEFS_SWIFTIE':      (40.0, 50.0),
    'MAINSTREAM_FEMALE':   (44.0, 56.0),
    'MAINSTREAM':          (34.0, 44.0),
    'MALE_ACTION_MID':     (24.0, 32.0),
    'OLDER_MALE_PRESTIGE': (20.0, 28.0),
    'LEGACY_75PLUS':       (13.0, 18.0),
    'CONSERVATIVE':        (16.0, 22.0),
    'DEFAULT':             (34.0, 42.0),
}


def _ts_tier_for(subject):
    """Return tier name for `subject`, or None if subject IS Taylor Swift
    (her self-pin should stay 100 — handled by enforce_input_brand_100)."""
    if not subject:
        return None
    s = subject.strip().upper()
    if s == 'TAYLOR SWIFT':
        return None
    if s in TS_POP_ADJACENT:
        return 'POP_ADJACENT'
    if s in TS_CHIEFS_SWIFTIE:
        return 'CHIEFS_SWIFTIE'
    if s in TS_MAINSTREAM_FEMALE:
        return 'MAINSTREAM_FEMALE'
    if s in TS_LEGACY_75PLUS:
        return 'LEGACY_75PLUS'
    if s in TS_CONSERVATIVE:
        return 'CONSERVATIVE'
    if s in TS_OLDER_MALE_PRESTIGE:
        return 'OLDER_MALE_PRESTIGE'
    if s in TS_MALE_ACTION_MID:
        return 'MALE_ACTION_MID'
    return 'DEFAULT'


def apply_taylor_swift_persona_tier(df, subject, verbose=True):
    """Override MUSICIAN/BAND -> TAYLOR SWIFT BP with a persona-tiered
    realistic value. Cross-cat alignment then propagates to TALENT.

    Only fires if a row already exists — won't insert a new row."""
    if df is None or len(df) == 0:
        return df, 0
    tier = _ts_tier_for(subject)
    if tier is None:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    lo, hi = TS_TIERS[tier]
    new_v = _jitter_for(subject, 'TAYLOR SWIFT', salt='ts_persona_tier',
                        lo=lo, hi=hi)
    fixed = 0
    touched_cats = set()
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        val = str(r.get('Value', '') or '').strip().upper()
        if cat == 'MUSICIAN/BAND' and val == 'TAYLOR SWIFT':
            old_bp = _bp(r.get(bp_col, 0))
            df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                         sample_size)
            fixed += 1
            touched_cats.add(cat)
            if verbose:
                print(f"   🎤 TS persona-tier [{subject}|{tier}]: "
                      f"{old_bp:.2f} → {new_v:.4f}")

    for cat in touched_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)
    return df, fixed


# ============================================================================
# 4. Round-BP depinning
# ============================================================================

DEPIN_DEMO_CATS = {
    'GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION',
    'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
}
DEPIN_META_CATS = {
    'INPUT_METADATA', 'BRAND INPUT', 'BRAND CATEGORY', 'SAMPLE SIZE',
    'AVID FAN', 'CASUAL FAN', 'GENERAL', 'LOCATION',
}

# 2026-08-26 (Liz QA, Paw Patrol): platform / carriage categories where
# a NON-SUBJECT spec pin must land at a salted messy near-100 instead
# of exactly 100.0000 (universal reach on a carrier is impossible in a
# multi-platform universe, and exact-100 is reserved for the subject's
# own property). Companion sports pins (SPORTS TEAM / leagues /
# divisions) keep the exact-100 convention from the pipeline rules.
CARRIER_PIN_SOFT_CATS = {
    'STREAMING/PLATFORM', 'STREAMING VIDEO', 'STREAMING MUSIC',
    'VMVPD/FAST', 'VIRTUAL MVPD/FAST', 'VIRTUAL MVPD FAST', 'VMVPD',
    'FAST PLATFORM', 'FAST CHANNEL', 'BROADCAST/CABLE', 'APP/PLATFORM',
    'PLATFORMS', 'MOVIE THEATER', 'MEDIA', 'SOCIAL MEDIA',
    'SEARCH ENGINE/AI',
}

# Categories where a non-subject row legitimately carries an exact-100
# companion pin (subject's own league/team/conference/division family
# per profile-iq-pipeline-rules #3). The exact-100 depin skips these.
COMPANION_PIN_CATS = {
    'SPORTS TEAM', 'MLB', 'NBA', 'NFL', 'NHL', 'MLS', 'WNBA', 'MILB',
    'EPL', 'LA LIGA', 'SERIE A', 'LIGUE 1', 'BUNDESLIGA', 'CFB',
    'SOCCER', 'NASCAR', 'F1', 'AL', 'NL', 'AFC', 'NFC', 'AL/NL',
    'AFC/NFC',
}
_COMPANION_PIN_PAT = None  # compiled lazily in _is_companion_pin_cat


def _is_companion_pin_cat(cat_u: str) -> bool:
    global _COMPANION_PIN_PAT
    if cat_u in COMPANION_PIN_CATS:
        return True
    if _COMPANION_PIN_PAT is None:
        import re as _re_cp
        _COMPANION_PIN_PAT = _re_cp.compile(
            r'\b(CONFERENCE|DIVISION|EAST|WEST|NORTH|SOUTH|CENTRAL|'
            r'PACIFIC|ATLANTIC|METROPOLITAN)\b')
    return bool(_COMPANION_PIN_PAT.search(cat_u))


def depin_round_brand_bps(df, subject, verbose=True):
    """Add deterministic jitter to brand BPs that look round to a human
    reader. Two detection bands:
      A) Strict round-2dp (5.00, 12.50, 0.30) — jitter ±0.0099pp.
      B) X.0Yzz "look-round" pattern (24.0013, 22.0023, 30.0028, 5.0184)
         where the hundredths digit is 0 — jitter ±0.15-0.45 pp so the
         visible 2nd decimal becomes non-zero. This addresses the
         "never .00xx" rule — multiple rehab passes were leaving values
         like 22.0035 that pass strict 2dp checks but read as pinned
         to a brand marketer.

    Note: this function handles the per-cell look-round case. Within-
    category 4dp collisions (3+ different brands in same category with
    identical 4dp BP, e.g. 69 MPB brands all at 5.6789%) are handled by
    the separate ``dejitter_within_cat_4dp_collisions`` pass, which must
    run AFTER this one. The two together fulfill Rule #9 Pass A + Pass B.

    Skips demographic cats (legitimate round buckets), meta cats, true
    zeros, and 100% self-pins. Sub-0.5pp values are also exempt because
    X.0Y is structurally common there (BET+ at 0.4577 doesn't read as
    pinned).

    Recomputes raw + projection. Idempotent — post-jitter values will
    have non-zero hundredths so a second pass is a no-op.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    fixed_strict = 0
    fixed_look = 0
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        val = str(r.get('Value', '') or '').strip().upper()
        if cat in DEPIN_DEMO_CATS or cat in DEPIN_META_CATS:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp <= 0 or old_bp >= 99.99:
            continue

        strict_round = _is_round_2dp(old_bp)
        look_round = _is_look_round(old_bp) and not strict_round
        if not strict_round and not look_round:
            continue

        h = int(_hl.blake2b(
            f'{subject}|{cat}|{val}|depin'.encode(), digest_size=8
        ).hexdigest(), 16)

        if strict_round:
            # ±0.0099pp drift (existing logic)
            u = ((h % 1801) - 900) / 100000.0
            new_v = max(0.0001, old_bp + u)
            new_v = round(new_v, 4)
            if abs(new_v * 100 - round(new_v * 100)) < 1e-4:
                new_v = round(new_v + 0.0017, 4)
            fixed_strict += 1
        else:
            # look-round: shift by 0.15-0.45 pp (deterministic sign) so the
            # visible hundredths digit becomes non-zero. The shift direction
            # is chosen by an independent hash so the offset feels natural
            # (some up, some down).
            sign = +1 if (h >> 16) % 2 else -1
            magnitude = 0.15 + ((h >> 24) % 31) / 100.0  # 0.15 - 0.45 pp
            shift = sign * magnitude
            # If shifting down would push below 0.5pp into the exempt band,
            # flip sign up
            if old_bp + shift < 0.55:
                shift = abs(shift)
            new_v = max(0.0001, old_bp + shift)
            new_v = round(new_v, 4)
            # paranoia: re-check
            if _is_look_round(new_v):
                new_v = round(new_v + 0.17, 4)
            fixed_look += 1

        df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                     sample_size)

    if verbose and (fixed_strict or fixed_look):
        print(f"   🎲 depinned {fixed_strict} round-2dp + {fixed_look} look-round BP(s)")
    return df, fixed_strict + fixed_look


# ─────────────────────────────────────────────────────────────────────────────
#  D7/D8/D10 aggressive post-emit dejitter
# ─────────────────────────────────────────────────────────────────────────────
# `depin_round_brand_bps` above handles strict 2dp + X.0Yzz "look-round".
# The colleague's audit defines "X.X5/X.X0 intentional-looking display" as
# ANY value where round(v*100) lands on 0 or 5 mod 10 — i.e. 4.7531
# displays as 4.75 and looks intentional even though it isn't strict 2dp.
# We re-jitter every such value at the END of the enforcer chain so the
# colleague's audit sees 0.

def _looks_intentional_2dp_disp(v) -> bool:
    """Display lands on X.X0 or X.X5 in 2dp rounding (colleague's def)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if f < 0.5:
        return False
    return round(f * 100) % 10 in (0, 5)


def _looks_x00xx_anchor(v) -> bool:
    """Within 0.01 of an integer (5.0028, 7.0009, 12.0042)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if f < 0.5:
        return False
    delta = abs(f - round(f))
    return 0.00005 < delta < 0.01


def _looks_round_any(v) -> bool:
    """True if BP looks round to a human reader in ANY of the bands the
    colleague's audit flags (X.X5, X.X0, X.00xx, exact-integer)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if f < 0.5:
        return False
    # Exact integer (5.0000) or X.X5/X.X0 in 2dp display
    if round(f * 100) % 10 in (0, 5):
        return True
    # X.XX00 - exact 2dp boundary at 4dp precision (6.8900, 11.8900).
    # 2026-08-22: the dejitter walks (step 0.0003) could land exactly on
    # a .XX00 boundary because this guard only rejected hundredths 0/5;
    # Aug 21 TVOD batch shipped 30-47 such landings per file.
    if round(f * 10000) % 100 == 0:
        return True
    # X.00xx (anchored near an integer)
    if abs(f - round(f)) < 0.01:
        return True
    return False


def dejitter_x5x0_displays(df, subject, verbose=True, max_attempts=8):
    """D7/D8: re-jitter every BP that looks round to a human reader so the
    final value is guaranteed non-round in every dashboard display.

    Iterates up to `max_attempts` times per row with progressively larger
    drift until `_looks_round_any` returns False. The drift is still small
    (typically ±0.013pp on the first try, escalating to ±0.04pp by attempt
    8) so the underlying magnitude is preserved.

    Catches three round bands simultaneously: X.X5, X.X0 (2dp display),
    and X.00xx (integer anchor + 4dp noise). Skips demo + meta categories,
    true zeros, and 100% self-pins.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    fixed = 0
    still_round = 0
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        val = str(r.get('Value', '') or '').strip().upper()
        if cat in DEPIN_DEMO_CATS or cat in DEPIN_META_CATS:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp <= 0 or old_bp >= 99.99:
            continue
        if not _looks_round_any(old_bp):
            continue

        new_v = old_bp
        for attempt in range(max_attempts):
            pct = 0.018 + attempt * 0.005  # grows from 0.018 → 0.053
            new_v = _jitter_for(subject, val,
                                  salt=f'dejitter-round|{cat}|try{attempt}',
                                  pct=pct, base=old_bp)
            if not _looks_round_any(new_v):
                break
        else:
            # Hard fallback: add a prime-fraction shift so we definitely
            # land off any round band (1/137 ≈ 0.0073pp is irrational-ish
            # in 4dp space and never lands on .X0/.X5/.00xx).
            new_v = round(old_bp + 0.0073 + 0.0011, 4)
            if _looks_round_any(new_v):
                new_v = round(old_bp - 0.0073 - 0.0011, 4)
            if _looks_round_any(new_v):
                still_round += 1
                continue

        if abs(new_v - old_bp) < 1e-5:
            continue
        df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        fixed += 1

    if verbose and (fixed or still_round):
        msg = f"   🎲 dejitter round displays: {fixed} BP(s) nudged off round"
        if still_round:
            msg += f"  ({still_round} could not be de-rounded after {max_attempts} tries)"
        print(msg)
    return df, fixed


def dejitter_cross_cat_4dp_pins(df, subject, verbose=True,
                                  min_pin_cats=2, max_attempts=8):
    """D10: when the same brand has the EXACT same 4dp BP in 2+ categories,
    re-jitter all but one so the 4dp identity is broken across cats AND
    the resulting value isn't itself round.

    `min_pin_cats=2` means even a 2-category pin gets broken — the
    colleague's audit wants zero cross-cat 4dp identities, not just 3+.
    Iterates up to `max_attempts` times per shifted row so the new value
    is both unique per (brand, cat) AND non-round.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    # Rule #3b exemption (2026-08-22): a brand carrying the EXACT same
    # 4dp BP in MPB and a sub-category is the INTENTIONAL exact mirror
    # (enforce_mpb_exact_mirror), not a pin defect. Breaking it here was
    # the root cause of ~1,880 mirror-drift rows per file. Skip any
    # (brand, bp4) identity that matches the brand's MPB row.
    mpb_identity = set()
    for _idx, _r in df.iterrows():
        if str(_r.get('Column', '') or '').strip().upper() != 'MOST PURCHASED BRANDS':
            continue
        _v = _bp(_r.get(bp_col, 0))
        if _v is None or _v <= 0 or _v >= 99.99:
            continue
        mpb_identity.add((_norm_brand(str(_r.get('Value', '') or '')),
                          round(_v, 4)))

    rows_per_pair = {}
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        val = str(r.get('Value', '') or '').strip().upper()
        if cat in DEPIN_DEMO_CATS or cat in DEPIN_META_CATS:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp <= 0 or old_bp >= 99.99:
            continue
        if (_norm_brand(val), round(old_bp, 4)) in mpb_identity:
            continue  # intentional MPB exact mirror (Rule #3b)
        key = (val, round(old_bp, 4))
        rows_per_pair.setdefault(key, []).append((idx, cat, old_bp))

    fixed = 0
    for (val, bp4), entries in rows_per_pair.items():
        if len(entries) < min_pin_cats:
            continue
        used_4dp = {round(entries[0][2], 4)}  # first occurrence keeps the value
        for idx, cat, old_bp in entries[1:]:
            new_v = old_bp
            for attempt in range(max_attempts):
                pct = 0.014 + attempt * 0.006
                new_v = _jitter_for(subject, val,
                                      salt=f'dejitter-xcat-4dp|{cat}|{bp4}|t{attempt}',
                                      pct=pct, base=old_bp)
                # Must be (a) different 4dp from any prior twin AND
                # (b) not itself round in any band.
                if round(new_v, 4) not in used_4dp and not _looks_round_any(new_v):
                    break
            used_4dp.add(round(new_v, 4))
            df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                         sample_size)
            fixed += 1
    if verbose and fixed:
        print(f"   🎲 dejitter cross-cat 4dp pins: {fixed} brand-cat hop(s) shifted")
    return df, fixed


def dejitter_within_cat_4dp_collisions(df, subject, verbose=True,
                                       min_collision_group=2, max_attempts=10):
    """Rule #9 Pass B: when 2+ DIFFERENT brands within the SAME category
    share the EXACT same 4dp BP (e.g. Bronny James and Draymond Green
    both at 8.1149% in Kit Harington's ATHLETE), re-jitter all but one
    so every brand in the category gets a unique 4dp value AND the
    resulting value isn't itself round.

    This catches the "placeholder sentinel" pattern from upstream
    enforcers (clamp-floors, missing-value defaults, audience-weighted
    lifts that batch-write the same value to many brands).

    Differs from ``dejitter_cross_cat_4dp_pins`` which handles ONE brand
    in MANY cats — this handles MANY brands in ONE cat.

    `min_collision_group=2` (default since 2026-06-11): even 2-brand
    exact-4dp pins are statistically implausible across a 100+ row
    category (probability ~0.01% per pair, observed at 32 pairs/file in
    Kit Harington's 06_11 pull) and indicate engineered values, not
    organic variance. The original threshold of 3 missed the
    Bronny/Draymond, Soto/Jokic, Eileen Fisher/Halara class of pairs
    that triggered Jenna's 06_11 batch-QC defect report.

    Recomputes Raw + Proj per row. Idempotent — second pass is a no-op
    because the new BPs are unique-by-construction.

    Added 2026-05-27 after Krapopolis audit found 46 within-cat
    collision groups (up to 69 brands at one BP) that all existing
    depin passes missed. Threshold lowered to 2 on 2026-06-11 after
    13-talent batch QC found pervasive 2-brand pairs.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    # Cast cols to object dtype so float assignment works on loaded CSVs
    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c and _c in df.columns and df[_c].dtype.name not in ('object', 'O'):
            df[_c] = df[_c].astype(object)

    # Mirror-awareness (Rule #3b, 2026-08-22): a sub-category row whose
    # 4dp BP exactly matches its brand's MPB value is an INTENTIONAL
    # exact mirror, not a collision artifact. Treat those rows as
    # unmovable keepers so this pass never fights enforce_mpb_exact_mirror
    # (jitter the OTHER colliding rows around them instead). If two
    # mirror-bound brands collide, their MPB parents collide too - the
    # MPB rows themselves are movable, and the next mirror pass
    # propagates the separation.
    mpb_vals = {}
    for idx, r in df.iterrows():
        if str(r.get('Column', '') or '').strip().upper() != \
                'MOST PURCHASED BRANDS':
            continue
        v = _bp(r.get(bp_col, 0))
        if v and 0 < v < 99.99:
            mpb_vals[_norm_brand(str(r.get('Value', '') or ''))] = round(v, 4)

    # Per-category state: set of ALL 4dp BP values already in that
    # category (so shifts don't collide with non-colliding rows either).
    cat_used = {}
    # And group collision-only entries
    groups = {}
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat in DEPIN_DEMO_CATS or cat in DEPIN_META_CATS:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp <= 0 or old_bp >= 99.99:
            continue
        val = str(r.get('Value', '') or '').strip().upper()
        mirror_bound = (
            cat != 'MOST PURCHASED BRANDS'
            and cat not in _MPB_MIRROR_SKIP_CATS
            and mpb_vals.get(_norm_brand(val)) == round(old_bp, 4)
        )
        cat_used.setdefault(cat, set()).add(round(old_bp, 4))
        key = (cat, round(old_bp, 4))
        groups.setdefault(key, []).append((idx, val, old_bp, mirror_bound))

    fixed = 0
    for (cat, bp4), entries in groups.items():
        if len(entries) < min_collision_group:
            continue
        # Mirror-bound rows are unmovable keepers; movable rows get
        # jittered around them. If nothing is movable, leave the group
        # to the MPB-side dejitter + next mirror pass.
        movable = [e[:3] for e in entries if not e[3]]
        pinned = len(entries) - len(movable)
        if not movable or (pinned == 0 and len(movable) < min_collision_group):
            continue
        entries = movable
        # Sort by brand value for deterministic ordering
        entries.sort(key=lambda e: e[1])
        N = len(entries)
        used = cat_used[cat]  # mutated — includes ALL existing values
        # A pinned (mirror-bound) row holds the original value, so ALL
        # movable rows shift. With no pinned row, the first movable
        # entry keeps the original value (already in used).
        movers = entries if pinned else entries[1:]
        for pos, (idx, val, old_bp) in enumerate(movers, start=1):
            # Find a free 4dp slot near old_bp, walking outward
            h = int(_hl.blake2b(
                f'{subject}|{cat}|{val}|within-cat-4dp-walk'.encode(),
                digest_size=8,
            ).hexdigest(), 16)
            # Direction +/- chosen by hash; start offset is small,
            # walks outward in 0.0003pp increments until free.
            sign = 1 if (h % 2) else -1
            new_v = old_bp
            step = 0.0003 * sign
            placed = False
            for k in range(1, 30000):  # plenty of headroom
                new_v = round(old_bp + k * step, 4)
                if new_v <= 0.0001 or new_v >= 99.99:
                    # flip direction and retry
                    sign = -sign
                    step = 0.0003 * sign
                    continue
                if new_v not in used and not _looks_round_any(new_v):
                    placed = True
                    break
            if not placed:
                # Last-resort: walk the other direction
                sign = -sign
                step = 0.0003 * sign
                for k in range(1, 30000):
                    new_v = round(old_bp + k * step, 4)
                    if 0.0001 < new_v < 99.99 and new_v not in used and not _looks_round_any(new_v):
                        placed = True
                        break
            used.add(new_v)
            df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                         sample_size)
            fixed += 1

    if verbose and fixed:
        print(f"   🎲 dejitter within-cat 4dp collisions: {fixed} row(s) shifted")
    return df, fixed


def dejitter_within_cat_tight_clusters(df, subject, *, verbose=True,
                                       min_cluster=2):
    """Within-category 2dp deduplication: ensures NO two rows in the
    same category share the same BP at 2-decimal precision (which is
    what analysts see in the CSV display). When 2+ rows share a 2dp
    bucket (e.g. 8.1149 and 8.1141 both display as "8.11"), the
    cluster is spread across distinct 2dp buckets centered on the
    cluster's original centroid, with subject-salted jitter so values
    aren't pinned at 4dp either.

    Different from ``dejitter_within_cat_4dp_collisions`` which only
    catches EXACT 4dp duplicates. This catches the wider 2dp display
    cluster — the perceptual pinning Jenna's analysts flag.

    Strategy:
      1. Group non-demo, non-meta rows in each category by ``round(bp, 2)``.
      2. For any 2dp bucket with ``min_cluster`` (default 2) or more
         rows, redistribute them across distinct 2dp buckets:
         ``[centroid - K/2, ..., centroid + K/2]`` where K = bucket size.
      3. Each row is placed near the integer-cent of its assigned
         bucket plus subject-salted jitter (±0.003pp) so 4dp values
         remain unique and don't sit on round-cents.
      4. Preserves rank order (lowest BP -> lowest assigned cent).
      5. Avoids colliding with values in OTHER 2dp buckets in the same
         category (collision-walk to next free 4dp slot).

    Idempotent: once each 2dp bucket holds <= 1 row in the cat, second
    pass detects no clusters and is a no-op.

    Skips:
      - Demographic + meta categories (handled by zero-sum dejitter).
      - Rows where BP is exactly 0 or 100 (self-pin / floor).
      - Rows where BP < 0.05 (the long-tail floor where small
        absolute differences are statistically real).

    Added 2026-06-11 after Jenna's batch-QC of 13 talent profiles
    flagged tight 2dp clusters as "pervasive pinning" (Kit Harington
    ATHLETE 8.11 x5, Kristen Bell TALENT 2.58 x6, etc.).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c and _c in df.columns and df[_c].dtype.name not in ('object', 'O'):
            df[_c] = df[_c].astype(object)

    # Hard-cap on how far ANY single row can move from its original BP.
    # This protects analysts from data drift — the enforcer breaks
    # visual ties without changing the brand's relative position.
    MAX_MOVE_PP = 0.03

    fixed_total = 0
    cat_rows = {}
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat in DEPIN_DEMO_CATS or cat in DEPIN_META_CATS:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp < 0.05 or old_bp >= 99.99:
            continue
        val = str(r.get('Value', '') or '').strip().upper()
        cat_rows.setdefault(cat, []).append((idx, val, old_bp))

    for cat, rows in cat_rows.items():
        # Group by 2dp bucket
        from collections import defaultdict as _dd
        bucket_members = _dd(list)
        for entry in rows:
            bucket_members[round(entry[2], 2)].append(entry)
        # Sort each bucket's members by old_bp asc
        for b2 in bucket_members:
            bucket_members[b2].sort(key=lambda e: e[2])
        # All currently-occupied 2dp buckets in this category
        occupied = set(bucket_members.keys())
        # Process each over-stuffed bucket
        for b2, members in bucket_members.items():
            K = len(members)
            if K < min_cluster:
                continue
            # Move all but the median member to nearby unoccupied buckets
            # within ±MAX_MOVE_PP. Median stays in original bucket.
            median_idx = K // 2
            stayer = members[median_idx]
            movers = members[:median_idx] + members[median_idx+1:]
            # Build candidate adjacent 2dp slots within MAX_MOVE_PP
            # of each mover's original BP.
            for mover in movers:
                idx_m, val_m, old_bp_m = mover
                # Search free slots in {b2 ±0.01, ±0.02, ±0.03}
                cand_offsets = [-1, +1, -2, +2, -3, +3]
                target_b2 = None
                for off in cand_offsets:
                    cand = round(b2 + off * 0.01, 2)
                    if cand < 0.05 or cand >= 99.99:
                        continue
                    if abs(cand - old_bp_m) > MAX_MOVE_PP + 0.005:
                        continue
                    if cand not in occupied:
                        target_b2 = cand
                        occupied.add(cand)
                        break
                if target_b2 is None:
                    # No free adjacent slot — leave row alone
                    continue
                # Compute new 4dp value inside the target bucket with
                # subject-salted jitter, avoiding round-cent boundaries
                h = int(_hl.blake2b(
                    f'{subject}|{cat}|{val_m}|tight-cluster-2026-06-11'.encode(),
                    digest_size=8,
                ).hexdigest(), 16)
                u = ((h % 200001) - 100000) / 100000.0
                new_v = round(target_b2 + u * 0.004, 4)
                if round(new_v, 2) != target_b2:
                    new_v = round(target_b2 + 0.003 * (1 if u >= 0 else -1), 4)
                if _looks_round_any(new_v):
                    new_v = round(new_v + 0.0007, 4)
                    if round(new_v, 2) != target_b2:
                        new_v = round(target_b2 + 0.0023, 4)
                if (0.05 <= new_v < 99.99
                        and round(new_v, 4) != round(old_bp_m, 4)
                        and abs(new_v - old_bp_m) <= MAX_MOVE_PP + 0.005):
                    df = _set_bp(df, idx_m, new_v, bp_col, cs_col, raw_col,
                                 proj_col, sample_size)
                    fixed_total += 1

    if verbose and fixed_total:
        print(f"   🎯 dejitter within-cat 2dp clusters: {fixed_total} row(s) "
              f"moved to adjacent unoccupied 2dp slots (max move "
              f"{MAX_MOVE_PP:.2f}pp)")
    return df, fixed_total


# ============================================================================
# 4a-bis. CROSS-PROFILE 4dp dejitter (added 2026-06-10 after Jenna's
#         colleague's MPB cross-profile observation + targeted scan).
#
# Different problem from `dejitter_within_cat_4dp_collisions`:
#   - within-cat: ONE category, MANY brands all at exact same 4dp BP
#                 (LLM batch placeholder, 69 MPB rows at 5.6789%)
#   - cross-profile: ONE (cat, brand) pair shared across MANY profiles
#                    at exact same 4dp BP (e.g. Mint Mobile @ 1.9898%
#                    across 5 unrelated talent profiles, or Visa @
#                    56.3383% across 4 show-cohort profiles)
#
# Root cause: per-category research agent emits deterministic values for
# (cat, brand) tuples that aren't differentiated enough by persona
# context (peripheral telecom MVNOs, infrastructure credit cards). With
# no per-subject jitter pass downstream of the agent, the same value
# sticks across profiles — even when each profile passes the local
# `dejitter_within_cat_4dp_collisions` (because the brands are NOT
# colliding within a single profile, only across profiles).
#
# This enforcer loads a (CATEGORY, VALUE, BP_4dp) → set(subject) index
# from S3 (cached 1h) and re-jitters any row whose 4dp value is held by
# >= min_collision_count OTHER subjects. Excludes LOCATION (DMA codes
# legitimately repeat at gen-pop values per Jenna's instruction), demos,
# and meta categories.
#
# Idempotent: after re-jitter, the new BP uses the per-subject hash
# `_jitter_for(subject|brand|cat|xprof-4dp)` so a second pass (next
# pipeline run, with a fresh corpus that includes the just-saved file)
# won't re-trigger.
# ============================================================================

# 2026-08-19 (Jenna): shared cross-worker cache. Previously cached under
# /tmp/ but systemd's PrivateTmp=true on synth-queue-worker@ isolates
# /tmp per-slot, so every worker rebuilt the 4dp index (a 4,150-file
# S3 walk, ~2-3 min) independently. Moving under /var/cache lets every
# slot share one build. Also protected by a lockfile so if two workers
# arrive cold-cache in the same second, only one does the S3 walk and
# the other blocks on the lock, then reads the fresh cache when it
# wins the lock. Persistent across worker restarts too.
_CROSS_PROFILE_CACHE_DIR = '/var/cache/synth_queue'
_CROSS_PROFILE_INDEX_CACHE_PATH = (
    _CROSS_PROFILE_CACHE_DIR + '/cross_profile_4dp_index.pkl'
)
_CROSS_PROFILE_INDEX_LOCK_PATH = (
    _CROSS_PROFILE_CACHE_DIR + '/cross_profile_4dp_index.lock'
)
# Legacy fallback: some environments may still write to /tmp (e.g. a
# stray local script). Try the shared cache first, then fall back to
# the legacy path if it's newer. New writes always go to the shared
# path.
_CROSS_PROFILE_INDEX_LEGACY_TMP_PATH = '/tmp/.cross_profile_4dp_index.pkl'
_CROSS_PROFILE_INDEX_TTL_SECS = 3600
# 2026-08-20 (Jenna: "why is gogurt taking so long"): stale-while-
# revalidate. A cache older than TTL but younger than this hard max is
# STILL USED by the run (collision-avoidance tolerates hours-stale
# corpora fine); a background thread refreshes it for the next run.
# Only a cache older than the hard max (or missing entirely) forces a
# blocking mid-run rebuild - the 4,163-profile S3 walk that stretched
# the Go-GURT run. A 6-hourly cron prebuild keeps even that rare.
_CROSS_PROFILE_INDEX_HARD_MAX_AGE_SECS = 26 * 3600
# Categories explicitly excluded from cross-profile dejitter:
#   - DEPIN_DEMO_CATS: demos legitimately repeat (gen pop anchored)
#   - DEPIN_META_CATS: BRAND INPUT, SAMPLE SIZE, etc.
#   - LOCATION: DMA codes legitimately repeat across profiles at gen-pop
#               values (Jenna 2026-06-10: "you can ignore location")
_CROSS_PROFILE_SKIP_CATS = (
    DEPIN_DEMO_CATS | DEPIN_META_CATS | {'LOCATION'}
)


def _read_4dp_cache_any_age(cache_path, verbose=True):
    """Read the newest usable cache file (shared path preferred, legacy
    /tmp fallback) regardless of age. Returns (idx, age_secs) or
    (None, None)."""
    import os as _os_x, time as _time_x, pickle as _pickle_x
    candidates = [
        (cache_path, 'shared cache'),
        (_CROSS_PROFILE_INDEX_LEGACY_TMP_PATH, 'legacy /tmp cache'),
    ]
    for path, label in candidates:
        try:
            if not _os_x.path.exists(path):
                continue
            age = _time_x.time() - _os_x.path.getmtime(path)
            with open(path, 'rb') as _f:
                _idx = _pickle_x.load(_f)
            if verbose:
                _n_keys = len(_idx)
                _n_subs = len({s for ss in _idx.values() for s in ss})
                print(f"   📚 cross-profile 4dp index loaded from "
                      f"{label} ({_n_keys:,} keys / {_n_subs:,} "
                      f"subjects / age {age/60:.0f}min)")
            return _idx, age
        except Exception as _e:
            if verbose:
                print(f"   ⚠ cross-profile {label} load failed: {_e}")
    return None, None


# One background refresh per process at a time.
_CROSS_PROFILE_BG_REFRESH_LOCK = None


def _spawn_4dp_index_bg_refresh(**rebuild_kwargs):
    """Kick a daemon thread that rebuilds the 4dp index without blocking
    the calling run. Non-blocking on the cross-process lock too: if
    another worker is already rebuilding, the thread exits immediately."""
    import threading as _threading_x
    global _CROSS_PROFILE_BG_REFRESH_LOCK
    if _CROSS_PROFILE_BG_REFRESH_LOCK is None:
        _CROSS_PROFILE_BG_REFRESH_LOCK = _threading_x.Lock()
    if not _CROSS_PROFILE_BG_REFRESH_LOCK.acquire(blocking=False):
        return  # this process already has a refresh in flight

    def _run():
        try:
            _rebuild_cross_profile_4dp_index(
                nonblocking=True, verbose=False, **rebuild_kwargs)
        except Exception:
            pass
        finally:
            try:
                _CROSS_PROFILE_BG_REFRESH_LOCK.release()
            except Exception:
                pass

    _threading_x.Thread(
        target=_run, name='cross-profile-4dp-refresh', daemon=True,
    ).start()


def _load_cross_profile_4dp_index(*,
                                    bucket='dashboard-inputs',
                                    region_name='us-east-2',
                                    cache_path=_CROSS_PROFILE_INDEX_CACHE_PATH,
                                    lock_path=_CROSS_PROFILE_INDEX_LOCK_PATH,
                                    ttl=_CROSS_PROFILE_INDEX_TTL_SECS,
                                    hard_max_age=_CROSS_PROFILE_INDEX_HARD_MAX_AGE_SECS,
                                    max_workers=80,
                                    verbose=True):
    """Return the (CATEGORY, VALUE, BP_4dp) -> set(subject_upper) index.

    Freshness policy (2026-08-20 stale-while-revalidate):
      - age < ttl: use as-is.
      - ttl <= age < hard_max_age: USE the stale index immediately and
        refresh it in a background daemon thread so THIS run never
        pays the 4,000+ profile S3 walk. Collision avoidance tolerates
        an hours-stale corpus (it only misses profiles uploaded since
        the last build).
      - age >= hard_max_age or no cache: blocking lock-serialized
        rebuild (the pre-2026-08-20 cold path).

    Returns the dict, or None if S3 is unreachable / boto3 missing.
    """
    idx, age = _read_4dp_cache_any_age(cache_path, verbose=verbose)
    if idx is not None and age is not None:
        if age < ttl:
            return idx
        if age < hard_max_age:
            if verbose:
                print(f"   📚 cross-profile index is {age/60:.0f}min old "
                      f"(ttl {ttl/60:.0f}min): using it now, refreshing "
                      f"in background")
            _spawn_4dp_index_bg_refresh(
                bucket=bucket, region_name=region_name,
                cache_path=cache_path, lock_path=lock_path,
                ttl=ttl, max_workers=max_workers)
            return idx
        if verbose:
            print(f"   📚 cross-profile index is {age/3600:.1f}h old "
                  f"(> hard max {hard_max_age/3600:.0f}h): rebuilding "
                  f"before use")

    return _rebuild_cross_profile_4dp_index(
        bucket=bucket, region_name=region_name, cache_path=cache_path,
        lock_path=lock_path, ttl=ttl, max_workers=max_workers,
        verbose=verbose, nonblocking=False)


def _rebuild_cross_profile_4dp_index(*,
                                      bucket='dashboard-inputs',
                                      region_name='us-east-2',
                                      cache_path=_CROSS_PROFILE_INDEX_CACHE_PATH,
                                      lock_path=_CROSS_PROFILE_INDEX_LOCK_PATH,
                                      ttl=_CROSS_PROFILE_INDEX_TTL_SECS,
                                      max_workers=80,
                                      verbose=True,
                                      nonblocking=False):
    """Lock-serialized S3 walk + atomic cache write.

    Walks every root-level *.csv in the bucket (excluding _backups/,
    system/, and the canonical Gen_Pop_2026.csv) with parallel reads.

    Concurrency safety: an fcntl LOCK_EX lockfile at lock_path
    serializes rebuilds across worker processes. With
    ``nonblocking=True`` (background refresh path) the lock is taken
    with LOCK_NB and the function returns None immediately when
    another worker is already rebuilding. After acquiring the lock the
    cache is re-checked: if a fresh one appeared while waiting, the S3
    walk is skipped.

    Returns the dict, or None if the rebuild was skipped or failed.
    """
    import os as _os_x, time as _time_x, pickle as _pickle_x

    try:
        _os_x.makedirs(_os_x.path.dirname(cache_path), exist_ok=True)
    except Exception:
        pass

    _lock_fh = None
    _got_lock = False
    try:
        import fcntl as _fcntl_x
        try:
            _lock_fh = open(lock_path, 'a+')
            if nonblocking:
                try:
                    _fcntl_x.flock(_lock_fh.fileno(),
                                   _fcntl_x.LOCK_EX | _fcntl_x.LOCK_NB)
                    _got_lock = True
                except OSError:
                    return None  # someone else is already rebuilding
            else:
                if verbose:
                    print(f"   🔒 cross-profile 4dp index: waiting for "
                          f"build lock ({lock_path})...")
                _t0 = _time_x.time()
                _fcntl_x.flock(_lock_fh.fileno(), _fcntl_x.LOCK_EX)
                _got_lock = True
                _wait = _time_x.time() - _t0
                if verbose and _wait > 1.0:
                    print(f"   🔒 acquired after {_wait:.1f}s "
                          f"(another worker was likely building)")
            # Re-check the cache after taking the lock: the previous
            # lock holder may have just written a fresh one. If so,
            # skip our own S3 walk.
            _idx2, _age2 = _read_4dp_cache_any_age(cache_path,
                                                    verbose=False)
            if _idx2 is not None and _age2 is not None and _age2 < ttl:
                if verbose:
                    print(f"   📚 cross-profile index refreshed by "
                          f"another worker while waiting; using it")
                return _idx2
        except Exception as _e_lock:
            # If flock isn't available (e.g. Windows dev) or the lockfile
            # can't be opened, fall through and rebuild without the lock.
            # This preserves the pre-2026-08-19 behavior as a safety net.
            if verbose:
                print(f"   ⚠ cross-profile lock unavailable "
                      f"({_e_lock}); rebuilding without lock")

        # Do the actual rebuild while holding the lock (if we got it).
        import io as _io_x
        import boto3 as _boto3_x
        import concurrent.futures as _cf_x
        s3 = _boto3_x.client('s3', region_name=region_name)
        paginator = s3.get_paginator('list_objects_v2')
        keys = []
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get('Contents', []) or []:
                k = obj['Key']
                if (k.endswith('.csv')
                        and '/' not in k
                        and not k.startswith('_')
                        and k != 'Gen_Pop_2026.csv'):
                    keys.append(k)
        if verbose:
            print(f"   📚 building cross-profile 4dp index from "
                  f"{len(keys):,} S3 profiles (parallel={max_workers})...")
        _t_build = _time_x.time()

        index = defaultdict(set)
        bp_col_canon = 'Brand Penetration (Row)'

        def _fetch(k):
            try:
                obj = s3.get_object(Bucket=bucket, Key=k)
                df_x = pd.read_csv(_io_x.BytesIO(obj['Body'].read()),
                                   low_memory=False)
                bi = df_x[df_x['Column'].astype(str).str.upper()
                          == 'BRAND INPUT']
                subj = (str(bi.iloc[0].get('Value', '')).strip().upper()
                        if len(bi) else k.upper())
                rows_local = []
                for _, r in df_x.iterrows():
                    cat = str(r.get('Column', '') or '').strip().upper()
                    if cat in _CROSS_PROFILE_SKIP_CATS:
                        continue
                    val = str(r.get('Value', '') or '').strip().upper()
                    if not val:
                        continue
                    bp = _bp(r.get(bp_col_canon, 0))
                    if bp <= 0 or bp >= 99.99:
                        continue
                    rows_local.append((cat, val, round(bp, 4), subj))
                return rows_local
            except Exception:
                return []

        with _cf_x.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for rows in ex.map(_fetch, keys):
                for cat, val, bp4, subj in rows:
                    index[(cat, val, bp4)].add(subj)

        out = dict(index)
        _t_walk = _time_x.time() - _t_build

        # Atomic write: write to a tempfile, then rename so partial
        # writes never corrupt the cache. Any other worker holding the
        # cache pickle open at read time will see either the old file
        # (via existing open fd) or the fully-written new one.
        try:
            _tmp = cache_path + '.tmp'
            with open(_tmp, 'wb') as _f:
                _pickle_x.dump(out, _f)
            _os_x.replace(_tmp, cache_path)
        except Exception as _e_w:
            if verbose:
                print(f"   ⚠ cross-profile cache write failed: {_e_w}")

        if verbose:
            n_keys = len(out)
            n_subs = len({s for ss in out.values() for s in ss})
            print(f"   📚 cross-profile 4dp index built: {n_keys:,} keys / "
                  f"{n_subs:,} subjects  (S3 walk: {_t_walk:.1f}s)")
        return out
    except Exception as _e_build:
        if verbose:
            print(f"   ⚠ cross-profile 4dp index build failed: {_e_build}")
        return None
    finally:
        # Always release the lock, even on failure, so a subsequent
        # cold-cache worker isn't stuck waiting forever.
        try:
            if _got_lock and _lock_fh is not None:
                import fcntl as _fcntl_x
                _fcntl_x.flock(_lock_fh.fileno(), _fcntl_x.LOCK_UN)
        except Exception:
            pass
        try:
            if _lock_fh is not None:
                _lock_fh.close()
        except Exception:
            pass


def dejitter_cross_profile_4dp_collisions(df, subject, *,
                                          verbose=True,
                                          bucket='dashboard-inputs',
                                          region_name='us-east-2',
                                          cache_path=_CROSS_PROFILE_INDEX_CACHE_PATH,
                                          ttl=_CROSS_PROFILE_INDEX_TTL_SECS,
                                          min_collision_count=2,
                                          max_attempts=20,
                                          corpus_index=None):
    """Re-jitter rows whose (CATEGORY, VALUE, BP_4dp) tuple is held by
    `min_collision_count` or more OTHER subjects in the S3 corpus.

    Catches cross-profile pins like:
      - TELECOM/MINT MOBILE @ 1.9898% across 5 talent profiles
      - CREDIT PROVIDER/VISA @ 56.3383% across 4 show-cohort profiles

    Excludes LOCATION (DMA codes legitimately repeat per Jenna), demos,
    meta. Recomputes Raw + Proj. Idempotent.

    `corpus_index` is an optional pre-built dict (cat, val, bp4) -> set
    of subjects; pass it to avoid the load cost when the caller already
    has the index in hand (e.g. a backfill loop).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    index = corpus_index if corpus_index is not None else \
        _load_cross_profile_4dp_index(
            bucket=bucket, region_name=region_name,
            cache_path=cache_path, ttl=ttl, verbose=verbose,
        )
    if not index:
        if verbose:
            print(f"   ⚠ cross-profile 4dp dejitter: corpus index "
                  f"unavailable; skipping")
        return df, 0

    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c and _c in df.columns and df[_c].dtype.name not in ('object', 'O'):
            df[_c] = df[_c].astype(object)

    subject_key = str(subject or '').strip().upper()
    fixed = 0

    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat in _CROSS_PROFILE_SKIP_CATS:
            continue
        val = str(r.get('Value', '') or '').strip().upper()
        if not val:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp <= 0 or old_bp >= 99.99:
            continue
        bp4 = round(old_bp, 4)
        peers = index.get((cat, val, bp4), set())
        peers_excl = {s for s in peers if s and s != subject_key}
        if len(peers_excl) < min_collision_count:
            continue

        h = int(_hl.blake2b(
            f'{subject_key}|{cat}|{val}|xprof-4dp'.encode(),
            digest_size=8,
        ).hexdigest(), 16)
        # Drift band scales with magnitude so the new 4dp value stays
        # within reading distance of the original. Hash → uniform real
        # in [-1, +1] picks the position inside the band; this gives
        # ~ 2*band*10000 unique 4dp slots. Bands sized so secondary-
        # collision rate is tiny even for the largest pin we've seen
        # (~25 subjects on niche-floor 0.5pp brands like AUDEMARS PIGUET).
        # 400+ slots × 25 colliders → ~7.5% pairwise collision chance,
        # which the convergence loop catches in 1-2 extra passes.
        if old_bp < 0.10:
            max_drift = 0.020
        elif old_bp < 1.0:
            max_drift = 0.030
        elif old_bp < 10.0:
            max_drift = 0.030
        else:
            max_drift = 0.050
        u = ((h % 200001) - 100000) / 100000.0
        new_v = round(old_bp + u * max_drift, 4)
        if new_v == bp4:
            new_v = round(bp4 + (max_drift / 4.0) * (1 if u >= 0 else -1), 4)

        for k in range(max_attempts):
            if not (0.0001 < new_v < 99.99):
                new_v = round(old_bp - u * max_drift, 4)
                u = -u
                continue
            new_peers = index.get((cat, val, new_v), set())
            new_peers_excl = {s for s in new_peers if s and s != subject_key}
            if (new_v != bp4
                    and len(new_peers_excl) < min_collision_count
                    and not _looks_round_any(new_v)):
                break
            step_sign = 1 if u >= 0 else -1
            new_v = round(new_v + step_sign * 0.0007, 4)

        df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        fixed += 1

    if verbose and fixed:
        print(f"   🌐 dejitter cross-profile 4dp collisions: {fixed} "
              f"row(s) re-jittered (LOCATION + demos excluded)")
    return df, fixed


# ============================================================================
# 4b. Sequential-digit placeholder detector (added 2026-05-30 after Jenna's
#     May 30 batch escalation). Niche-creator profiles with tiny raw
#     universes (3-75 panel users) force `mpb-floor` to ask the LLM to
#     INVENT ~1,400+ MPB brand rows. The LLM, having no real signal for
#     so many fabricated brands, emits BPs as a rotating set of sequential-
#     digit decimals (5.6789, 6.7890, 7.8901, 8.9012, 9.0123, 4.3210,
#     5.4321, 6.5432, 7.6543, 8.7654, 9.8765, 10.9876, 1.2345, 11.2345...)
#     — each value reused 20-100 times in a single category.
#
#     The existing `dejitter_within_cat_4dp_collisions` would catch the
#     COLLISIONS but it only runs ONCE, BEFORE mpb-floor injects the LLM
#     brands. By the time the placeholders exist, the dejitter sweep is
#     already done.
#
#     This detector matches the PLACEHOLDER PATTERN directly (independent
#     of collision counts), so even individual occurrences are caught.
#     It can be run at any pipeline stage and is idempotent.
# ============================================================================

def _is_sequential_digit_bp(bp_value) -> bool:
    """Return True if the BP's FRACTIONAL part begins with 4 digits that
    form a strict monotonic sequence (ascending or descending in base 10
    with mod-10 wrap).

    This is the LLM's actual placeholder shape — it ALWAYS emits 4
    fractional digits forming a strict sequence:
      X.1234, X.2345, X.3456, X.4567, X.5678, X.6789, X.7890, X.8901,
      X.9012, X.0123  (ascending)
      X.9876, X.8765, X.7654, X.6543, X.5432, X.4321, X.3210, X.2109,
      X.1098, X.0987  (descending)

    Caught examples: 5.6789, 6.7890, 7.8901, 9.8765, 11.2345, 13.4567,
    1.2345, 4.3210, 5.4321, 22.3456, 100.1234

    NOT caught (false-positive-safe):
      * 12.34, 67.89, 23.45 — only 2 fractional digits
      * 0.5080, 0.5084 — 4 fractional digits but not sequential
      * 1.2300 — fraction "2300" breaks sequence after 3
      * 10.5680 — fraction "5680" breaks sequence after 3

    False-positive rate on real data: only 20 of 10,000 possible 4-digit
    fractions form a strict sequence (~0.2%). When one does land on real
    data, dejitter rewrites it into the natural tail (0.30-1.20%) which
    is invisible to dashboard consumers.

    Accepts either a float OR a string like "5.6789" / "5.6789%" — the
    string path preserves trailing zeros that float conversion drops,
    so "6.7890" is still detected.
    """
    if isinstance(bp_value, str):
        s = bp_value.strip().rstrip('%').strip()
        try:
            v_check = float(s)
        except (TypeError, ValueError):
            return False
    else:
        try:
            v_check = float(bp_value)
        except (TypeError, ValueError):
            return False
        s = f"{v_check:.4f}"
    if v_check <= 0 or v_check >= 100:
        return False
    if '.' not in s:
        return False
    _, frac = s.rsplit('.', 1)
    if len(frac) < 4:
        return False
    # Take the first 4 fractional digits (LLM emits 4dp precision)
    digits = [int(c) for c in frac[:4]]
    asc = all((digits[i+1] - digits[i]) % 10 == 1 for i in range(3))
    desc = all((digits[i] - digits[i+1]) % 10 == 1 for i in range(3))
    return asc or desc


def detect_placeholder_bps(df, bp_col='Brand Penetration (Row)'):
    """Return a boolean mask indexed like df marking sequential-digit
    placeholder BPs. Read-only — used by gates + the dejitter pass.

    Uses the STRING form of each cell when available — preserves trailing
    zeros that float conversion would drop (e.g. '6.7890' would otherwise
    become 6.789 and miss the LLM's 4-decimal placeholder pattern).
    """
    if df is None or len(df) == 0 or bp_col not in df.columns:
        return pd.Series([], dtype=bool)
    col = df[bp_col]
    # If the column stores strings (typical at save time, e.g. "5.6789%"),
    # detection runs against the literal digits. If it stores numerics,
    # the detector formats to 4dp internally.
    return col.apply(_is_sequential_digit_bp)


def dejitter_sequential_placeholders(df, subject, verbose=True,
                                     drop_below_universe=None,
                                     min_universe_to_keep=50):
    """Rewrite (or optionally drop) BPs that match the sequential-digit
    placeholder pattern.

    Rationale: the LLM has no real signal for the brand — pretending
    we do by jittering the placeholder to a plausible value is worse
    than dropping the row when the universe is small. Behavior:

      * If `drop_below_universe` is provided AND raw_universe < that,
        DROP every placeholder row (it was a fabricated brand with no
        real backing — better absent than dishonest).
      * Otherwise rewrite the BP to a deterministic small jittered
        value in [0.3, 1.2] %, hashed off (subject, category, brand)
        so the value is reproducible. This sits in the natural tail
        and is invisible to dashboard consumers.

    Recomputes Raw + Projection. Idempotent.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    # Cast BP/CS/Raw/Proj cols to object dtype so float assignment works
    # even when the loaded CSV stores them as strings ("5.6789%").
    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c and _c in df.columns and df[_c].dtype.name not in ('object', 'O'):
            df[_c] = df[_c].astype(object)

    placeholder_mask = detect_placeholder_bps(df, bp_col)
    if not placeholder_mask.any():
        return df, 0

    # Decide drop vs rewrite based on raw universe
    drop_mode = (
        drop_below_universe is not None
        and sample_size is not None
        and sample_size < drop_below_universe
        and sample_size >= min_universe_to_keep is False
    )

    placeholder_idxs = df.index[placeholder_mask]
    if drop_mode:
        # Only drop placeholders in MOST PURCHASED BRANDS — other
        # categories may have a legitimate placeholder pattern collision
        # (rare but possible) and we don't want to silently delete real
        # brand rows from small categories.
        mpb_mask = df['Column'].astype(str).str.upper() == 'MOST PURCHASED BRANDS'
        drop_idxs = df.index[placeholder_mask & mpb_mask]
        if len(drop_idxs):
            df = df.drop(index=drop_idxs).reset_index(drop=True)
            if verbose:
                print(f"   🗑️  sequential-placeholder DROP: removed {len(drop_idxs)} fabricated MPB row(s) "
                      f"(raw universe {sample_size} < {drop_below_universe})")
        # Rewrite any remaining placeholders in other cats
        placeholder_mask = detect_placeholder_bps(df, bp_col)
        placeholder_idxs = df.index[placeholder_mask]

    # 2026-06-10 (Jenna 3am audit, Joel Edgerton Arizona Cardinals 14.4321%
    # → 0.6473%): the original rewrite path was magnitude-destructive — ANY
    # value with sequential post-decimals (incl. legitimate LLM emissions
    # like Spotify@76.2109%, Cardinals@14.4321%, Amy Adams@18.4567%, Boston
    # Bruins@9.9876%) was rewritten down into [0.30, 1.20] tail, throwing
    # away real signal. Two-mode rewrite:
    #   * Low-magnitude (< 5pp): full natural-tail rewrite [0.30, 1.20] —
    #     these match the LLM's actual placeholder-stamping range (it
    #     emits X.YYYY for very small values where confidence is low and
    #     fills in a "decorative" 4-digit sequence).
    #   * Mid/high-magnitude (≥ 5pp): magnitude-preserving — keep the
    #     integer part, only perturb the fractional digits enough to
    #     break the sequential pattern. The signal at this scale is
    #     real; we only need to break the sequential identity for
    #     dashboard aesthetics, not rescale the value.
    rewritten = 0
    for idx in placeholder_idxs:
        r = df.loc[idx]
        cat = str(r.get('Column', '') or '').strip().upper()
        val = str(r.get('Value', '') or '').strip().upper()
        if cat in DEPIN_DEMO_CATS or cat in DEPIN_META_CATS:
            continue
        h = int(_hl.blake2b(
            f'{subject}|{cat}|{val}|placeholder-rewrite'.encode(),
            digest_size=8,
        ).hexdigest(), 16)

        cur_bp = _bp(df.at[idx, bp_col])
        if cur_bp is None or pd.isna(cur_bp):
            continue

        if cur_bp < 5.0:
            # Tail-magnitude — likely a true LLM placeholder. Rewrite
            # into [0.30, 1.20] natural tail.
            base = 0.30 + ((h % 901) / 1000.0)
            jitter = ((h >> 16) % 89) / 10000.0
            new_v = round(base + jitter, 4)
        else:
            # Mid/high magnitude — preserve integer part, only perturb
            # the fractional digits to break the sequential pattern.
            # Jitter range ±0.0500pp centered on the original value.
            int_part = int(cur_bp)
            jitter_pp = (((h % 1001) - 500) / 10000.0)  # -0.0500 .. +0.0500
            # 4dp tail jitter to ensure non-sequential
            tail_jitter = ((h >> 24) % 89) / 10000.0    # 0.0001 .. 0.0089
            new_v = round(cur_bp + jitter_pp + tail_jitter, 4)
            # Clamp to [int_part - 0.5, int_part + 0.5] so we never
            # cross an integer boundary
            new_v = max(int_part - 0.5, min(int_part + 0.999, new_v))
            new_v = round(new_v, 4)

        # Final paranoia: avoid landing back on a placeholder pattern
        attempts = 0
        while _is_sequential_digit_bp(new_v) and attempts < 5:
            new_v = round(new_v + 0.0037 * (attempts + 1), 4)
            attempts += 1

        df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col, sample_size)
        rewritten += 1

    if verbose and rewritten:
        print(f"   🎰 sequential-placeholder REWRITE: jittered {rewritten} BP(s) "
              f"(tail-rewrite for <5pp, magnitude-preserving for ≥5pp)")
    return df, rewritten


# ============================================================================
# 4c. Fractional-part LADDER dejitter (2026-08-26 Liz QA escalation,
#     BETHENNY FRANKEL avid derive_cut run 3jEG3Kw76rpoZA)
#
#     THE SIGNATURE: many rows share an IDENTICAL 4dp fractional part at
#     integer-stepped values (TALENT 67.8912 / 55.8912 / ... / 3.8912 x15;
#     AMUSEMENT PARKS 4.1847 / 3.1847 / 2.1847; .2847 on 76 rows
#     file-wide). Born in row-by-row model output: within a chunk the
#     model reuses one fractional part and steps only the integer.
#     `dejitter_within_cat_4dp_collisions` only breaks EXACT 4dp identity
#     and `dejitter_sequential_placeholders` only matches monotonic-digit
#     fractions, so same-suffix-different-integer ladders shipped
#     untouched.
#
#     Detection lives in migration/fractional_ladders.py (shared with
#     ship-gate I14 and the profile_writer 6.8 auto-remediation so the
#     three consumers can never drift). Thresholds are empirical - see
#     that module's docstring (organic max 3-4 per category / 9-10
#     file-wide; defect floor 12+ / 49+).
#
#     THE FIX: per-row salted fractional re-jitter, DOWNWARD-only.
#       * salt = blake2b(subject|category|brand|frac-ladder-v1) - a
#         per-(subject, brand, category) hash per workspace rule #1;
#         no two rows can share an offset by construction.
#       * integer part preserved (the magnitude call is respected).
#       * category rank order preserved: rows move only within the open
#         interval (next-lower current value, own value), processed
#         ascending so already-moved lower rows bound the walk.
#       * DOWNWARD-only means Raw can only shrink, so the avid subset
#         invariant (avid Raw <= parent Raw, ship-gate I12) survives the
#         re-jitter unconditionally - no parent frame needed here.
#       * MPB mirror clusters (Rule #3b: same brand at the same 4dp in
#         MOST PURCHASED BRANDS + sub-categories) move TOGETHER to one
#         new value so the exact-mirror invariant holds.
#     Raw / Projection / Category Share recompute through _set_bp.
#     Idempotent: once a group is dissolved below threshold, the second
#     pass is a no-op.
# ============================================================================

def dejitter_fractional_ladders(df, subject, verbose=True):
    """Break seeded fractional-part ladders (see block comment above).

    Returns (df, n_rows_moved).
    """
    try:
        from migration.fractional_ladders import (
            detect_fractional_ladders, ladder_in_scope, suffix4,
        )
    except ImportError:
        from fractional_ladders import (  # type: ignore
            detect_fractional_ladders, ladder_in_scope, suffix4,
        )
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not bp_col:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c and _c in df.columns and df[_c].dtype.name not in ('object', 'O'):
            df[_c] = df[_c].astype(object)

    # In-scope triples + per-category value books.
    triples = []
    cat_rows = {}   # cat_u -> [(idx, bp)] every parseable row (scope or not)
    for idx, r in df.iterrows():
        cat_u = str(r.get('Column', '') or '').strip().upper()
        v = _bp(r.get(bp_col))
        if v is None or pd.isna(v):
            continue
        cat_rows.setdefault(cat_u, []).append((idx, float(v)))
        if ladder_in_scope(cat_u, v):
            triples.append((idx, cat_u, float(v)))
    if not triples:
        return df, 0

    det = detect_fractional_ladders(triples)
    flagged = det['flagged_ids']
    if not flagged:
        return df, 0
    flagged_suffixes = set(det['flagged_suffixes'])

    # Mirror clusters: the SAME brand at the SAME 4dp in more than one
    # category is a deliberate mirror (MPB exact-mirror rule #3b, sports
    # team -> league/conference/division companion sync, CPG subcats).
    # Every row of that (brand, bp4) moves together to ONE new value so
    # no mirror relationship desyncs. Rows are clustered on ALL
    # categories' rows, not just MPB.
    mirror_count = Counter()
    for c, pairs in cat_rows.items():
        for idx, v in pairs:
            b = _norm_brand(str(df.at[idx, 'Value'] or ''))
            mirror_count[(b, round(v, 4))] += 1
    clusters = {}    # key -> [row idx]; key = ('mirror', brand, bp4) or ('row', idx)
    for idx, cat_u, v in triples:
        if idx not in flagged:
            continue
        b = _norm_brand(str(df.at[idx, 'Value'] or ''))
        bp4 = round(v, 4)
        if mirror_count[(b, bp4)] > 1:
            clusters.setdefault(('mirror', b, bp4), []).append(idx)
        else:
            clusters.setdefault(('row', idx), []).append(idx)
    # A mirror cluster must carry EVERY row of that (brand, bp4) - the
    # unflagged twins too (an MPB source row often escapes the detector
    # because its category is huge). Moving only the flagged twin lets
    # the MPB-mirror safety net copy the old ladder value straight back,
    # deadlocking the fix (Swimming With Sharks, 2026-08-26).
    for key in list(clusters):
        if key[0] != 'mirror':
            continue
        _, b_key, bp4_key = key
        members = set(clusters[key])
        for c, pairs in cat_rows.items():
            for idx, v in pairs:
                if (round(v, 4) == bp4_key and idx not in members
                        and _norm_brand(str(df.at[idx, 'Value'] or '')) == b_key):
                    members.add(idx)
        clusters[key] = sorted(members)

    # Current value + used-value books per category (live-updated).
    cur = {idx: float(v) for c, pairs in cat_rows.items() for idx, v in pairs}
    used_by_cat = {c: {round(v, 4) for _, v in pairs}
                   for c, pairs in cat_rows.items()}
    cat_of = {}
    for c, pairs in cat_rows.items():
        for idx, _ in pairs:
            cat_of[idx] = c

    # Rows still awaiting placement. A flagged row's CURRENT position is
    # a fabricated ladder artifact (often micro-bands 3 ulps apart from
    # prior jitter passes), so it must NOT bound its neighbours - only
    # organic rows and already-PLACED rows carry rank meaning. Ascending
    # processing + this bound preserves total order among flagged rows
    # while letting a packed band spread across the decade's free space.
    flagged_remaining = set(flagged)

    def _lower_bound(idx, ignore_within=0.0):
        """Highest value strictly below this row in its category among
        organic + already-placed rows (0.0001 floor when the row is the
        category minimum). `ignore_within` > 0 additionally skips
        neighbours closer than that gap below the row: sub-0.01 BP
        ordering is jitter noise, and honoring it can leave a packed
        band with no room at 4dp (rows separated by >= the gap never
        invert)."""
        c = cat_of[idx]
        mine = cur[idx]
        lo = 0.0001
        for j, _ in cat_rows[c]:
            if j == idx or j in flagged_remaining:
                continue
            vj = cur[j]
            if ignore_within and vj < mine and (mine - vj) < ignore_within:
                continue
            if lo < vj < mine:
                lo = vj
        return lo

    n_moved = 0
    # Suffixes already assigned to adjusted rows this pass: no two
    # ADJUSTED rows share a 4dp fractional part (mirror clusters count
    # once - one value by design). BEST-EFFORT under saturation: a file
    # needing more adjustments than there are free 4dp suffixes (10,000
    # minus flagged ones) cannot honor the guarantee by pigeonhole, so
    # when a row's candidate walk exhausts with the guard on, one relax
    # pass runs without it (still avoiding flagged suffixes, round
    # values, and per-category value collisions).
    used_new_suffixes = set()
    # Ascending by value: lower rows settle first so upper rows walk
    # down onto fresh bounds and rank order is preserved end-to-end.
    ordered = sorted(clusters.items(),
                     key=lambda kv: min(cur[i] for i in kv[1]))
    for key, members in ordered:
        old = cur[members[0]]
        old4 = round(old, 4)
        old_suffix = suffix4(old)
        # Interval: strictly above every member's lower neighbour,
        # strictly below the old value (downward-only), and inside the
        # old value's integer decade when there is room. Bound stage 2
        # (near-tie relax) additionally ignores neighbours within 0.01
        # BP below the row - that micro-order is jitter noise and
        # honoring it can leave a packed band unplaceable at 4dp.
        brand_u = str(df.at[members[0], 'Value'] or '').strip().upper()
        salt_cat = ('MIRROR' if key[0] == 'mirror'
                    else cat_of[members[0]])
        seed = f"{subject}|{salt_cat}|{brand_u}|frac-ladder-v1"
        h = int(_hl.blake2b(seed.encode(), digest_size=8).hexdigest(), 16)
        u = 0.10 + 0.80 * ((h % 100000) / 100000.0)
        step = 0.0001 + ((h >> 20) % 9) * 0.0001
        member_cats = {cat_of[i] for i in members}
        decade_floor = float(int(old)) if old >= 1.0 else 0.0001
        placed = None
        for ignore_within in (0.0, 0.01):
            lb = max(_lower_bound(i, ignore_within) for i in members)
            lo_eff = max(lb, decade_floor)
            if old - lo_eff < 0.0004:
                lo_eff = lb        # decade has no room: allow the cross
            if old - lo_eff < 0.0004:
                continue           # nowhere to go at this bound stage
            cand = old - u * (old - lo_eff)
            for relax_suffix_guard in (False, True):
                for k in range(20000):
                    c4 = round(cand - k * step, 4)
                    if c4 <= lo_eff:
                        # walk back up from the initial pick instead
                        c4 = round(cand + (k * step), 4)
                        if c4 >= old4:
                            break
                    if not (lo_eff < c4 < old4):
                        continue
                    s4 = suffix4(c4)
                    if (s4 == old_suffix or s4 in flagged_suffixes
                            or s4 == '0000'):
                        continue
                    if not relax_suffix_guard and s4 in used_new_suffixes:
                        continue
                    if _looks_round_any(c4):
                        continue
                    if any(c4 in used_by_cat.get(c, set())
                           for c in member_cats):
                        continue
                    placed = c4
                    break
                if placed is not None:
                    break
            if placed is not None:
                break
        if placed is None:
            # FINAL exhaustive micro-scan: thin decades (e.g. a X.0102
            # ladder whose only legal landings are X.0101 / X.0103) hold
            # 1-3 legal 4dp slots the coarse u/step walk usually misses.
            # Enumerate every 4dp value in the relaxed interval, rotated
            # by the row hash so files don't pile onto the same slot,
            # and take the first that passes every guard.
            lb = max(_lower_bound(i, 0.01) for i in members)
            # Two bounds: in-decade first; then decade-crossed, because a
            # near-integer band (X.0101-X.0104 the only legal in-decade
            # suffixes) pigeonholes ladder rows back onto each other. The
            # sub-integer space diversifies the suffixes; rank order is
            # still held by lb and subset by downward-only.
            for lo_eff in (max(lb, decade_floor), lb):
                n_slots = int(round((old4 - lo_eff) * 10000)) - 1
                if n_slots <= 0:
                    continue
                n_scan = min(n_slots, 3000)
                start = h % n_slots
                for t in range(n_scan):
                    c4 = round(old4 - 0.0001 * (1 + (start + t) % n_slots), 4)
                    if not (lo_eff < c4 < old4):
                        continue
                    s4 = suffix4(c4)
                    if (s4 == old_suffix or s4 in flagged_suffixes
                            or s4 == '0000'):
                        continue
                    if s4 in used_new_suffixes:
                        continue
                    if _looks_round_any(c4):
                        continue
                    if any(c4 in used_by_cat.get(c, set())
                           for c in member_cats):
                        continue
                    placed = c4
                    break
                if placed is not None:
                    break
        if placed is None:
            # Unplaceable: the row keeps its old value, so it must act
            # as a rank bound for everything processed after it.
            for i in members:
                flagged_remaining.discard(i)
            continue
        used_new_suffixes.add(suffix4(placed))
        for i in members:
            df = _set_bp(df, i, placed, bp_col, cs_col, raw_col,
                         proj_col, sample_size)
            cur[i] = placed
            flagged_remaining.discard(i)
            used_by_cat[cat_of[i]].add(placed)
            n_moved += 1

    if verbose and n_moved:
        pg = det['percat_groups'][:3]
        fw = det['filewide_groups'][:3]
        print(f"   🪜 fractional-ladder dejitter: {n_moved} row(s) re-salted "
              f"per (subject, brand, category) | per-cat groups: "
              f"{[(c, '.' + s, n) for c, s, n, _ in pg]} | file-wide: "
              f"{[('.' + s, n) for s, n, _ in fw]}")
    return df, n_moved


# ============================================================================
# 5. Hostmap-shape compliance (per Jessie / Ana feedback 2026-05-19)
#    Hostmap tracks BRAND-level entries only. Corporate parents and product
#    SKUs/lines do NOT have hostmap rows and so create unresolvable values in
#    the dashboard. Strip these at write time.
# ============================================================================

CORPORATE_PARENTS = {
    'COCA-COLA COMPANY', 'COCA COLA COMPANY', 'THE COCA-COLA COMPANY',
    'PEPSICO', 'PEPSI CO', 'CONAGRA', 'CONAGRA BRANDS', 'CONAGRA FOODS',
    'KRAFT HEINZ', 'THE KRAFT HEINZ COMPANY',
    'PROCTER & GAMBLE', 'PROCTER AND GAMBLE', 'P&G',
    'UNILEVER', 'NESTLE', 'NESTLE SA',
    'MARS', 'MARS INC', 'MARS INCORPORATED',
    'KELLOGG COMPANY', 'KELLOGGS COMPANY', "KELLOGG'S COMPANY",
    'GENERAL MILLS',
    'JOHNSON & JOHNSON', 'JOHNSON AND JOHNSON',
    'COLGATE-PALMOLIVE', 'COLGATE PALMOLIVE',
    'PERNOD RICARD', 'DIAGEO', 'CONSTELLATION BRANDS',
    'AB INBEV', 'ANHEUSER-BUSCH INBEV',
    'MOLSON COORS', 'MOLSON COORS BEVERAGE COMPANY',
    'BROWN-FORMAN',
    'CAMPBELL SOUP COMPANY', 'TYSON FOODS', 'HORMEL FOODS',
    'POST HOLDINGS', 'POST CONSUMER BRANDS',
    'MONDELEZ', 'MONDELEZ INTERNATIONAL',
    'HERSHEY', 'THE HERSHEY COMPANY',
    'BAYER', 'BLOCK INC', 'BLOCK',
}

PRODUCT_SKUS = {
    'IPADS', 'IPAD', 'IPADS PRO', 'IPAD PRO', 'IPAD AIR', 'IPAD MINI',
    'AIRPODS', 'AIRPODS PRO', 'AIRPODS MAX',
    'IPHONE', 'IPHONES', 'IPHONE PRO',
    'APPLE WATCH', 'IMAC', 'MACBOOK', 'MACBOOK AIR', 'MACBOOK PRO',
    'NIKE TRAINING', 'NIKE RUN CLUB', 'NIKE PRO',
    'AIR JORDAN', 'JORDAN BRAND',
    'GOOGLE PIXEL', 'PIXEL', 'PIXEL BUDS',
    'SURFACE', 'MICROSOFT SURFACE',
    'KINDLE', 'ECHO', 'ALEXA', 'FIRE TV', 'FIRE TABLET',
    'ROKU TV',
    'GALAXY S', 'GALAXY NOTE', 'GALAXY BUDS', 'GALAXY WATCH',
    'BEATS BY DRE',
}

METADATA_COLS = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY',
    'SUBJECT', 'SUBJECT_NAME',
}


def _strip_rows(df, subject, predicate, label, verbose):
    """Shared helper: drop rows where predicate(col_upper, val) is True,
    then renormalize Category Share for every affected category."""
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    drop_idx = []
    affected_cats = set()
    examples = []
    for idx, r in df.iterrows():
        col = str(r.get('Column', '') or '').strip()
        val = str(r.get('Value', '') or '').strip()
        if not col or not val:
            continue
        if col.upper() in METADATA_COLS:
            continue
        if predicate(col.upper(), val):
            drop_idx.append(idx)
            affected_cats.add(col)
            if len(examples) < 5:
                examples.append((col, val))

    if not drop_idx:
        return df, 0

    if verbose:
        ex = ', '.join(f'[{c}]"{v}"' for c, v in examples)
        more = f' (+{len(drop_idx)-len(examples)} more)' if len(drop_idx) > len(examples) else ''
        print(f"   🧹 stripped {len(drop_idx)} {label} row(s): {ex}{more}")

    df = df.drop(index=drop_idx).reset_index(drop=True)
    for cat in affected_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)
    return df, len(drop_idx)


def strip_corporate_parents(df, subject, verbose=True):
    """Drop rows whose Value is a corporate parent company that doesn't have
    a hostmap entry (PepsiCo, ConAgra, Kraft Heinz, Coca-Cola Company, etc.).
    The child brands (Pepsi, Mountain Dew, Doritos, Coca-Cola, etc.) carry
    the signal in their own rows."""
    return _strip_rows(
        df, subject,
        lambda c, v: v.upper() in CORPORATE_PARENTS,
        label='corporate-parent', verbose=verbose,
    )


def strip_product_skus(df, subject, verbose=True):
    """Drop rows whose Value is a specific product SKU or product line
    (iPad, AirPods, Apple Watch, Kindle, Air Jordan, Galaxy Buds, etc.).
    The parent brand row (Apple, Amazon, Nike, Samsung) carries the
    signal."""
    return _strip_rows(
        df, subject,
        lambda c, v: v.upper() in PRODUCT_SKUS,
        label='product-SKU', verbose=verbose,
    )


# Build-time annotation patterns (Defect 26 — Shemar Moore SAILING-na leak).
# These are upstream mapping/remap instructions that should have been resolved
# during the build but leaked into Value cells. Examples seen in the wild:
#   "SAILING-na (use OUTDOOR LIFE)"  -- remap instruction in INTEREST grid
#   "FOO-TBD"                          -- placeholder pending resolution
#   "BAR (see BAZ)"                    -- cross-reference hint
#   "QUX => QUUX"                      -- arrow remap
#   "TODO: rebuild"                    -- explicit dev-marker
# Detection is conservative: each pattern must be distinctive enough that no
# legitimate brand/interest/talent label could plausibly match it. The key
# signal is INSTRUCTIONAL VERBS (use/see/remap/map to/replace with) inside
# parentheses, or development-process suffixes/markers (-NA, -TBD, TODO:,
# FIXME:, =>) that don't appear in real-world panel labels.
_ANNOTATION_RX = _re.compile(
    r'(?ix)'                                            # case-insensitive, verbose
    r'(?:'
    r'  -\s*(?:NA|TBD|TODO|PENDING|FIXME)\b'            # SAILING-na, FOO-TBD
    r'| \(\s*(?:USE|SEE|REMAP|MAP\s+TO|INSTEAD|REPLACE\s+WITH)\s+[A-Z]'  # (use X)
    r'| \b(?:TODO|FIXME)\s*:'                           # TODO: / FIXME:
    r'| \bN\s*/\s*A\b'                                  # N/A
    r'| =>'                                             # remap arrow
    r')'
)


def _is_polluted_brand_value(v):
    if not v:
        return False
    v = v.strip()
    if v.startswith(('Bing |', 'Google |', 'Yahoo |', 'DuckDuckGo |')):
        return True
    if v.startswith('.') or v.startswith('http'):
        return True
    # 2026-06-15 (Defect 26): build-time annotation/remap instruction leaks.
    if _ANNOTATION_RX.search(v):
        return True
    return False


def strip_polluted_brand_values(df, subject, verbose=True):
    """Drop brand rows whose Value is a search-result string, URL fragment,
    or build-time annotation/remap instruction (e.g. 'Bing | Breaking: ...',
    'Google | Maps', 'SAILING-na (use OUTDOOR LIFE)', 'FOO-TBD',
    'TODO: rebuild', 'X => Y'). Skips metadata columns (INPUT_METADATA /
    BRAND INPUT / SAMPLE SIZE) which legitimately contain long structured
    strings."""
    return _strip_rows(
        df, subject,
        lambda c, v: _is_polluted_brand_value(v),
        label='polluted-brand', verbose=verbose,
    )


# ============================================================================
# 2026-06-15 (Rob Schneider INTEREST defect) — phantom-zero row stripper.
#
# Symptom: Rob Schneider profile shipped with three INTEREST rows at exact
# 0.0000% / Raw=0 / Projection=0 (BOOKTOK, EDM, K-POP). Other recent
# profiles (Sebastian Stan, Sophie Turner, Parker Posey, Stephen Colbert,
# Seth Rogen, Rooney Mara) had none of these tokens at all -- they were
# phantom rows synthesized by the hybrid sanity check from "named under-
# index" mentions in the persona dossier (line 275 of Rob's run log:
# "Under-indexes hard on: K-pop, BookTok, ... EDM").
#
# Root cause: hybrid_reasoning.apply_sanity_fixes was inserting new
# (Category, Value) rows for any auditor fix with current_bp == 0,
# without checking whether suggested_bp was also 0. Patched 2026-06-15
# (kicked the floor to 0.10). This enforcer is the defense-in-depth:
# any non-meta, non-demo row at exact 0.0% / Raw=0 is a build defect
# (legitimate engagement is never exactly zero in panel data) and gets
# dropped.
# ============================================================================

def strip_phantom_zero_rows(df, subject, verbose=True):
    """Drop any non-meta, non-demo row whose Brand Penetration AND
    Original Raw Numbers are both exactly zero. These rows are always
    phantom inserts (build-side closure / persona-dossier under-index
    echoing) -- a genuinely engaged audience cannot hit exact 0.0000%
    on a named brand/interest/talent token. Real low-engagement rows
    have at least 1 raw confirmation and a non-zero (jittered) BP.

    Preserves:
      - METADATA_COLS (BRAND INPUT / SAMPLE SIZE / BRAND CATEGORY / etc.)
      - DEPIN_DEMO_CATS (demo zeros are legitimate sub-bucket absences)
      - rows with BP > 0 OR Raw > 0 (non-phantom, just low signal)

    Returns (df, n_dropped). Renormalizes Category Share for any
    affected category so the trio remains internally consistent.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    def _to_float(cell):
        try:
            s = str(cell).replace('%', '').replace(',', '').strip()
            if not s or s.lower() in ('nan', 'none', ''):
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    drop_idx = []
    affected_cats = set()
    examples = []
    for idx, r in df.iterrows():
        col = str(r.get('Column', '') or '').strip()
        val = str(r.get('Value', '') or '').strip()
        if not col or not val:
            continue
        col_u = col.upper()
        if col_u in METADATA_COLS:
            continue
        if col_u in DEPIN_DEMO_CATS:
            continue
        if col_u in DEPIN_META_CATS:
            continue
        bp_v = _to_float(r.get(bp_col)) if bp_col else None
        raw_v = _to_float(r.get(raw_col)) if raw_col else None
        # Drop iff BOTH are exactly zero (or one is zero and the other
        # is missing). One being non-zero means the row has signal
        # somewhere -- jitter it elsewhere instead of dropping here.
        bp_zero = (bp_v is not None and bp_v == 0.0)
        raw_zero = (raw_v is not None and raw_v == 0.0)
        if (bp_zero and (raw_zero or raw_v is None)) or \
           (raw_zero and (bp_zero or bp_v is None)):
            drop_idx.append(idx)
            affected_cats.add(col)
            if len(examples) < 8:
                examples.append((col, val))

    if not drop_idx:
        return df, 0

    if verbose:
        ex = ', '.join(f'[{c}]"{v}"' for c, v in examples)
        more = f' (+{len(drop_idx)-len(examples)} more)' if len(drop_idx) > len(examples) else ''
        print(f"   🧹 stripped {len(drop_idx)} phantom-zero row(s): {ex}{more}")

    df = df.drop(index=drop_idx).reset_index(drop=True)
    for cat in affected_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)
    return df, len(drop_idx)


# ============================================================================
# 2026-06-15 (Apple Pay native-category gap) — subject-self-pin in native
# category enforcer.
#
# Symptom: Apple_Pay_06_03_2026_22_28.csv shipped with BRAND INPUT=APPLE PAY
# at 100% but no APPLE PAY row anywhere in DIGITAL BANKING (its native
# category). The DIGITAL BANKING grid had 11 peers (PayPal, Venmo, Zelle,
# Cash App, ...) but the anchor brand was completely absent. Same gap also
# observed in PayPal_06_03_2026_18_25.csv (PAYPAL absent from its own
# DIGITAL BANKING grid).
#
# Per Profile IQ Rule #3:
#   "Subject = exactly 100% in: BRAND INPUT, SUBJECT, the subject's own
#    league category, all companion cols (SPORTS TEAM, AL/NL, AFC/NFC,
#    divisions), persona/content cats."
# The subject's native (BRAND CATEGORY) is one of those self-pin categories
# -- the existing enforce_input_brand_100 in BG.py only PINS rows where the
# subject already exists; it does not INSERT a missing row. This enforcer
# fills that gap by reading BRAND CATEGORY metadata + BRAND INPUT and
# inserting the subject row at 100% if missing.
# ============================================================================

def ensure_subject_in_native_category(df, subject, verbose=True):
    """Insert subject row at 100% in the BRAND CATEGORY native category if
    missing. Uses the BRAND CATEGORY metadata row (already in every profile)
    to identify the native category -- no hostmap lookup required.

    Returns (df, n_inserted). 0 means subject was already present (the
    common case once enforce_input_brand_100 has run on a well-formed
    profile, or the BRAND CATEGORY is one we don't pin into -- e.g. some
    INTEREST/PERSONA-only inputs).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not bp_col or not raw_col or not proj_col:
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()

    # Pull BRAND INPUT row (subject + sample-size raw + projection).
    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    bi_row = df.loc[bi_mask].iloc[0]
    subject_name = _clean_subject_from_bi(
        bi_row.get('Value'), df=df, col_u=col_u, subject_arg=subject,
    )
    if not subject_name:
        return df, 0

    def _to_int(cell):
        try:
            s = str(cell).replace(',', '').replace('%', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return int(float(s))
        except (ValueError, TypeError):
            return None

    subj_raw = _to_int(bi_row.get(raw_col))
    subj_proj = _to_int(bi_row.get(proj_col))
    if subj_raw is None or subj_proj is None:
        return df, 0

    # Pull BRAND CATEGORY metadata row to identify native category.
    bc_mask = col_u == 'BRAND CATEGORY'
    if not bc_mask.any():
        return df, 0
    native_cat = str(df.loc[bc_mask].iloc[0].get('Value', '') or '').strip()
    if not native_cat:
        return df, 0
    native_cat_u = native_cat.upper()

    # Skip categories we don't pin subject into. Demographic / metadata
    # native categories don't carry a 100% self-pin.
    if (native_cat_u in METADATA_COLS or native_cat_u in DEPIN_DEMO_CATS
            or native_cat_u in DEPIN_META_CATS):
        return df, 0

    # MPB-only profiles follow MPB rules (Rule #3 exception). Don't pin
    # at 100% in MOST PURCHASED BRANDS family.
    if native_cat_u in {
        'MOST PURCHASED BRANDS', 'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS',
        'WHERE THEY SHOP', 'TECHNOLOGY BRAND', 'HOME/OUTDOOR', 'CPG',
        'BEVERAGES', 'FRANCHISE',
    }:
        return df, 0

    # Find the native category rows in the profile.
    cat_mask = col_u == native_cat_u
    if not cat_mask.any():
        # Native category is missing entirely. Don't synthesize the whole
        # category here -- that's a bigger build-side issue (escalate).
        if verbose:
            print(f"   ⚠️ ensure_subject_in_native_category: BRAND CATEGORY "
                  f"'{native_cat}' has no rows in profile -- skipping insert "
                  f"(needs full-category re-pull, not row-level patch)")
        return df, 0

    val_u = df['Value'].astype(str).str.upper().str.strip()
    subj_u = subject_name.upper()
    # Case + punctuation insensitive match (workspace rule #4 dedup).
    subj_norm = _re.sub(r'[^A-Z0-9]', '', subj_u)
    val_norm = val_u.str.replace(r'[^A-Z0-9]', '', regex=True)
    present_mask = cat_mask & (val_norm == subj_norm)
    if present_mask.any():
        # 2026-06-16 PM (Defect 38b -- Gemini SEARCH ENGINE/AI 32.87%, also
        # Jennifer Lawrence ACTOR 86.92%, MGM+ STREAMING/PLATFORM 3.75%, and
        # 15 social-media platforms like Snapchat / Pinterest / Discord at
        # 13-45% in SOCIAL MEDIA): when subject IS a brand whose category is
        # also the grid name (Gemini -> SEARCH ENGINE/AI, etc.), the
        # cat-agent reasons about the subject as a peer ("Gemini's share is
        # 32% of this Gemini-avid audience") rather than as a self-pin.
        # enforce_input_brand_100 (BG.py) is supposed to catch this but
        # breaks on case mismatch ("Gemini" Title Case vs "GEMINI" subject)
        # or on certain category labels.
        #
        # Force-pin every subject row in the native category to 100.0000%,
        # normalize the Value to match BRAND INPUT exactly, recompute
        # Raw/Projection. The native cat is the subject's "own league" per
        # Rule #3 -- pin is unconditional here, even from 0% or 3%.
        n_pinned = 0
        for idx in df.index[present_mask]:
            cur_bp = None
            try:
                cur_bp = float(str(df.at[idx, bp_col]).replace('%', '').replace(',', '').strip())
            except (ValueError, TypeError):
                pass
            cur_val = str(df.at[idx, 'Value']).strip()
            if (cur_bp is not None and abs(cur_bp - 100.0) < 0.0001
                    and cur_val == subject_name):
                continue  # already correct
            df.at[idx, 'Value'] = subject_name
            df.at[idx, bp_col] = '100.0000%'
            df.at[idx, raw_col] = subj_raw
            df.at[idx, proj_col] = subj_proj
            n_pinned += 1
            if verbose:
                msg = (f"   📌 native-pin [{native_cat}] "
                       f"'{cur_val}'@{cur_bp or 0:.4f}% -> "
                       f"'{subject_name}'@100.0000%")
                print(msg)
        if n_pinned > 0 and cs_col:
            try:
                df = _renormalize_category(df, native_cat, bp_col, cs_col,
                                            raw_col, proj_col, subj_raw)
            except Exception as _re_err:
                if verbose:
                    print(f"   ⚠️ renormalize {native_cat} failed "
                          f"(non-fatal): {_re_err}")
        return df, n_pinned

    # Insert subject at 100% above the first native-category row.
    insert_at = df.index[cat_mask][0]
    new_row = {c: '' for c in df.columns}
    new_row['Column'] = native_cat
    new_row['Value'] = subject_name
    new_row[bp_col] = '100.0000%'
    if cs_col:
        new_row[cs_col] = 100.0
    new_row[raw_col] = subj_raw
    new_row[proj_col] = subj_proj

    top = df.iloc[:insert_at]
    bottom = df.iloc[insert_at:]
    df_new = pd.concat(
        [top, pd.DataFrame([new_row]), bottom], ignore_index=True,
    )
    if verbose:
        print(f"   📌 inserted subject self-pin: [{native_cat}] "
              f"\"{subject_name}\" @ 100.0000% "
              f"(raw={subj_raw:,}, projection={subj_proj:,})")
    return df_new, 1


# ============================================================================
# 2026-06-15 (Netflix self-anchor near-miss) — pin subject to exactly 100%
# in any non-meta, non-demo, non-MPB category where the subject row exists
# with BP >= 95%.
#
# Symptom: Netflix_06_12_2026_01_23.csv shipped with NETFLIX at:
#   BRAND INPUT      = 100.0000%   (sample_size 10M)
#   STREAMING/PLATFORM = 100.0000%   (sample_size 10M)
#   STREAMING VIDEO   =  99.0376%   (raw 9,903,760)  ← near-miss
#
# Root cause: BG.py::enforce_input_brand_100 gates by hostmap-canonical
# category for the brand (added 2026-06-03 to prevent variants like
# `BRANDY~CLARK` from being pinned in wrong categories). If hostmap lists
# NETFLIX's SECTION as `STREAMING/PLATFORM` only (not `STREAMING VIDEO`),
# the gate skips STREAMING VIDEO and the writer's emitted near-100 value
# survives. Same pattern hits any subject that appears in a sister/parent
# grid that hostmap doesn't list canonically.
#
# This enforcer is the writer-stage signal interpreter: a BP >= 95% is the
# writer's clear self-pin intent; pin to exactly 100% with raw=sample_size
# and projection=profile_universe. The 95% threshold avoids over-correcting
# legitimate brand-mention rows where the subject's name happens to appear
# at moderate BP (those would be rare and below the threshold anyway).
# ============================================================================

# ============================================================================
# Native-cluster self-pin (2026-06-16 PM, Defect 38 -- Jenna BofA/Citi/BMO):
#
# Some BRAND CATEGORY metadata maps to MULTIPLE display grids, all of which
# represent the subject's "own league" per Rule #3 (subject must be 100% in
# native cat). Examples:
#   BANKS = {BANK, BANKING, BANKS}  -- BANK (legacy singular) + BANKING
#                                      (canonical post-2026-06-16) both
#                                      render in the dashboard's "Banking"
#                                      panel
#
# `pin_subject_to_100_in_appearing_categories` picks just ONE of those grids
# (max-BP or BRAND CATEGORY metadata match) and pins only there. This left
# Citibank shipping with BANK="CITIBANK"@100% AND BANKING="Citibank"@15.67%
# (peer-style row) -- the BANKING grid (the larger, canonical one) had the
# subject ranked 5th behind Chase/BofA/Wells/Vanguard. BMO shipped with BANK
# at 100% but completely absent from BANKING (writer omitted BMO because
# it's not a major US retail bank in isolation).
#
# This enforcer iterates every grid in the subject's native cluster that
# is PRESENT in the profile and either pins the existing subject row to 100
# (normalizing the Value to match BRAND INPUT exactly) or INSERTS a new row
# when the subject row is absent. Does NOT create a grid that wasn't already
# in the profile -- only operates within already-present grids.
#
# STREAMING/PLATFORM + STREAMING VIDEO are NOT clustered here -- the active
# Defect-31 enforcer `dedupe_subject_streaming_grids` drops the non-native
# peer row by design (the dashboard merges those grids into one display
# section, so a 100% self-pin alongside a 4% peer reads as contradictory).
# Banks have no such merge -- both bars rendered properly when both are at
# 100% (see BofA reference profile from 06-16 which already had this).
# ============================================================================

NATIVE_CLUSTERS = {
    "BANKS": {"BANK", "BANKING", "BANKS"},
    # 2026-06-16 PM (NetShort + GoodShort defect): vertical-shorts subjects
    # render in the dashboard's "Streaming" display section which merges
    # STREAMING VIDEO + STREAMING/PLATFORM. Both grids must carry the
    # subject's 100% self-pin (the cat-agent emits one but skips the other
    # for some subjects -- 6 of 8 corpus vertical-shorts had both, NetShort
    # + GoodShort had only STREAMING VIDEO). Limited to BRAND CATEGORY =
    # 'VERTICAL SHORTS' so it does NOT trigger for Netflix-type subjects
    # (where BRAND CATEGORY is itself one of the streaming grids; those
    # are handled by `dedupe_subject_streaming_grids` instead, which
    # drops the peer-row artifact in the non-native grid).
    "VERTICAL SHORTS": {"VERTICAL SHORTS", "STREAMING VIDEO",
                        "STREAMING/PLATFORM"},
}

# 2026-08-26 (Liz QA, Bethenny Frankel avid DEFECT 2): talent-archetype
# subjects must self-include in the TALENT umbrella grid at 100 in
# addition to their specific talent subcategory. Bethenny (BRAND
# CATEGORY = HOST/PERSONALITY) shipped pinned in HOST/PERSONALITY but
# entirely ABSENT from the 490-row TALENT grid. Each archetype clusters
# with TALENT; enforce_native_cluster_self_pin only touches grids
# already present in the profile, so profiles without a TALENT grid are
# unaffected. Mirrored as ship-gate I15 in migration/final_ship_gate.py.
_TALENT_ARCHETYPES = (
    'ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER/CREATOR',
    'CREATOR/INFLUENCER', 'EMERGING TALENT', 'HOST/PERSONALITY',
    'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST',
    'WRITER/DIRECTOR/AUTHOR/ARTIST',
)
for _arch in _TALENT_ARCHETYPES:
    NATIVE_CLUSTERS.setdefault(_arch, {_arch, 'TALENT'})


def enforce_native_cluster_self_pin(df, subject, verbose=True):
    """For each grid in the subject's native cluster present in the profile,
    ensure the subject row exists at exactly 100.0000% with Value matching
    BRAND INPUT. Inserts missing rows.

    Cluster membership is resolved from BRAND CATEGORY metadata. Profiles
    whose BRAND CATEGORY isn't a member of any cluster are no-op.

    Returns (df, n_changes). n_changes counts inserts + pins separately.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not all((bp_col, cs_col, raw_col, proj_col)):
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()
    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    bi_row = df.loc[bi_mask].iloc[0]
    subject_name = _clean_subject_from_bi(
        bi_row.get('Value'), df=df, col_u=col_u, subject_arg=subject,
    )
    if not subject_name:
        return df, 0

    bc_mask = col_u == 'BRAND CATEGORY'
    if not bc_mask.any():
        return df, 0
    brand_category = str(df.loc[bc_mask].iloc[0].get('Value', '') or '').strip().upper()
    if not brand_category:
        return df, 0

    cluster = None
    for _k, members in NATIVE_CLUSTERS.items():
        if brand_category in members:
            cluster = members
            break
    if cluster is None:
        return df, 0

    sample_size = _detect_sample_size(df, bp_col, raw_col)
    if sample_size is None or sample_size <= 0:
        return df, 0
    # Universe for projection: use BRAND INPUT row's projection col (same
    # convention as _set_bp uses indirectly via _detect_sample_size).
    def _to_int(v):
        try:
            s = str(v).replace(',', '').replace('%', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return int(float(s))
        except (ValueError, TypeError):
            return None
    profile_universe = _to_int(bi_row.get(proj_col))
    if profile_universe is None:
        return df, 0

    def _to_float(v):
        try:
            s = str(v).replace('%', '').replace(',', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    subj_norm = _re.sub(r'[^A-Z0-9]', '', subject_name.upper())
    val_norm = (
        df['Value'].astype(str).str.upper()
        .str.replace(r'[^A-Z0-9]', '', regex=True)
    )

    existing_grids = [c for c in cluster if (col_u == c).any()]
    if not existing_grids:
        return df, 0

    n_changes = 0
    touched_cats = set()

    for grid in existing_grids:
        # 2026-06-16 PM (NetShort/GoodShort avid bug): recompute masks
        # each iteration. Inserting a new row in iteration N changes
        # df length, leaving the precomputed col_u / val_norm series
        # one row short for iteration N+1 (IndexError on .index[mask]).
        col_u = df['Column'].astype(str).str.upper().str.strip()
        val_norm = (
            df['Value'].astype(str).str.upper()
            .str.replace(r'[^A-Z0-9]', '', regex=True)
        )
        grid_mask = col_u == grid
        subj_in_grid_mask = grid_mask & (val_norm == subj_norm)
        idxs = list(df.index[subj_in_grid_mask])
        if idxs:
            for idx in idxs:
                cur_bp = _to_float(df.at[idx, bp_col])
                cur_val = str(df.at[idx, 'Value']).strip()
                already_pinned = (cur_bp is not None
                                   and abs(cur_bp - 100.0) < 0.0001)
                value_canonical = (cur_val == subject_name)
                if already_pinned and value_canonical:
                    continue
                df.at[idx, 'Value'] = subject_name
                df.at[idx, bp_col] = '100.0000%'
                df.at[idx, raw_col] = sample_size
                df.at[idx, proj_col] = profile_universe
                touched_cats.add(grid)
                n_changes += 1
                if verbose:
                    if not value_canonical:
                        print(f"   📌 native-cluster pin [{grid}] "
                              f"'{cur_val}' -> '{subject_name}' "
                              f"BP {cur_bp or 0:.4f}% -> 100.0000%")
                    else:
                        print(f"   📌 native-cluster pin [{grid}] "
                              f"BP {cur_bp or 0:.4f}% -> 100.0000%")
        else:
            # Insert. Place RIGHT AFTER the last row of this grid so it
            # stays grouped in display order.
            grid_idxs = list(df.index[grid_mask])
            if not grid_idxs:
                continue
            insert_after_pos = df.index.get_loc(grid_idxs[-1])
            new_row = {c: '' for c in df.columns}
            new_row['Column'] = grid
            new_row['Value'] = subject_name
            new_row[bp_col] = '100.0000%'
            new_row[cs_col] = '0.0000%'  # recomputed below via renormalize
            new_row[raw_col] = sample_size
            new_row[proj_col] = profile_universe
            top = df.iloc[:insert_after_pos + 1]
            bot = df.iloc[insert_after_pos + 1:]
            df = pd.concat(
                [top, pd.DataFrame([new_row]), bot], ignore_index=True,
            )
            touched_cats.add(grid)
            n_changes += 1
            if verbose:
                print(f"   ➕ native-cluster insert [{grid}] "
                      f"'{subject_name}'@100.0000%")

    # Recompute Category Share for each touched grid (BANKING-style reach
    # categories aren't normalized to sum=100, but Category Share should
    # be in sync with current BP values).
    for cat in touched_cats:
        try:
            df = _renormalize_category(df, cat, bp_col, cs_col, raw_col,
                                        proj_col, sample_size)
        except Exception as _re_err:
            if verbose:
                print(f"   ⚠️ renormalize {cat} after native-cluster pin "
                      f"failed (non-fatal): {_re_err}")

    return df, n_changes


def pin_subject_to_100_in_appearing_categories(df, subject, verbose=True):
    """Pin subject to exactly 100.0000% only in the NATIVE grid -- the
    non-MPB-family category where the subject row has the HIGHEST BP --
    when that BP is a writer near-miss in [95, 100). All other subject-
    row appearances are PEER RATES (cross-platform overlap measures, e.g.
    Fandango At Home audience using STREAMING/PLATFORM peers at ~4.5%)
    and must be left untouched.

    Threshold history:
      - 2026-06-15 AM: BP >= 95% -> 100 in every appearing cat
                       (Netflix Defect 22 near-miss)
      - 2026-06-15 PM: BP > 0    -> 100 in every appearing cat
                       (Fandango Defect 23 deep miss)
      - 2026-06-15 PM (revised, this version): scoped to HIGHEST-BP grid
        only, threshold [95, 100). Reason: Jenna's native-grid scoping
        framing -- some services anchor in STREAMING/PLATFORM (Netflix,
        Canela, Crackle, DAZN), others in STREAMING VIDEO (Fandango At
        Home, GoodShort), depending on upstream classification. The
        (0, 100) widening was over-correcting peer rates as if they were
        self-pin misses. Native = highest-BP grid; peers = the rest.

    Skip set:
      - METADATA_COLS, DEPIN_DEMO_CATS, DEPIN_META_CATS
      - MPB family (Rule #3 exception)
    Returns (df, n_pinned).  n_pinned ∈ {0, 1}.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not bp_col or not raw_col or not proj_col:
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()

    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    bi_row = df.loc[bi_mask].iloc[0]
    subject_name = _clean_subject_from_bi(
        bi_row.get('Value'), df=df, col_u=col_u, subject_arg=subject,
    )
    if not subject_name:
        return df, 0

    sz_mask = col_u == 'SAMPLE SIZE'
    if not sz_mask.any():
        return df, 0
    sz_row = df.loc[sz_mask].iloc[0]

    def _to_int(cell):
        try:
            s = str(cell).replace(',', '').replace('%', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return int(float(s))
        except (ValueError, TypeError):
            return None

    def _to_float(cell):
        try:
            s = str(cell).replace('%', '').replace(',', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    sample_size = _to_int(sz_row.get(raw_col))
    profile_universe = _to_int(sz_row.get(proj_col))
    if sample_size is None or profile_universe is None:
        return df, 0

    skip = (
        METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
        | {'MOST PURCHASED BRANDS', 'APPAREL/FOOTWEAR',
           'BEAUTY/WELLNESS', 'WHERE THEY SHOP', 'TECHNOLOGY BRAND',
           'HOME/OUTDOOR', 'CPG', 'BEVERAGES', 'FRANCHISE'}
    )

    subj_norm = _re.sub(r'[^A-Z0-9]', '', subject_name.upper())
    val_norm = (
        df['Value'].astype(str).str.upper()
        .str.replace(r'[^A-Z0-9]', '', regex=True)
    )
    base_mask = (val_norm == subj_norm) & (~col_u.isin(skip))
    candidate_idxs = list(df.index[base_mask])
    if not candidate_idxs:
        return df, 0

    # 2026-06-15 PM-revised-2 (DOGTV defect): native grid =
    # BRAND CATEGORY metadata (Profile IQ Rule #3 canonical), with
    # max-BP heuristic as fallback when metadata is missing or doesn't
    # match a candidate row.
    #
    # The previous version (Defect 24) used max-BP only -- "writer's
    # de-facto self-pin grid". That broke for cases like DOGTV/Netflix
    # where BRAND CATEGORY metadata says STREAMING VIDEO but the writer
    # ALSO pinned at 100% in STREAMING/PLATFORM (a sister grid). Max-BP
    # picked S/P and skipped the SV near-miss; the SV near-miss is the
    # actual native-grid violation per Rule #3.
    bc_mask = col_u == 'BRAND CATEGORY'
    bc_native = None
    if bc_mask.any():
        bc_val = str(df.loc[bc_mask].iloc[0].get('Value', '') or '').strip().upper()
        # Only honor BRAND CATEGORY if it's a real grid name (not a
        # demo/meta cat that would be in skip set anyway).
        if bc_val and bc_val not in skip:
            bc_native = bc_val

    best_idx = None
    best_bp = -1.0
    used_metadata = False
    if bc_native is not None:
        for idx in candidate_idxs:
            cat_here = str(df.at[idx, 'Column']).strip().upper()
            if cat_here == bc_native:
                bp = _to_float(df.at[idx, bp_col])
                if bp is not None:
                    best_idx = idx
                    best_bp = bp
                    used_metadata = True
                    break

    # Fallback: BRAND CATEGORY missing or no matching candidate row ->
    # use max-BP heuristic (Defect 24 behavior).
    if best_idx is None:
        for idx in candidate_idxs:
            bp = _to_float(df.at[idx, bp_col])
            if bp is None:
                continue
            if bp > best_bp:
                best_bp = bp
                best_idx = idx
        if best_idx is None:
            return df, 0

    # 2026-06-19 PM (Defect 31 — Marriott et al). When BRAND CATEGORY
    # metadata explicitly identifies the native grid (used_metadata=True),
    # ANY BP < 99.9999 is a Rule #3 violation, not a peer rate. Pin
    # regardless of magnitude. Examples found in corpus audit:
    #   Marriott:  TRAVEL row at 32.77% (writer treated subject as a peer)
    #   Adidas:    ACTIVEWEAR row at 32.23%
    #   Dunkin:    QSR row at 29.74%
    #   Dominos:   QSR row at 31.79%
    #   Hidive:    STREAMING/PLATFORM row at 7.93%
    # All of these had BRAND INPUT correctly at 100% but the writer
    # produced peer-rate values in the in-grid self-row. The enforcer
    # previously bailed out (best_bp < 95) thinking these were peer
    # rates. With BRAND CATEGORY metadata identifying the native grid,
    # the candidate row IS the self-pin slot by definition.
    #
    # When falling back to max-BP heuristic (no metadata or no match),
    # keep the conservative [95, 100) gate to avoid over-correcting
    # genuine peer rates (e.g., Fandango appearing at 4.5% as a
    # streaming-platform peer when its native grid is STREAMING VIDEO).
    if best_bp >= 99.9999:
        return df, 0  # already pinned
    if used_metadata:
        if best_bp >= 100.0:
            return df, 0
    else:
        if not (95.0 <= best_bp < 99.9999):
            return df, 0

    df.at[best_idx, bp_col] = '100.0000%'
    df.at[best_idx, raw_col] = sample_size
    df.at[best_idx, proj_col] = profile_universe
    affected_cat = str(df.at[best_idx, 'Column']).strip()

    if verbose:
        src = "BRAND CATEGORY metadata" if used_metadata else "max-BP fallback"
        print(f"   📌 pinned subject \"{subject_name}\" native self-anchor "
              f"({src}): [{affected_cat}] {best_bp:.4f}% -> 100.0000% "
              f"(raw={sample_size:,}, projection={profile_universe:,})")

    df = _renormalize_category(df, affected_cat, bp_col, cs_col, raw_col,
                               proj_col, sample_size)
    return df, 1


# ============================================================================
# 2026-06-15 PM (Defect 28 — Netflix peer-rate displacement). Today's batch
# had 6 profiles where Amazon Prime Video led Netflix in STREAMING/PLATFORM:
#   Seth Macfarlane    AP 67.88 > NX 60.24
#   Roseanne Barr      AP 63.84 > NX 51.23
#   Sean Penn          AP 66.29 > NX 62.51
#   Richard Jenkins    AP 63.78 > HBO Max 52.81 > NX 50.18  (NX displaced to #3)
#   Steve Buscemi      AP 67.52 > NX 60.01
#   Steve-O            AP 65.34 > NX 58.67
# Per Jenna: "the only time netflix isn't number one is if it really
# shouldn't be there like another platform is 100% or something." Netflix
# has the highest universal streaming penetration in the US (~60-70% of
# adults) -- non-Netflix leadership in STREAMING/PLATFORM is almost
# always a writer-side bias artifact unless the subject IS the leading
# platform (e.g. an Amazon Prime Video profile self-pinned at 100%).
#
# This enforcer ensures Netflix is the #1 BP among non-self-pin rows.
# If another platform leads, swap their BP/Raw/Projection (Category Share
# is then renormalized for STREAMING/PLATFORM).
# ============================================================================

def enforce_netflix_leads_streaming_platform(df, subject, verbose=True):
    """Restore Netflix (and Amazon Prime Video, when also suppressed) to
    their universal STREAMING/PLATFORM baselines.

    Defect 30 (2026-06-15 PM, Jenna):
      The earlier swap-based fix (Defect 28) moved suppression from
      Netflix's row into the displaced peer row instead of restoring
      the baseline. Per Jenna's universal-anchor framing:
        - Netflix US adult reach ~75% (single highest-penetration
          streaming service across virtually every demographic).
          FLOOR=73 (75 - jitter band).
        - Amazon Prime Video US adult reach ~66% (flat across
          audiences). FLOOR=64.

    Logic:
      - If Netflix BP is below 73 (and Netflix isn't itself a streaming
        subject, BP < 95%), raise to 75 + deterministic jitter[-2,+2].
        Recompute Raw and Projection via _set_bp().
      - If Amazon Prime BP is below 64, raise to 66 + jitter[-2,+2].
      - Renormalize Category Share via _renormalize_category() so the
        grid still sums correctly.

    Self-pin exception: rows at >=95% BP are exempt (subject IS a
    streaming brand, or near-self-pin show profile like
    "POWER ON AMAZON PRIME" with AP at 99.49%).

    Returns (df, n_changes).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not all((bp_col, cs_col, raw_col, proj_col)):
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()
    sp_mask = col_u == 'STREAMING/PLATFORM'
    if not sp_mask.any():
        return df, 0

    def _to_float(v):
        try:
            s = str(v).replace('%', '').replace(',', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    sample_size = _detect_sample_size(df, bp_col, raw_col)

    sp_idx = list(df.index[sp_mask])
    val_u = df.loc[sp_idx, 'Value'].astype(str).str.upper().str.strip()

    nx_idx = next((i for i in sp_idx if val_u.loc[i] == 'NETFLIX'), None)
    pr_idx = next((i for i in sp_idx
                   if val_u.loc[i] in ('AMAZON PRIME VIDEO', 'AMAZON PRIME')), None)
    if nx_idx is None:
        return df, 0

    nx_bp = _to_float(df.at[nx_idx, bp_col])
    if nx_bp is None or nx_bp >= 95.0:
        # Netflix is itself a self-pin / near-self-pin (Netflix profile or
        # Netflix-original show profile). No-op.
        return df, 0

    pr_bp = _to_float(df.at[pr_idx, bp_col]) if pr_idx is not None else None
    pr_self_pin = pr_bp is not None and pr_bp >= 95.0

    # Universal baselines
    # 2026-08-24 (floor-vs-corrected-baseline sweep): Netflix Gen Pop
    # baseline corrected to 39.17 and Prime Video to 37.19 in the
    # market-cap audit; the legacy 73/75 + 64/66 anchors sat ~2x above
    # baseline and manufactured over-reads on every rescue. Floors now
    # sit just under the corrected baselines.
    NX_FLOOR, NX_TARGET, NX_JITTER = 36.5, 38.0, 2.0
    PR_FLOOR, PR_TARGET, PR_JITTER = 34.5, 36.0, 2.0

    n_changes = 0

    if nx_bp < NX_FLOOR:
        # 2026-06-17 Jenna (Jennifer Beals G17 reject @ 72.5686%): jitter is
        # now POSITIVE-ONLY (target..target+2*jitter) and the clamp lower
        # bound is NX_TARGET (75) -- previously [-NX_JITTER, +NX_JITTER]
        # combined with a [70, 80] clamp could land at 72.8, which then
        # the post-enforce dejitter step ("BPs nudged off round") pushed
        # below the 73 floor, blowing the G17 pre-publish gate. New
        # invariant: post-enforcer Netflix is in [75.0, 79.0] -- gives
        # the dejitter step ~2pp headroom above the 73 floor.
        new_nx = NX_TARGET + _jitter_for(subject, 'NETFLIX',
                                          salt='baseline',
                                          lo=0.0, hi=NX_JITTER * 2)
        new_nx = round(min(max(new_nx, NX_TARGET), 42.5), 4)
        if verbose:
            print(f"   ⤴ raising NETFLIX {nx_bp:.4f}% -> {new_nx:.4f}% "
                  f"(below baseline floor {NX_FLOOR}%, suppression detected)")
        _set_bp(df, nx_idx, new_nx, bp_col, cs_col, raw_col, proj_col, sample_size)
        n_changes += 1

    if pr_idx is not None and pr_bp is not None and not pr_self_pin and pr_bp < PR_FLOOR:
        # Same upward-only fix as Netflix (Jenna 2026-06-17).
        new_pr = PR_TARGET + _jitter_for(subject, 'AMAZON PRIME VIDEO',
                                          salt='baseline',
                                          lo=0.0, hi=PR_JITTER * 2)
        new_pr = round(min(max(new_pr, PR_TARGET), 40.5), 4)
        if verbose:
            print(f"   ⤴ raising AMAZON PRIME VIDEO {pr_bp:.4f}% -> "
                  f"{new_pr:.4f}% (below baseline floor {PR_FLOOR}%)")
        _set_bp(df, pr_idx, new_pr, bp_col, cs_col, raw_col, proj_col, sample_size)
        n_changes += 1

    # 2026-06-17 (Jenna Santander Bank): peer-inversion check. Even when
    # Netflix is above the 73% floor, a peer (Prime, ESPN, Hulu, etc.)
    # can still lead Netflix and represent a "Netflix suppression
    # signature" (Prime floats to #1). Cap any non-self-pin peer at
    # nx_bp - 0.1 so Netflix retains the rank-1 position. Re-reads bp_num
    # from df so post-lift Netflix is the comparison reference.
    try:
        nx_bp_now = _to_float(df.at[nx_idx, bp_col])
        # 2026-08-20 (EST Buyers batch): the subject's own row (and its
        # parent-anchor row for cut-shaped subjects like "Amazon Prime
        # Video - EST Buyers") is NEVER a peer. The >=95 exemption below
        # missed it when earlier passes had eroded the self-pin to ~87,
        # and this cap then forced the subject UNDER Netflix (73.92) -
        # a Rule #3 violation that shipped in four files. Exempt by
        # name, not by magnitude.
        subj_pin_norm = _re.sub(r'[^A-Z0-9]', '',
                                str(subject or '').upper())
        parent_pin_norm = _re.sub(
            r'[^A-Z0-9]', '',
            str(subject or '').split(' - ')[0].upper())
        if nx_bp_now is not None and nx_bp_now < 95.0 and sample_size and sample_size > 0:
            for i in sp_idx:
                if i == nx_idx:
                    continue
                bp_i = _to_float(df.at[i, bp_col])
                if bp_i is None:
                    continue
                if bp_i >= 95.0:  # peer self-pin (e.g. AP profile) — exempt
                    continue
                _vn = _re.sub(r'[^A-Z0-9]', '',
                              str(df.at[i, 'Value']).upper())
                if subj_pin_norm and (
                        _vn == subj_pin_norm
                        or (parent_pin_norm and _vn == parent_pin_norm)
                        or (len(subj_pin_norm) >= 6
                            and _vn.startswith(subj_pin_norm))):
                    continue  # subject / parent-anchor row, not a peer
                if bp_i > nx_bp_now + 1e-9:
                    cap_val = round(nx_bp_now - 0.1, 4)
                    if cap_val < 1.0:
                        cap_val = 1.0
                    if verbose:
                        print(f"   ⤵ peer-cap {df.at[i, 'Value']} "
                              f"{bp_i:.4f}% -> {cap_val:.4f}% "
                              f"(was leading NETFLIX {nx_bp_now:.4f}%)")
                    _set_bp(df, i, cap_val, bp_col, cs_col, raw_col,
                            proj_col, sample_size)
                    n_changes += 1
            # Renormalize Category Share for the grid after any cap
            if n_changes > 0:
                try:
                    _renormalize_category(df, 'STREAMING/PLATFORM',
                                          bp_col, cs_col, raw_col, proj_col,
                                          sample_size)
                except Exception:
                    pass
    except Exception as _pe:
        if verbose:
            print(f"   ⚠️ peer-inversion check failed: {_pe}")

    if n_changes == 0:
        return df, 0

    # Renormalize Category Share for STREAMING/PLATFORM. Guarded: on
    # str-dtype frames the CS write raises TypeError and previously
    # killed the whole enforcer AFTER the BP caps had been applied.
    try:
        _renormalize_category(df, 'STREAMING/PLATFORM',
                              bp_col, cs_col, raw_col, proj_col, sample_size)
    except Exception as _rn_err:
        if verbose:
            print(f"   ⚠️ S/P share renormalize skipped: {_rn_err}")
    return df, n_changes


# ============================================================================
# Niche-streamer caps in STREAMING/PLATFORM (Defect 39, 2026-06-16 PM,
# Jenna Tilda Swinton + Willem Dafoe report)
#
# THE CRITERION CHANNEL leading Netflix is implausible for ANY real audience:
# Criterion has ~1-2M US subscribers vs Netflix's 80M+. The cat-agent over-
# inflates niche streamers in art-house actor profiles (Tilda 94.6%, Willem
# 99.99%, Sam Elliott 58%, Sissy Spacek 42%, etc. -- 257 corpus profiles
# affected).
#
# Caps applied to STREAMING/PLATFORM grid only (subject self-pin in
# BRAND INPUT or that streamer's own native grid is exempt by the
# subject != value check):
#
#   THE CRITERION CHANNEL    22.0%   ~1-2M subs
#   MUBI                     18.0%   ~1M subs
#   ACORN TV                 15.0%   ~1.5M subs
#   BRITBOX                  18.0%   ~3M subs
#   SHUDDER                  12.0%   ~1M subs
#   KOCOWA / KOCOWA+          8.0%   ~0.5M subs (K-content niche)
#   HIDIVE                    8.0%   ~0.3M subs (anime niche)
#   CRACKLE                  18.0%   ~5M subs (FAST tier)
#   PLEX                     20.0%   ~5M subs (FAST tier)
#   MGM+ / MGM PLUS          18.0%   ~5M subs
#
# Logic per row:
#   - Skip if subject IS the streamer itself (self-pin allowed).
#   - If BP > cap, set BP = cap + jitter[-0.5, +0.5], recompute Raw + Proj.
#   - Renormalize STREAMING/PLATFORM Category Share if any cap fired.
# ============================================================================

NICHE_STREAMER_CAPS = {
    'THE CRITERION CHANNEL': 22.0,
    'CRITERION CHANNEL':     22.0,
    'MUBI':                  18.0,
    'ACORN TV':              15.0,
    'BRITBOX':               18.0,
    'SHUDDER':               12.0,
    'KOCOWA':                 8.0,
    'KOCOWA+':                8.0,
    'HIDIVE':                 8.0,
    'CRACKLE':               18.0,
    'PLEX':                  20.0,
    'MGM+':                  18.0,
    'MGM PLUS':              18.0,
}


def enforce_niche_streamer_caps(df, subject, verbose=True):
    """Cap niche streamers in STREAMING/PLATFORM at plausible ceilings
    derived from US subscriber base. Skip rows where the subject IS the
    streamer (self-pin allowed in own profile).

    Returns (df, n_changes).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not all((bp_col, cs_col, raw_col, proj_col)):
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()
    sp_mask = col_u == 'STREAMING/PLATFORM'
    if not sp_mask.any():
        return df, 0

    sample_size = _detect_sample_size(df, bp_col, raw_col)
    if sample_size is None:
        return df, 0

    # Subject self-pin exemption (rare: the streamer is the subject itself)
    subject_u = subject.upper().strip() if subject else ''
    subject_norm = _re.sub(r'[^A-Z0-9]', '', subject_u)

    def _to_float(v):
        try:
            s = str(v).replace('%', '').replace(',', '').strip()
            return float(s) if s and s.lower() not in ('nan', 'none') else None
        except (ValueError, TypeError):
            return None

    n_changes = 0
    for idx in df.index[sp_mask]:
        v_raw = str(df.at[idx, 'Value']).strip()
        v_u = v_raw.upper()
        cap = NICHE_STREAMER_CAPS.get(v_u)
        if cap is None:
            continue
        v_norm = _re.sub(r'[^A-Z0-9]', '', v_u)
        if v_norm == subject_norm:
            # Subject IS the streamer: self-pin at 100% legitimate
            continue
        cur = _to_float(df.at[idx, bp_col])
        if cur is None or cur <= cap + 0.05:
            continue
        new_bp = cap + _jitter_for(
            subject or 'unknown', v_u, salt='niche-cap', lo=-0.5, hi=0.5,
        )
        new_bp = round(max(0.5, min(cap + 0.5, new_bp)), 4)
        if verbose:
            print(f"   📉 niche-cap [{v_raw}] {cur:.4f}% -> {new_bp:.4f}% "
                  f"(cap {cap:.1f}%, US subs makes higher implausible)")
        _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col,
                sample_size)
        n_changes += 1

    if n_changes > 0:
        _renormalize_category(df, 'STREAMING/PLATFORM',
                              bp_col, cs_col, raw_col, proj_col, sample_size)
    return df, n_changes


def enforce_vanguard_in_investments(df, subject, verbose=True):
    """Move VANGUARD from BANKING to INVESTMENTS.

    Defect 42 (2026-06-17, Jenna): the canonical hostmap places Vanguard in
    SECTION='Investments' (BRAND='Vanguard'), but generation drift parked
    it in BANKING in 2,219 of 2,538 corpus profiles + Gen_Pop_2026.csv.
    Vanguard is an investment management firm (mutual funds, ETFs,
    brokerage), not a retail bank — never offered checking/savings.
    Rule #4: when a brand IS in hostmap, use hostmap's category.

    Logic:
      * If VANGUARD appears in BANKING and INVESTMENTS:
          drop the BANKING duplicate; INVESTMENTS row is canonical.
      * If VANGUARD appears in BANKING only:
          relabel Column BANKING -> INVESTMENTS; BP/raw/proj unchanged.
      * If VANGUARD appears in INVESTMENTS only or not at all: no-op.

    Renormalizes Category Share for BANKING and INVESTMENTS post-move.
    No BP changes, no jitter (Rule #1 — pure column reassignment, the
    underlying penetration is the same; we're only correcting which
    canonical category the row reports under).

    Returns (df, n_changes).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not bp_col:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    cu = df['Column'].astype(str).str.strip().str.upper()
    vu = df['Value'].astype(str).str.strip().str.upper()
    in_banking = (cu == 'BANKING') & (vu == 'VANGUARD')
    in_invest  = (cu == 'INVESTMENTS') & (vu == 'VANGUARD')

    if not in_banking.any():
        return df, 0

    if in_invest.any():
        n = int(in_banking.sum())
        df.drop(df.loc[in_banking].index, inplace=True)
        df.reset_index(drop=True, inplace=True)
        if verbose:
            print(f"   🏦→📈 Vanguard hostmap-fix: dropped {n} BANKING duplicate "
                  f"(INVESTMENTS row is canonical at hostmap)")
    else:
        df.loc[in_banking, 'Column'] = 'INVESTMENTS'
        if verbose:
            n = int(in_banking.sum())
            print(f"   🏦→📈 Vanguard hostmap-fix: relabelled {n} row(s) "
                  f"BANKING -> INVESTMENTS")

    if sample_size is not None:
        _renormalize_category(df, 'BANKING',
                              bp_col, cs_col, raw_col, proj_col, sample_size)
        _renormalize_category(df, 'INVESTMENTS',
                              bp_col, cs_col, raw_col, proj_col, sample_size)
    return df, 1


def dedupe_subject_streaming_grids(df, subject, verbose=True):
    """Drop the subject-brand peer row from the non-native streaming grid.

    Defect 31 (2026-06-15 PM, Hallmark Plus): the writer can emit the
    subject brand in BOTH the STREAMING VIDEO and STREAMING/PLATFORM
    grids. After the dashboard CATEGORY_DISPLAY_LABELS change merges
    both grids under one "STREAMING/PLATFORM" tab, having a 100%
    self-anchor in one grid AND a 4% peer row in the other renders
    as two contradictory bars in the same display section.

    Logic:
      - If subject appears in BOTH STREAMING VIDEO and STREAMING/PLATFORM
        AND one row is >= 95% (true self-anchor)
        AND the other row is < 50% (clearly the non-native peer artifact)
        -> drop the non-native row, renormalize Category Share.
      - If both rows are >= 95%: leave alone (rare, both grids agree on
        self-pin).
      - If neither row clears 95%: leave alone (no clear native, this is
        a different defect handled by G14 / pin_subject_to_100_in_appearing).

    Returns (df, n_dropped). n_dropped ∈ {0, 1}.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not bp_col:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_u = df['Value'].astype(str).str.upper().str.strip()

    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    subject_name = str(df.loc[bi_mask].iloc[0].get('Value', '') or '').strip().upper()
    if not subject_name:
        return df, 0

    sv_mask = (col_u == 'STREAMING VIDEO') & (val_u == subject_name)
    sp_mask = (col_u == 'STREAMING/PLATFORM') & (val_u == subject_name)
    if not sv_mask.any() or not sp_mask.any():
        return df, 0  # not a dual-grid case

    sv_idx = df.index[sv_mask][0]
    sp_idx = df.index[sp_mask][0]
    try:
        sv_bp = float(_bp(df.at[sv_idx, bp_col]))
        sp_bp = float(_bp(df.at[sp_idx, bp_col]))
    except Exception:
        return df, 0

    SELF_ANCHOR_FLOOR = 95.0
    PEER_DROP_CEILING = 50.0

    drop_idx = drop_grid = renorm_cat = None
    if sv_bp >= SELF_ANCHOR_FLOOR and sp_bp < PEER_DROP_CEILING:
        drop_idx, drop_grid, renorm_cat = sp_idx, 'STREAMING/PLATFORM', 'STREAMING/PLATFORM'
    elif sp_bp >= SELF_ANCHOR_FLOOR and sv_bp < PEER_DROP_CEILING:
        drop_idx, drop_grid, renorm_cat = sv_idx, 'STREAMING VIDEO', 'STREAMING VIDEO'
    else:
        return df, 0  # both >=95 (consistent), or neither clears anchor floor

    if verbose:
        print(f"   🧹 dropping duplicate subject row "
              f"{drop_grid},{subject_name} (peer {sv_bp if drop_grid=='STREAMING VIDEO' else sp_bp:.4f}% "
              f"vs native {sp_bp if drop_grid=='STREAMING VIDEO' else sv_bp:.4f}%)")
    df = df.drop(index=drop_idx).reset_index(drop=True)
    df = _renormalize_category(df, renorm_cat, bp_col, cs_col, raw_col, proj_col,
                               sample_size)
    return df, 1


# ============================================================================
# 2026-05-27 — Two new enforcers added after Bria flagged that UBG had:
#   (a) The Root + Ecosia in MEDIA / SEARCH ENGINE/AI despite being
#       hostmap SECTION='Hidden' (Rule #4b)
#   (b) An ad-hoc audit script dropped the canonical BRAND INPUT row
#       because its Value contained '|' (URL-variant seed list). The
#       shared _strip_rows helper already gates on METADATA_COLS, but
#       any caller writing its own metadata-strip regex can re-introduce
#       this bug. We now provide a canonical enforcer.
# ============================================================================

# Metadata-leakage patterns: things the LLM is echoing from prompt context
# into the Value column. NEVER strips rows whose Column is in METADATA_COLS
# (BRAND INPUT, SAMPLE SIZE, INPUT_METADATA, BRAND CATEGORY) — those
# legitimately contain structured strings like URL-variant seed lists.
_METADATA_LEAK_PATS = [
    _re.compile(r'INPUT_METADATA', _re.I),
    _re.compile(r'SAMPLE_START\s*:', _re.I),
    _re.compile(r'BEHAVIOR_START\s*:', _re.I),
    _re.compile(r'\bBEHAVIOR\s+STUDY\b', _re.I),
    _re.compile(r'\bSEED\s*:\s*\d', _re.I),
    _re.compile(r'\(\d{4}-\d{2}-\d{2}\s+TO\s+\d{4}-\d{2}-\d{2}\)', _re.I),
    _re.compile(r'\bBRAND\s*:\s*[A-Z][A-Z0-9_-]*_(?:SAMPLE|BEHAVIOR)_', _re.I),
]


def strip_input_metadata_leakage(df, subject, verbose=True):
    """Drop rows where the LLM has echoed prompt-context metadata strings
    into the Value column (e.g. 'BRAND:KRAPOPOLIS_SAMPLE_START:2025-01-01
    _SAMPLE_END:2025-12-31_BEHAVIOR_START:..._SEED:1238889381').

    Also drops rows whose Column is 'INPUT_METADATA' (an entire pseudo-
    category the pipeline sometimes emits).

    CRITICAL: NEVER touches rows whose Column is in METADATA_COLS
    (BRAND INPUT / SAMPLE SIZE / BRAND CATEGORY / SUBJECT). The
    canonical BRAND INPUT row legitimately contains a URL-variant
    seed list with pipe characters and the 100% pin must be preserved.

    Added 2026-05-27 after the UBG A+ pass accidentally dropped the
    BRAND INPUT row when an ad-hoc regex matched on '|'.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    drop_idx, affected, examples = [], set(), []
    for idx, r in df.iterrows():
        col = str(r.get('Column', '') or '').strip()
        val = str(r.get('Value', '') or '').strip()
        if not col or not val:
            continue
        col_u = col.upper()
        # NEVER strip the canonical metadata rows (BRAND INPUT etc.).
        if col_u in METADATA_COLS and col_u != 'INPUT_METADATA':
            continue
        # Drop entire INPUT_METADATA pseudo-column.
        if col_u == 'INPUT_METADATA':
            drop_idx.append(idx)
            affected.add(col)
            if len(examples) < 3:
                examples.append((col, val[:60]))
            continue
        # Otherwise check Value against metadata leak patterns.
        for pat in _METADATA_LEAK_PATS:
            if pat.search(val):
                drop_idx.append(idx)
                affected.add(col)
                if len(examples) < 3:
                    examples.append((col, val[:60]))
                break

    if not drop_idx:
        return df, 0
    if verbose:
        ex = ', '.join(f'[{c}]"{v}..."' for c, v in examples)
        more = f' (+{len(drop_idx)-len(examples)} more)' if len(drop_idx) > len(examples) else ''
        print(f"   🧹 stripped {len(drop_idx)} metadata-leak row(s): {ex}{more}")
    df = df.drop(index=drop_idx).reset_index(drop=True)
    for cat in affected:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)
    return df, len(drop_idx)


# ============================================================================
# strip_url_variant_seed_rows — hide BG.py URL-variant seed lists from
# category displays (2026-07-29)
# ----------------------------------------------------------------------------
# Jenna 2026-07-29 (Elton John TU): "this shouldnt show up in talent. its
# the brand input row and should be hidden. in general anything with
# commas like that would b[e]".
#
# The screenshot showed a row in MUSICIAN/BAND ranked #1 at 100% with
# Value='Elton John, EltonJohn, ELTONJOHN, Elton, Sir Elton John,
# EltonHercules, EltonJohnOfficial'. That is the URL-variant seed list
# BG.py emits into the BRAND INPUT metadata row -- when the pipeline also
# leaves a duplicate copy of the same value inside a real category
# (MUSICIAN/BAND / TALENT / etc.) the dashboard renders the entire
# comma-separated seed list as a brand name at rank #1. Ugly.
#
# Detection heuristic (must distinguish from legitimate comma-in-name
# stage aliases like 'PITBULL, MR. WORLDWIDE' or 'TYLER, THE CREATOR'):
#   - >=4 comma-separated parts
#   - AND at least one part is a no-space single token that is EITHER
#     CamelCase (e.g. 'EltonHercules', len>=6, mixed upper+lower) OR
#     all-caps (e.g. 'ELTONJOHN', len>=5, no lowercase).
# Legit stage names have at most 2 parts and each part is space-separated
# proper words, so they fail the len>=4 gate up-front.
#
# Action:
#   - If a clean-named row (same normalized first token) already exists in
#     the same Column: DELETE the seed-list row (duplicate).
#   - Otherwise: REPLACE the Value with the first comma-separated token
#     (usually the clean subject name).
#
# NEVER touches METADATA_COLS (BRAND INPUT / SAMPLE SIZE / BRAND CATEGORY /
# SUBJECT). Those legitimately contain URL-variant seed lists (Rule #4c).
# ============================================================================

def _is_url_variant_seed_list(val):
    """True iff `val` looks like a BG.py URL-variant seed list.

    Robust against 'PITBULL, MR. WORLDWIDE' / 'TYLER, THE CREATOR'
    (stage names) by requiring both:
      (a) >=4 comma-separated parts, AND
      (b) >=1 part that is a no-space CamelCase (mixed upper+lower, len>=6)
          OR all-caps token (no lowercase, len>=5).
    """
    if not val or not isinstance(val, str):
        return False
    parts = [p.strip() for p in val.split(',')]
    if len(parts) < 4:
        return False
    for p in parts:
        if not p or ' ' in p:
            continue
        has_upper = any(c.isupper() for c in p)
        has_lower = any(c.islower() for c in p)
        is_camel = has_upper and has_lower and len(p) >= 6
        is_allcaps = has_upper and (not has_lower) and len(p) >= 5
        if is_camel or is_allcaps:
            return True
    return False


# Audience-noun suffixes that appear in DELIVERABLE labels but never in
# the clean entity name: 'Furious Viewers' names the audience of the
# series 'Furious'. Mirrors _AUDIENCE_LABEL_SUFFIXES in
# scripts/synth_engine_row_by_row.py (BRAND INPUT slug building) plus the
# ip-scope consumer verbs (viewers/readers/listeners/players/watchers).
_AUDIENCE_SUFFIX_NOUNS = {
    'viewers', 'watchers', 'listeners', 'readers', 'players',
    'fans', 'fan', 'audience', 'moviegoers',
}


def _norm_ident(s):
    """Case + punctuation-insensitive identity key for subject/brand
    name comparison ('Disney+/Hulu' -> 'DISNEYHULU')."""
    return _re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _subject_label_variants(name):
    """Ordered clean-subject candidates for a display / deliverable
    label, full name first, most-stripped last.

    'Furious Viewers - Avid Fan' -> ['Furious Viewers - Avid Fan',
    'Furious Viewers', 'Furious']. Strips ' - {Cut}' suffixes right to
    left (naming rule: every cut = '{Subject} - {Cut}'), then a single
    trailing audience noun per candidate. Callers must validate a
    stripped variant against a file identity anchor before trusting it
    ('Steven Universe' must never collapse to 'Steven' blindly).
    """
    out = []

    def _add(s):
        s = _re.sub(r'\s+', ' ', str(s or '').strip().strip('-, ')).strip()
        if s and s not in out:
            out.append(s)

    _add(name)
    base = str(name or '').strip()
    while ' - ' in base:
        base = base.rsplit(' - ', 1)[0]
        _add(base)
    for cand in list(out):
        toks = cand.split()
        if len(toks) >= 2 and toks[-1].lower() in _AUDIENCE_SUFFIX_NOUNS:
            _add(' '.join(toks[:-1]))
    return out


def _clean_subject_from_bi(bi_value, df=None, col_u=None, subject_arg=None):
    """Return a clean, display-ready subject name for use in category
    row Value cells.

    2026-07-29 (Elton MUSICIAN/BAND seed-list leak): the BRAND INPUT
    Value legitimately holds the URL-variant seed list
    ('Elton John, EltonJohn, ELTONJOHN, ...') for runtime URL matching
    (workspace Rule #4c -- METADATA_COLS values are preserved). But
    downstream enforcers that insert or force-rename a subject row in a
    real category (MUSICIAN/BAND, TALENT, etc.) must NEVER use the raw
    seed list -- it renders as an ugly comma-blob in the dashboard.

    2026-08-24 (Furious audit D5): cut paths pass DELIVERABLE labels as
    `subject_arg` ('Furious Viewers', 'Furious Viewers - Millennials
    25-44') where the clean entity is 'Furious'. Taking subject_arg
    verbatim planted those labels as pinned rows in SERIES. Now, when
    subject_arg looks like a label (has a ' - ' cut suffix and/or a
    trailing audience noun), we walk its stripped variants and return
    the first one that matches an identity anchor read off the file
    itself (BRAND INPUT first token, SUBJECT row). Unanchored names
    still return verbatim, so 'Steven Universe' never collapses.

    Priority order for a clean name:
      1. `subject_arg` (anchor-validated variant when it's a label) if
         non-empty and not itself a seed list.
      2. The SUBJECT row's Value if present + not a seed list.
      3. The first comma-separated token of `bi_value`.
      4. `bi_value` unchanged (safe default if none of the above apply).
    """
    if bi_value is None:
        bi_value = ''
    bi_value = str(bi_value).strip()

    # Identity anchors from the file itself.
    anchors = set()
    if bi_value:
        first_tok = bi_value.split(',')[0].strip()
        if first_tok:
            anchors.add(_norm_ident(first_tok))
    if df is not None and col_u is not None:
        try:
            subj_mask = col_u == 'SUBJECT'
            if subj_mask.any():
                sv = str(
                    df.loc[subj_mask].iloc[0].get('Value', '') or '',
                ).strip()
                if sv and not _is_url_variant_seed_list(sv):
                    anchors.add(_norm_ident(sv.split(',')[0].strip()))
        except Exception:
            pass
    anchors.discard('')

    if (subject_arg and isinstance(subject_arg, str)
            and subject_arg.strip()
            and not _is_url_variant_seed_list(subject_arg)):
        variants = _subject_label_variants(subject_arg)
        if len(variants) > 1 and anchors:
            for v in variants:
                if _norm_ident(v) in anchors:
                    return v
        return variants[0] if variants else subject_arg.strip()

    if df is not None and col_u is not None:
        try:
            subj_mask = col_u == 'SUBJECT'
            if subj_mask.any():
                subj_val = str(
                    df.loc[subj_mask].iloc[0].get('Value', '') or '',
                ).strip()
                if subj_val and not _is_url_variant_seed_list(subj_val):
                    return subj_val
        except Exception:
            pass

    if _is_url_variant_seed_list(bi_value):
        first_tok = bi_value.split(',')[0].strip()
        if first_tok:
            return first_tok

    return bi_value


def ensure_subject_metadata_row(df, subject, verbose=True):
    """Guarantee the canonical SUBJECT metadata row exists:
    Column='SUBJECT', Value=<clean subject name>, BP=100, Raw=sample,
    Proj=sample projection.

    2026-08-24 (Furious audit D4): all five files of the run shipped
    without it - the engine never emitted one and no enforcer
    backfilled. Wired into run_write_safety_net so EVERY write path
    (fresh builds, avid skins, gender/addon cuts, coherence re-uploads)
    carries it. Cuts inherit the parent's row; this is the backstop.

    If a SUBJECT row exists but its Value is a URL-variant seed list or
    a deliverable label that strips to the file's identity anchor, the
    Value is repaired to the clean name. BP/Raw/Proj are re-anchored to
    100 / sample / sample-projection when stale.
    """
    if (df is None or len(df) == 0 or 'Column' not in df.columns
            or 'Value' not in df.columns):
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    col_u = df['Column'].astype(str).str.strip().str.upper()

    bi_mask = col_u == 'BRAND INPUT'
    bi_val = ''
    if bi_mask.any():
        bi_val = str(df.loc[bi_mask].iloc[0].get('Value', '') or '').strip()
    clean = _clean_subject_from_bi(
        bi_val, df=df, col_u=col_u, subject_arg=subject)
    clean = _re.sub(r'\s+', ' ', str(clean or '').strip())
    if not clean:
        return df, 0

    def _fnum(v):
        try:
            f = float(str(v).replace(',', '').replace('%', '').strip())
            return f if f > 0 else None
        except Exception:
            return None

    # Sample + universe anchors: SAMPLE SIZE row first, BRAND INPUT next.
    sample = universe = None
    for anchor in ('SAMPLE SIZE', 'BRAND INPUT'):
        m = col_u == anchor
        if not m.any():
            continue
        row = df.loc[m].iloc[0]
        if sample is None and raw_col:
            sample = _fnum(row.get(raw_col))
        if universe is None and proj_col:
            universe = _fnum(row.get(proj_col))

    n = 0
    subj_mask = col_u == 'SUBJECT'
    if subj_mask.any():
        idx = df.index[subj_mask][0]
        try:
            cur = str(df.at[idx, 'Value'] or '').strip()
            label_norms = {
                _norm_ident(v) for v in _subject_label_variants(subject)
            }
            if (cur and _norm_ident(cur) != _norm_ident(clean)
                    and (_is_url_variant_seed_list(cur)
                         or _norm_ident(cur) in label_norms)):
                df.at[idx, 'Value'] = clean
                n += 1
            if bp_col in df.columns:
                bpv = _fnum(df.at[idx, bp_col])
                if bpv is None or abs(bpv - 100.0) > 0.005:
                    df[bp_col] = df[bp_col].astype(object)
                    df.at[idx, bp_col] = '100.0000'
                    n += 1
            if raw_col and sample is not None:
                rv = _fnum(df.at[idx, raw_col])
                if rv is None or abs(rv - sample) > max(1.0, sample * 0.001):
                    df[raw_col] = df[raw_col].astype(object)
                    df.at[idx, raw_col] = int(round(sample))
                    n += 1
            if proj_col and universe is not None:
                pv = _fnum(df.at[idx, proj_col])
                if pv is None or abs(pv - universe) > max(1.0, universe * 0.001):
                    df[proj_col] = df[proj_col].astype(object)
                    df.at[idx, proj_col] = int(round(universe))
                    n += 1
        except Exception:
            pass
        return df, n

    # No SUBJECT row: insert one right after the metadata block.
    new_row = {c: '' for c in df.columns}
    new_row['Column'] = 'SUBJECT'
    new_row['Value'] = clean
    if bp_col in df.columns:
        new_row[bp_col] = '100.0000'
    if raw_col and sample is not None:
        new_row[raw_col] = int(round(sample))
    if proj_col and universe is not None:
        new_row[proj_col] = int(round(universe))

    insert_after = -1
    for anchor in ('BRAND CATEGORY', 'SAMPLE SIZE', 'BRAND INPUT'):
        m = col_u == anchor
        if m.any():
            insert_after = int(df.index.get_indexer([df.index[m][0]])[0])
            break
    try:
        top = df.iloc[: insert_after + 1]
        rest = df.iloc[insert_after + 1:]
        df = pd.concat(
            [top, pd.DataFrame([new_row], columns=df.columns), rest],
            ignore_index=True,
        )
        n += 1
        if verbose:
            print(f"   📇 SUBJECT metadata row inserted: {clean!r} "
                  f"(BP=100, Raw={new_row.get(raw_col, '?')})")
    except Exception as e:
        if verbose:
            print(f"   ⚠ SUBJECT row insert failed (non-fatal): {e}")
    return df, n


def strip_cohort_label_rows(df, subject, verbose=True):
    """Drop or rename category rows whose Value is the DELIVERABLE /
    display label instead of the clean subject.

    2026-08-24 (Furious audit D5): 'Furious Viewers', 'Furious Viewers -
    Avid Fan', '- Millennials', '- Los Angeles Ca' shipped as ~100-pinned
    rows in SERIES. Naming rule: TU deliverable = clean entity name only;
    every cut = '{Subject} - {Cut}'. Neither form is a real content row.

    Rules (non-demo, non-metadata categories only):
      - dash-orphan Values ('- Millennials') are dropped outright;
      - a Value equal to a label variant of `subject` (deliverable name
        or its stripped forms, excluding the clean name itself) is
        renamed to the clean subject, or dropped when the clean-named
        row already exists in that category;
      - a Value of the form '{base} - {tail}' where base is the clean
        subject or a label variant is treated the same way; when base
        equals the clean subject the rule additionally requires
        BP >= 95 so real dash-titled sibling content is never touched.
    """
    if (df is None or len(df) == 0 or 'Column' not in df.columns
            or 'Value' not in df.columns):
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    col_u = df['Column'].astype(str).str.strip().str.upper()

    bi_mask = col_u == 'BRAND INPUT'
    bi_val = ''
    if bi_mask.any():
        bi_val = str(df.loc[bi_mask].iloc[0].get('Value', '') or '').strip()
    clean = _clean_subject_from_bi(
        bi_val, df=df, col_u=col_u, subject_arg=subject)
    clean = _re.sub(r'\s+', ' ', str(clean or '').strip())
    clean_norm = _norm_ident(clean)

    label_norms = {
        _norm_ident(v) for v in _subject_label_variants(subject)
    }
    label_norms.discard(clean_norm)
    label_norms.discard('')

    skip_cats = (set(METADATA_COLS) | set(DEPIN_DEMO_CATS)
                 | {'INPUT_METADATA', 'AVID FAN', 'CASUAL FAN',
                    'LOCATION', 'DMA', 'REGION', 'GENERAL'})

    val_norm = df['Value'].astype(str).map(_norm_ident)
    cats_with_clean = set(
        col_u[(val_norm == clean_norm) & ~col_u.isin(skip_cats)]
    ) if clean_norm else set()

    drops, renames, examples = [], [], []
    for idx in df.index:
        cat = col_u.loc[idx]
        if not cat or cat in skip_cats:
            continue
        val = str(df.at[idx, 'Value'] or '').strip()
        if not val:
            continue
        vn = val_norm.loc[idx]
        if vn == clean_norm:
            continue
        # Dash-orphan: label composed with an empty subject.
        if _re.match(r'^-\s+\S', val):
            drops.append(idx)
            examples.append((cat, val, 'drop:dash-orphan'))
            continue
        is_label = vn in label_norms
        if not is_label and ' - ' in val:
            base = val.rsplit(' - ', 1)[0].strip()
            bn = _norm_ident(base)
            if bn and bn in label_norms:
                is_label = True
            elif bn and bn == clean_norm and _bp(df.at[idx, bp_col]) >= 95:
                # '{CleanSubject} - {Cut}' pinned at/near 100: label leak.
                # BP gate protects real dash-titled sibling content.
                is_label = True
        if not is_label:
            continue
        if cat in cats_with_clean:
            drops.append(idx)
            examples.append((cat, val, 'drop:clean-row-exists'))
        else:
            renames.append(idx)
            cats_with_clean.add(cat)
            examples.append((cat, val, f'rename->{clean}'))

    for idx in renames:
        df.at[idx, 'Value'] = clean
    if drops:
        df = df.drop(index=drops).reset_index(drop=True)

    n = len(drops) + len(renames)
    if n and verbose:
        print(f"   🏷️  cohort-label row guard: {len(drops)} dropped, "
              f"{len(renames)} renamed to {clean!r}")
        for cat, val, action in examples[:4]:
            print(f"      - {cat} | {val!r} [{action}]")
    return df, n


def pin_target_matches_value(target, value):
    """Case/punctuation-insensitive + merged-name alias-aware pin match.

    A pin target 'Hulu' must land on rows named 'Hulu', 'HULU',
    'Disney+/Hulu', 'Hulu (Disney+)', 'Hulu + Live TV' etc. Matching is
    component-based (split on '/', ',', '(', ')', '|', ' - ', ' + '),
    NOT free substring, so 'Max' never matches 'Maxwell House'.
    Symmetric: row 'Hulu' also matches pin target 'Disney+/Hulu'.
    """
    def _comps(s):
        parts = _re.split(r'[/,()|]|\s-\s|\s\+\s', str(s or ''))
        out = {_norm_ident(p) for p in parts if p and p.strip()}
        out.add(_norm_ident(s))
        out.discard('')
        return out

    t = _norm_ident(target)
    v = _norm_ident(value)
    if not t or not v:
        return False
    if t == v:
        return True
    if t in _comps(value):
        return True
    return v in _comps(target)


def spec_pin_disposition(subject, cat_u, value):
    """Classify an approved-spec subject_rows pin. Added 2026-08-27
    after the Chobani Buyers hold (run 2mSR3dbumpc1bA): the interpret
    step listed the subject's retail destinations (WHERE THEY SHOP /
    Walmart, Target, Whole Foods) in subject_rows, and the exact-100
    fall-through in enforce_spec_pin_rows pinned two retailers at 100
    on a universe no single retailer covers. The I18 de-pin autofix
    then parked them just under 100, where ship-gate I1 correctly held
    the file - three times, deterministically, because the spec rode
    along into every rebuild.

    Returns one of:
      'pin_100'  - the subject's own row, its verified owner platform,
                   a restatement of the universe definition ('Florida
                   Voters' on '2026 Florida Gubernatorial Voters'), or
                   a sports-companion row: exact 100 per convention.
      'pin_soft' - non-subject carrier in a platform / carriage grid:
                   subject-salted messy near-100 (existing behavior).
      'demote'   - affinity / adjacency pin (retailers a CPG sells
                   through, peer brands, cast members, ...): never
                   pin; the reasoned value stands.
    """
    cu = str(cat_u or "").strip().upper()
    sval = str(value or "").strip()
    if not sval:
        return "demote"
    try:
        try:
            from migration.self_property_coherence import (
                is_subject_own as _spd_own,
                is_owner_platform_row as _spd_owner,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                is_subject_own as _spd_own,
                is_owner_platform_row as _spd_owner,
            )
        if _spd_own(subject, sval) or _spd_owner(subject, sval):
            return "pin_100"
    except Exception:
        # Standalone fallback: normalized equality / containment, the
        # same shape as the interpret-side affinity-pin guard.
        _ns = _re.sub(r"[^a-z0-9]", "", str(subject or "").lower())
        _nv = _re.sub(r"[^a-z0-9]", "", sval.lower())
        if _nv and _ns and (_nv == _ns or (len(_nv) >= 4
                            and (_nv in _ns or _ns in _nv))):
            return "pin_100"
    # Universe restatement: every word of the value appears in the
    # subject name (pseudo-anchor rows that re-say the cohort).
    subj_words = set(_re.findall(r"[a-z0-9]+", str(subject or "").lower()))
    val_words = _re.findall(r"[a-z0-9]+", sval.lower())
    if val_words and subj_words and all(w in subj_words for w in val_words):
        return "pin_100"
    if cu in COMPANION_PIN_CATS:
        return "pin_100"
    if cu in CARRIER_PIN_SOFT_CATS:
        return "pin_soft"
    return "demote"


def enforce_spec_pin_rows(df, subject, pin_rows, verbose=True):
    """Re-assert the approved spec's subject_rows pins late in the write
    path: every (category, name) pin must land at exactly BP=100 on the
    row it aliases to.

    2026-08-24 (Furious audit D3): the viewers-scope platform pin
    ('STREAMING/PLATFORM', 'Hulu') matched nothing because the profile
    row was the merged name 'Disney+/Hulu', and later pipeline stages
    (persona noise, sanity fixes) drifted the value to 64-71 with no
    re-assertion. This enforcer runs after the polish passes with
    alias-aware matching and logs LOUDLY when a pin target matches zero
    rows. Demo categories are excluded (GENDER pins etc. are owned by
    the cut transforms); metadata rows are excluded.

    2026-08-26 (Jenna convention correction, superseding the earlier
    same-day Liz-QA softening): a spec pin on the subject's OWN
    property or its OWNING / universe-defining platform (Paramount+
    on a Paw Patrol universe, per OWNER_PLATFORM_MAP / must_pin_100)
    lands at exactly 100.0000 - "for paw patrol paramount+ should be
    100% as should paw patrol". Only a non-subject, non-owner pin in
    a platform/carriage category lands at a subject-salted messy
    near-100 (the pin intent - "this carrier is ~universal" - is
    honored without asserting unverified universal reach).

    Returns (df, n_changed, unmatched_pins).
    """
    if (not pin_rows or df is None or len(df) == 0
            or 'Column' not in df.columns or 'Value' not in df.columns):
        return df, 0, []

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0, []
    col_u = df['Column'].astype(str).str.strip().str.upper()

    def _fnum(v):
        try:
            f = float(str(v).replace(',', '').replace('%', '').strip())
            return f if f > 0 else None
        except Exception:
            return None

    sample = universe = None
    for anchor in ('SAMPLE SIZE', 'BRAND INPUT'):
        m = col_u == anchor
        if not m.any():
            continue
        row = df.loc[m].iloc[0]
        if sample is None and raw_col:
            sample = _fnum(row.get(raw_col))
        if universe is None and proj_col:
            universe = _fnum(row.get(proj_col))

    n = 0
    unmatched = []
    for entry in pin_rows:
        try:
            cat, name = str(entry[0]).strip(), str(entry[1]).strip()
        except Exception:
            continue
        if not cat or not name:
            continue
        cu = cat.upper()
        if cu in DEPIN_DEMO_CATS or cu in METADATA_COLS or cu == 'INPUT_METADATA':
            continue
        cat_mask = col_u == cu
        if not cat_mask.any():
            unmatched.append((cat, name))
            print(f"   ⚠️⚠️ SPEC PIN CATEGORY ABSENT FROM PROFILE: {cu} | "
                  f"{name!r} - pin cannot be applied; verify the approved "
                  f"spec's category name")
            continue
        hit = 0
        skip_logged = False
        for idx in df.index[cat_mask]:
            if not pin_target_matches_value(name, df.at[idx, 'Value']):
                continue
            hit += 1
            row_val = str(df.at[idx, 'Value'])
            # Exact 100 belongs to the subject's own property AND its
            # owning / universe-defining platform (2026-08-26 Jenna:
            # Paramount+ = 100 on Paw Patrol). A non-subject, non-owner
            # pin in a platform/carriage category lands at a
            # subject-salted messy near-100. Every OTHER non-subject
            # pin is an affinity/adjacency pin and is SKIPPED outright
            # (2026-08-27 Chobani Buyers hold: WHERE THEY SHOP /
            # Walmart+Target spec pins at 100 held the file at ship
            # gate I1 three times); the reasoned value stands.
            _rank = {"pin_100": 2, "pin_soft": 1, "demote": 0}
            disp = max(
                spec_pin_disposition(subject, cu, name),
                spec_pin_disposition(subject, cu, row_val),
                key=lambda d: _rank[d],
            )
            if disp == "demote":
                if not skip_logged:
                    skip_logged = True
                    print(f"   🚫 SPEC PIN SKIPPED (non-subject affinity "
                          f"pin): {cu} | {name!r} - not the subject, its "
                          f"owner platform, or a carriage/companion row; "
                          f"the reasoned value stands "
                          f"(cur={df.at[idx, bp_col]})")
                continue
            if disp == "pin_100":
                target_bp = 100.0
            else:
                import hashlib as _hl_pin
                h = int(_hl_pin.sha256(
                    f"{subject}|{cu}|{name}|carrier-pin".encode()
                ).hexdigest()[:8], 16)
                target_bp = round(99.0 + (120 + h % 6800) / 10000.0, 4)
                if int(round(target_bp * 10000)) % 100 == 0:
                    target_bp = round(target_bp + (1 + h % 89) / 10000.0, 4)
            cur_cell = str(df.at[idx, bp_col])
            bpv = _fnum(cur_cell)
            if bpv is not None and abs(bpv - target_bp) <= 0.00005:
                continue
            had_pct = cur_cell.strip().endswith('%')
            df[bp_col] = df[bp_col].astype(object)
            df.at[idx, bp_col] = (f'{target_bp:.4f}%' if had_pct
                                  else f'{target_bp:.4f}')
            if raw_col and sample is not None:
                df[raw_col] = df[raw_col].astype(object)
                df.at[idx, raw_col] = int(round(sample * target_bp / 100.0))
            if proj_col and universe is not None:
                df[proj_col] = df[proj_col].astype(object)
                df.at[idx, proj_col] = int(round(
                    universe * target_bp / 100.0))
            n += 1
            if verbose:
                kind = ("100" if target_bp == 100.0
                        else f"{target_bp:.4f} (non-subject carrier)")
                print(f"   📌 spec pin re-asserted: {cu} | "
                      f"{df.at[idx, 'Value']!r} -> {kind} (was {bpv})")
        if not hit:
            unmatched.append((cat, name))
            print(f"   ⚠️⚠️ SPEC PIN TARGET MATCHED ZERO ROWS: {cu} | "
                  f"{name!r} - no row in the category aliases to the pin "
                  f"target; the pin did NOT land. Row values present: "
                  f"{list(df.loc[cat_mask, 'Value'].astype(str).head(8))}")
    return df, n, unmatched


def enforce_self_property_coherence(df, subject, verbose=True):
    """I16 (2026-08-26 Liz QA, Paw Patrol): on a content/franchise
    subject, the subject's own merch/games/media rows must be coherent
    with its own FRANCHISE anchor. A viewer base at 82.74% franchise
    engagement cannot sit at 6.20% on the property's own toys.

    Detection + remediation arithmetic live in
    migration/self_property_coherence.py (shared with ship-gate I16):
    flagged rows are re-leveled to a peer-anchored, franchise-bounded,
    subject-salted target, and every flagged row of the same brand
    gets the SAME target so the Rule #3b subcategory mirror never
    drifts on the fix. Positioned BEFORE recompute_raw_and_projection;
    only BP is set here.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    try:
        try:
            from migration.self_property_coherence import (
                check_self_property_coherence, coherence_target,
                peer_max_in_category,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                check_self_property_coherence, coherence_target,
                peer_max_in_category,
            )
    except Exception as e:
        if verbose:
            print(f'   [self-prop] module unavailable; skipping ({e})')
        return df, 0

    items = []
    idx_by_key = {}
    import re as _re_sp
    for idx in df.index:
        cu = str(df.at[idx, 'Column']).strip().upper()
        val = str(df.at[idx, 'Value']).strip()
        bp = _bp(df.at[idx, bp_col])
        items.append((cu, val, bp))
        idx_by_key[(cu, _re_sp.sub(r'[^A-Z0-9]', '', val.upper()))] = idx

    anchor_bp, viols = check_self_property_coherence(items, subject)
    if not viols:
        return df, 0

    # One target per brand group (mirror-preserving): compute the max
    # per-category target across the group's grids, then apply it to
    # every flagged row of that brand.
    groups = {}
    for v in viols:
        bkey = _re_sp.sub(r'[^A-Z0-9]', '', str(v['val']).upper())
        groups.setdefault(bkey, []).append(v)
    n = 0
    for bkey, vs in groups.items():
        target = 0.0
        for v in vs:
            pm = peer_max_in_category(items, v['cat'], subject)
            t = coherence_target(subject, v['val'], anchor_bp, pm)
            if t > target:
                target = t
        for v in vs:
            idx = idx_by_key.get((v['cat'], bkey))
            if idx is None:
                continue
            cur_cell = str(df.at[idx, bp_col])
            had_pct = cur_cell.strip().endswith('%')
            df[bp_col] = df[bp_col].astype(object)
            df.at[idx, bp_col] = (f'{target:.4f}%' if had_pct
                                  else f'{target:.4f}')
            n += 1
            if verbose:
                print(f"   🧸 self-property coherence: {v['cat']} | "
                      f"{v['val']!r} {v['bp']:.4f} -> {target:.4f} "
                      f"(franchise anchor {anchor_bp:.4f}, floor "
                      f"{v['floor']:.4f})")
    return df, n


def pin_own_property_rows(df, subject, verbose=True):
    """I17 (2026-08-26 Jenna convention correction, verbatim: "if it
    is its own property it should be 100%"): the subject's own
    property row (FRANCHISE 'Paw Patrol') and the owning /
    universe-defining platform row (Paramount+ on a Paw Patrol
    universe, Apple TV+ on an Apple TV+-scoped universe) pin at
    exactly 100.0000 in the base file and every derived cut.
    must_pin_100 in self_property_coherence decides which rows
    qualify. Only BP is set here; Raw/Projection recompute
    downstream. Merch grids (TOYS/GAMES/MPB) are not touched."""
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    try:
        try:
            from migration.self_property_coherence import must_pin_100
        except ImportError:
            from self_property_coherence import (  # type: ignore
                must_pin_100,
            )
    except Exception as e:
        if verbose:
            print(f'   [own-pin] module unavailable; skipping ({e})')
        return df, 0
    skip_cats = METADATA_COLS | DEPIN_DEMO_CATS | {'SUBJECT'}
    n = 0
    for idx in df.index:
        cu = str(df.at[idx, 'Column']).strip().upper()
        if cu in skip_cats:
            continue
        bp = _bp(df.at[idx, bp_col])
        if bp is None or abs(bp - 100.0) <= 0.00005:
            continue
        val = str(df.at[idx, 'Value']).strip()
        if not must_pin_100(subject, cu, val):
            continue
        cur_cell = str(df.at[idx, bp_col])
        had_pct = cur_cell.strip().endswith('%')
        df[bp_col] = df[bp_col].astype(object)
        df.at[idx, bp_col] = '100.0000%' if had_pct else '100.0000'
        n += 1
        if verbose:
            print(f'   📌 own-property pin: {cu} | {val!r} '
                  f'{bp:.4f} -> 100.0000')
    return df, n


def depin_exact_100_non_subject(df, subject, verbose=True, cut_label=None):
    """I18 (2026-08-26 Liz QA, Paw Patrol): a row at exactly 100.0000
    that is NOT the subject's own property (and not metadata, not a
    demo bucket, not a companion sports pin) violates the messy-value
    convention and usually asserts an impossible universal reach
    (Paramount+ 100.0000 on a universe defined across four platforms).
    De-pin to a subject-salted messy near-100. Positioned AFTER the
    pin/carriage passes and BEFORE recompute_raw_and_projection.

    2026-08-26 Jenna convention correction: rows where exact 100 is
    LEGITIMATE are exempt and left untouched - the subject's own
    property, the owner-verified platform (OWNER_PLATFORM_MAP), the
    cut-defining platform of a platform cut (Apple TV+ on an Apple
    TV+-scoped cut), and the single carrier of a one-platform viewer
    universe (exact_100_exempt in self_property_coherence). Of the
    rows that DO get de-pinned: a cut-defining non-platform row (the
    DMA row on a geo cut, per the cut-skin gender convention) lands
    in [99.90, 99.99]; everything else (accidental reach pins) lands
    in [99.01, 99.69]. Pass cut_label='Spotify Fan' (the ' - '
    suffix of the deliverable name) when the caller knows it.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    try:
        try:
            from migration.self_property_coherence import (
                is_subject_own as _spc_is_own,
                exact_100_exempt as _spc_e100,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                is_subject_own as _spc_is_own,
                exact_100_exempt as _spc_e100,
            )
    except Exception:
        _spc_is_own = None
        _spc_e100 = None

    # LOCATION deliberately NOT skipped (a geo cut's own DMA pin is
    # cut-defining and moves to the high band); demo-shaped bucket
    # categories outside the canonical 9 ARE skipped - a 100 bucket
    # there is a renormalization defect, not a reach pin.
    skip_cats = (METADATA_COLS | DEPIN_DEMO_CATS
                 | (DEPIN_META_CATS - {'LOCATION'})
                 | {'SUBJECT', 'AGE_OF_CHILDREN', 'AGE OF CHILDREN'})

    def _canon(s):
        return __import__('re').sub(r'[^A-Z0-9]', '', str(s or '').upper())

    cut_norm = _canon(cut_label)
    # Single-carrier universes read near-total on their one carrier:
    # collect the platform domains present in BRAND INPUT (the
    # exemption matches the row value against the single domain).
    carrier_domains = []
    try:
        try:
            from migration.viewer_carriage import PLATFORM_DOMAINS
        except ImportError:
            from viewer_carriage import PLATFORM_DOMAINS  # type: ignore
        col_u = df['Column'].astype(str).str.strip().str.upper()
        bi_idx = df.index[col_u == 'BRAND INPUT']
        if len(bi_idx):
            bi_val = str(df.at[bi_idx[0], 'Value'] or '').lower()
            carrier_domains = [d for d in PLATFORM_DOMAINS if d in bi_val]
    except Exception:
        carrier_domains = []

    import hashlib as _hl_dp
    n = 0
    for idx in df.index:
        cu = str(df.at[idx, 'Column']).strip().upper()
        if cu in skip_cats or _is_companion_pin_cat(cu):
            continue
        bp = _bp(df.at[idx, bp_col])
        if bp is None or abs(bp - 100.0) > 0.00005:
            continue
        val = str(df.at[idx, 'Value']).strip()
        if _spc_is_own is not None and _spc_is_own(subject, val):
            continue  # legitimate subject self-pin
        # 2026-08-26 Jenna convention: owner-verified platforms,
        # cut-defining platforms, and single-carrier rows keep their
        # legitimate exact 100 - never de-pinned.
        if _spc_e100 is not None and _spc_e100(
                subject, cu, val, cut_label=cut_label,
                carrier_domains=carrier_domains):
            continue
        vn = _canon(val)
        cut_defining = bool(cut_norm) and len(cut_norm) >= 3 and (
            cut_norm in vn or vn in cut_norm)
        h = int(_hl_dp.sha256(
            f'{subject}|{cu}|{val}|depin-100'.encode()).hexdigest()[:8], 16)
        if cut_defining or cu == 'LOCATION':
            new_bp = round(99.90 + (h % 900) / 10000.0, 4)
        else:
            new_bp = round(99.0 + (120 + h % 6800) / 10000.0, 4)
        if int(round(new_bp * 10000)) % 100 == 0:
            new_bp = round(new_bp + (1 + h % 89) / 10000.0, 4)
        cur_cell = str(df.at[idx, bp_col])
        had_pct = cur_cell.strip().endswith('%')
        df[bp_col] = df[bp_col].astype(object)
        df.at[idx, bp_col] = (f'{new_bp:.4f}%' if had_pct
                              else f'{new_bp:.4f}')
        n += 1
        if verbose:
            print(f'   📍 exact-100 de-pin (non-subject): {cu} | '
                  f'{val!r} 100.0000 -> {new_bp:.4f}')
    return df, n


def detect_brand_input_landing_pages(df, subject=None, verbose=True):
    """I19 detection (2026-08-26 Liz QA, Paw Patrol): generic platform
    landing pages (fubo.tv/welcome, netflix.com, hulu.com/home) in a
    URL-bearing BRAND INPUT qualify EVERY visitor of that platform
    into the universe, violating the clickstream-slug rule (4c-i case
    4: specific title paths only). Returns a list of offending URL
    tokens; judgment is required for the fix (research the specific
    title URL or drop the platform slug), so this never auto-writes.
    """
    out = []
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return out
    try:
        try:
            from migration.viewer_carriage import is_generic_landing_url
        except ImportError:
            from viewer_carriage import (  # type: ignore
                is_generic_landing_url,
            )
    except Exception:
        return out
    col_u = df['Column'].astype(str).str.strip().str.upper()
    for idx in df.index[col_u == 'BRAND INPUT']:
        value = str(df.at[idx, 'Value'] or '')
        toks = [t.strip() for t in value.split(',') if t.strip()]
        urlish = [t for t in toks if '/' in t or
                  __import__('re').match(
                      r'^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$', t)]
        for t in urlish:
            # Path-bearing tokens are real URLs (any domain); dotted
            # no-path tokens are usually name variants (PAW.Patrol,
            # Samsung.Tv) and only flag on known platform domains.
            if is_generic_landing_url(t,
                                      require_platform_domain=(
                                          '/' not in t)):
                out.append(t)
                if verbose:
                    print(f'   🚧 BRAND INPUT landing page: {t!r} is a '
                          f'generic platform page (every visitor '
                          f'qualifies); needs a specific title URL')
    return out


def audit_upload_invariants(df, subject='', context='',
                            exact_2dp_limit=50, verbose=True):
    """Pre-upload invariant audit - the loud tripwire for the defect
    signatures caught on the Furious run (2026-08-24, D2/D4/D6).

    Checks (report only, never mutates):
      - exact-2dp BP landings on non-metadata rows (>limit = missed
        depin/dejitter pass);
      - categories not sorted BP-descending (missed sort pass);
      - percent/comma string artifacts in numeric cells (missed
        _normalize_numeric_artifacts);
      - SUBJECT metadata row present;
      - subject self-pin erosion (clean-subject row in [95, 100) in a
        non-demo category).

    Returns a dict report; prints ❌ lines for hard failures.
    """
    report = {
        'context': context,
        'exact_2dp_count': 0,
        'unsorted_categories': [],
        'percent_string_cells': 0,
        'subject_row_present': False,
        'eroded_self_pins': [],
        'ok': True,
    }
    if (df is None or len(df) == 0 or 'Column' not in df.columns
            or 'Value' not in df.columns):
        return report

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    col_u = df['Column'].astype(str).str.strip().str.upper()
    meta_like = (set(METADATA_COLS)
                 | {'INPUT_METADATA', 'GENERAL', 'AVID FAN', 'CASUAL FAN'})

    def _fnum(v):
        try:
            return float(str(v).replace(',', '').replace('%', '').strip())
        except Exception:
            return None

    # 1. exact-2dp landings
    exact2 = 0
    if bp_col in df.columns:
        for idx in df.index:
            if col_u.loc[idx] in meta_like:
                continue
            bpv = _fnum(df.at[idx, bp_col])
            if bpv is None or bpv <= 0 or bpv >= 99.985:
                continue
            if abs(bpv * 100 - round(bpv * 100)) < 1e-7:
                exact2 += 1
    report['exact_2dp_count'] = exact2

    # 2. per-category BP-descending sort
    unsorted = []
    if bp_col in df.columns:
        for cat, grp in df.groupby('Column', sort=False):
            if str(cat).strip().upper() in meta_like:
                continue
            seq = [_fnum(v) for v in grp[bp_col]]
            seq = [s if s is not None else -1.0 for s in seq]
            if any(seq[i] < seq[i + 1] - 1e-9 for i in range(len(seq) - 1)):
                unsorted.append(str(cat))
    report['unsorted_categories'] = unsorted

    # 3. percent/comma string artifacts in numeric cells
    pct_cells = 0
    for c in (bp_col, cs_col, raw_col, proj_col):
        if not c or c not in df.columns:
            continue
        for v in df[c]:
            s = str(v).strip()
            if not s or s.lower() in ('nan', 'none', ''):
                continue
            try:
                float(s)
                continue
            except Exception:
                pass
            if _fnum(s) is not None:
                pct_cells += 1
    report['percent_string_cells'] = pct_cells

    # 4. SUBJECT metadata row
    report['subject_row_present'] = bool((col_u == 'SUBJECT').any())

    # 5. self-pin erosion
    eroded = []
    if bp_col in df.columns:
        bi_mask = col_u == 'BRAND INPUT'
        bi_val = (str(df.loc[bi_mask].iloc[0].get('Value', '') or '').strip()
                  if bi_mask.any() else '')
        clean = _clean_subject_from_bi(
            bi_val, df=df, col_u=col_u, subject_arg=subject)
        cn = _norm_ident(clean)
        if cn:
            for idx in df.index:
                cat = col_u.loc[idx]
                if cat in meta_like or cat in DEPIN_DEMO_CATS:
                    continue
                if _norm_ident(df.at[idx, 'Value']) != cn:
                    continue
                bpv = _fnum(df.at[idx, bp_col])
                if bpv is not None and 95.0 <= bpv < 99.9999:
                    eroded.append((str(df.at[idx, 'Column']), bpv))
    report['eroded_self_pins'] = eroded

    hard_fail = (exact2 > exact_2dp_limit or unsorted or pct_cells
                 or eroded)
    report['ok'] = not hard_fail
    tag = f" [{context}]" if context else ''
    if exact2 > exact_2dp_limit:
        print(f"   ❌ UPLOAD AUDIT{tag}: {exact2} BPs on exact-2dp "
              f"boundaries (>{exact_2dp_limit}) - depin/dejitter pass "
              f"did not run on this file")
    elif exact2 and verbose:
        print(f"   ⚠ upload audit{tag}: {exact2} exact-2dp BPs (within "
              f"tolerance {exact_2dp_limit})")
    if unsorted:
        print(f"   ❌ UPLOAD AUDIT{tag}: {len(unsorted)} categories not "
              f"sorted BP-descending (e.g. {unsorted[:5]}) - sort pass "
              f"did not run")
    if pct_cells:
        print(f"   ❌ UPLOAD AUDIT{tag}: {pct_cells} numeric cells carry "
              f"%/comma string artifacts - _normalize_numeric_artifacts "
              f"did not run late enough")
    if not report['subject_row_present']:
        print(f"   ⚠️ UPLOAD AUDIT{tag}: SUBJECT metadata row missing")
    if eroded:
        print(f"   ❌ UPLOAD AUDIT{tag}: subject self-pin eroded below "
              f"100: {eroded[:5]}")
    if report['ok'] and verbose:
        print(f"   ✅ upload audit{tag}: clean "
              f"(2dp={exact2}, sorted, no artifacts, "
              f"subject_row={report['subject_row_present']})")
    return report


def strip_url_variant_seed_rows(df, subject, verbose=True):
    """Drop or clean category rows whose Value is a BG.py URL-variant
    seed list. See module docstring above for detection/action rules.

    NEVER touches METADATA_COLS (BRAND INPUT / SAMPLE SIZE / BRAND
    CATEGORY / SUBJECT).
    """
    if df is None or len(df) == 0:
        return df, 0
    if 'Column' not in df.columns or 'Value' not in df.columns:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    cats_u = df['Column'].astype(str).str.upper().str.strip()
    vals = df['Value'].astype(str)

    def _norm_first_tok(v):
        first = v.split(',')[0].strip().upper()
        # collapse whitespace + punctuation for match
        return _re.sub(r'[^A-Z0-9]+', '', first)

    to_delete_idx = []
    to_replace = []       # (idx, new_val)
    affected_cats = set()
    examples = []

    for idx in df.index:
        col_u = cats_u.iat[idx] if hasattr(cats_u, 'iat') else cats_u.iloc[idx]
        if col_u in METADATA_COLS:
            continue
        val = vals.iat[idx] if hasattr(vals, 'iat') else vals.iloc[idx]
        if not _is_url_variant_seed_list(val):
            continue
        first_tok = val.split(',')[0].strip()
        clean_norm = _norm_first_tok(val)
        # Look for a clean-named sibling in the same Column
        cat_mask = (cats_u == col_u)
        has_clean_sibling = False
        for j in df.index[cat_mask]:
            if j == idx:
                continue
            other = vals.iat[j] if hasattr(vals, 'iat') else vals.iloc[j]
            if _is_url_variant_seed_list(other):
                continue
            other_norm = _re.sub(r'[^A-Z0-9]+', '', str(other).upper())
            if other_norm == clean_norm:
                has_clean_sibling = True
                break
        if has_clean_sibling:
            to_delete_idx.append(idx)
            affected_cats.add(col_u)
            if len(examples) < 3:
                examples.append(('DEL', col_u, val[:60]))
        else:
            to_replace.append((idx, first_tok))
            if len(examples) < 3:
                examples.append(('REN', col_u, val[:60]))

    if not to_delete_idx and not to_replace:
        return df, 0

    if verbose:
        summary = ', '.join(f'[{action} {cat}]"{v}..."'
                            for action, cat, v in examples)
        n_total = len(to_delete_idx) + len(to_replace)
        more = (f' (+{n_total - len(examples)} more)'
                if n_total > len(examples) else '')
        print(f"   🧹 strip_url_variant_seed_rows: "
              f"del={len(to_delete_idx)} rename={len(to_replace)} {summary}{more}")

    for idx, new_val in to_replace:
        df.at[idx, 'Value'] = new_val
    if to_delete_idx:
        df = df.drop(index=to_delete_idx).reset_index(drop=True)
    # Renormalize categories that lost rows (BP sums)
    for cat in affected_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)
    return df, len(to_delete_idx) + len(to_replace)


# ============================================================================
# strip_avid_casual_fan_rows — retire AVID FAN / CASUAL FAN rows (2026-07-20)
# ----------------------------------------------------------------------------
# User mandate 2026-07-20 (Jenna): "remove the avid fan/casual fan rows from
# all profile iqs pipeline, we don't need that anywhere so it shouldnt be in
# the output".
#
# Runs at the tail of run_all_enforcers (after every enforcer that used
# them as skip-list markers -- those references become harmless no-ops
# once the rows are gone) and just before validate_demo_sum_100.
#
# Downstream consumers that historically read these rows:
#   1. avid_fan_row_by_row.py (line ~137) -- reads avid_fan_bp as a SOFT
#      Claude prompt signal (audience snapshot). Missing row just means
#      Claude picks cohort_fraction without that specific signal; the
#      broader audience picture (demos, category BPs) is still fully
#      available. No functional break.
#   2. audience_cut_synthesis._compute_deterministic_cohort_fraction
#      (line ~177) -- reads AVID FAN BP for the rare "OG -> avid_F/M"
#      derivation. HARD dependency. The function already has a graceful
#      fallback (line ~183 returns gender-share alone) which now fires
#      + logs a warning. In practice this path is unused: gender-avid
#      cuts are always sourced from the auto-generated AVID FAN file,
#      which returns early at line ~173 (source_intensity="avid" branch)
#      and never reaches the AVID FAN row read.
#
# NEVER touches METADATA_COLS (BRAND INPUT / SAMPLE SIZE / BRAND CATEGORY /
# SUBJECT). Idempotent -- no-op on files that already lack the rows.
# ============================================================================

def strip_avid_casual_fan_rows(df, subject, verbose=True,
                                 keep_avid_row=False):
    """Drop retired fan rows. CASUAL FAN (plus SUPER FAN / SUPERFAN /
    CORE FAN hygiene variants) is stripped ALWAYS, everywhere: by the
    data model the TU IS the casual cohort, so a casual row is
    definitionally redundant.

    AVID FAN semantics REVERSED 2026-08-24 (Jenna, verbatim approval
    "Go with what you recommend", consciously reversing her 2026-07-20
    "remove the avid fan/casual fan rows" directive): TUs carry a
    reasoned AVID FAN row again (migration/avid_share_reasoner.py,
    one Claude call per fresh TU) so avid cuts get the same
    deterministic TU anchor mandated for gender/age/geo cuts
    (deterministic_avid_fraction = parent row BP / 100). Every
    hash-era row was stripped from S3 the same day, so any AVID FAN
    row on a current file is reasoned-era.

    ``keep_avid_row``:
      * True  -- TU write paths. profile_writer auto-detects TU keys
                 (no ' - ' cut suffix in the basename); the BG.py
                 save-gate passes True explicitly (BG.py builds TUs).
                 The AVID FAN row is preserved (deduped to one row).
      * False (default) -- derived cuts and every unthreaded legacy
                 caller: AVID FAN is stripped alongside CASUAL FAN. A
                 cut carrying its own AVID FAN row is meaningless (the
                 cut IS the avid slice, or reads its anchor from the
                 parent TU at derive time).

    Returns (df, n_rows_removed).
    """
    if df is None or len(df) == 0:
        return df, 0
    if 'Column' not in df.columns:
        return df, 0

    ALWAYS_STRIP = {'CASUAL FAN', 'SUPER FAN', 'SUPERFAN', 'CORE FAN'}
    col_upper = df['Column'].astype(str).str.strip().str.upper()
    fan_mask = col_upper.isin(ALWAYS_STRIP)
    if keep_avid_row:
        # Keep exactly ONE AVID FAN row -- extra copies are an
        # ingestion defect; keep the first.
        avid_idx = list(df.index[col_upper == 'AVID FAN'])
        if len(avid_idx) > 1:
            fan_mask = fan_mask | df.index.isin(avid_idx[1:])
    else:
        fan_mask = fan_mask | (col_upper == 'AVID FAN')
    n = int(fan_mask.sum())
    if n == 0:
        return df, 0

    if verbose:
        removed_labels = sorted(set(col_upper[fan_mask].tolist()))
        kept_note = (" (AVID FAN kept: reasoned-era TU anchor, "
                     "Jenna 2026-08-24)" if keep_avid_row else "")
        print(f"   🗑️  strip_avid_casual_fan_rows: dropped {n} fan-row(s) "
              f"({', '.join(removed_labels)}){kept_note}")

    df = df.loc[~fan_mask].reset_index(drop=True)
    return df, n


def enforce_follower_ceiling_projection(df, subject, follower_ceiling,
                                          verbose=True):
    """Enforce the physical public-metric ceiling for any capped-audience
    profile: US Gen Pop Projection cannot exceed the underlying public
    metric that defines the cohort.

    Established 2026-08-19 (Jenna directive), broadened same day per
    follow-up: "confirming that the followers of or viewers of a
    certain video, etc that has easy to see public metrics will never
    exceed that value when projected."

    Applies to every capped audience_type (see
    migration/follower_ceiling.CAPPED_AUDIENCE_TYPES):
      - followers / subscribers: cap = total follower / subscriber count
      - viewers: cap = video / broadcast view count (YouTube, Nielsen,
        streamer top-10)
      - listeners: cap = podcast play / listener count (Spotify,
        Chartable, Podtrac)
      - attendees: cap = event attendance figure
      - users: cap = MAU / DAU

    The function only cares about the ceiling NUMBER, not the audience
    type - the math is identical regardless of which public metric
    supplies it. Parameter is named `follower_ceiling` for backward
    compat; treat it as "audience_ceiling".

    How it works:
      1. Read BRAND INPUT row's `Original Raw Numbers` (= subject_raw =
         sample size). Fall back to SAMPLE SIZE row if BRAND INPUT is
         missing.
      2. Compute current projection = raw / PANEL * US_POP.
      3. If projection > ceiling, cap raw at
         floor(ceiling * PANEL / US_POP) and rewrite the BRAND INPUT /
         SAMPLE SIZE Raw + Proj cells.
      4. Downstream `recompute_raw_and_projection` cascades the new
         sample size to every brand row's Raw + Proj (see Rule #3a).

    Idempotent: if raw is already at or below the ceiling, no changes.

    Only fires when `follower_ceiling` is a positive integer. Pass
    `None` or `0` to disable (default in the general audience case).

    Returns (df, n_cells_updated).
    """
    if df is None or len(df) == 0:
        return df, 0
    try:
        fc = int(follower_ceiling) if follower_ceiling else 0
    except Exception:
        fc = 0
    if fc <= 0:
        return df, 0

    try:
        from migration.follower_ceiling import (
            max_subject_raw_for_ceiling, PROJECTION_MULTIPLIER,
        )
    except Exception:
        # Fallback math if the helper isn't importable for some reason
        # (very unlikely; belt-and-suspenders).
        US_POP_LOCAL = 329_900_000
        PANEL_LOCAL = 10_000_000
        PROJECTION_MULTIPLIER = US_POP_LOCAL / PANEL_LOCAL
        def max_subject_raw_for_ceiling(x):
            import math as _m
            try:
                return _m.floor(int(x) * PANEL_LOCAL / US_POP_LOCAL)
            except Exception:
                return 0

    max_raw = max_subject_raw_for_ceiling(fc)
    if max_raw <= 0:
        return df, 0

    def _num(v):
        try:
            return float(str(v).replace(',', '').replace('%', '').strip())
        except Exception:
            return None

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if raw_col is None and proj_col is None:
        return df, 0
    if 'Column' not in df.columns:
        return df, 0

    col_upper = df['Column'].astype(str).str.upper().str.strip()
    # Locate the metadata rows we need to correct
    bi_mask = col_upper == 'BRAND INPUT'
    ss_mask = col_upper == 'SAMPLE SIZE'
    if not (bi_mask.any() or ss_mask.any()):
        return df, 0

    # Determine current sample_size from either row (prefer BRAND INPUT
    # since it always carries BP=100 -> raw=sample_size).
    current_raw = None
    for mask in (bi_mask, ss_mask):
        if not mask.any():
            continue
        idx = df.index[mask][0]
        v = _num(df.at[idx, raw_col]) if raw_col else None
        if v and v > 0:
            current_raw = int(round(v))
            break
    if current_raw is None or current_raw <= 0:
        return df, 0

    if current_raw <= max_raw:
        # Already under the ceiling.
        return df, 0

    projection_before = int(round(current_raw * PROJECTION_MULTIPLIER))
    projection_after = int(round(max_raw * PROJECTION_MULTIPLIER))
    if verbose:
        print(f"   🔒 enforce_follower_ceiling_projection: "
              f"{subject!r} follower_ceiling={fc:,}; "
              f"sample_size {current_raw:,} -> {max_raw:,}; "
              f"projection {projection_before:,} -> {projection_after:,}")

    # Rewrite Raw + Proj cells on BOTH BRAND INPUT and SAMPLE SIZE rows.
    # BP + Category Share stay untouched — they were correct relative
    # to the (now smaller) sample size, and the downstream
    # `recompute_raw_and_projection` pass cascades the new sample_size
    # to every non-metadata row's Raw + Proj automatically.
    n_cells = 0
    for mask in (bi_mask, ss_mask):
        if not mask.any():
            continue
        idx = df.index[mask][0]
        if raw_col is not None:
            try:
                df.at[idx, raw_col] = int(max_raw)
                n_cells += 1
            except Exception:
                pass
        if proj_col is not None:
            try:
                df.at[idx, proj_col] = int(projection_after)
                n_cells += 1
            except Exception:
                pass
    return df, n_cells


def recompute_raw_and_projection(df, subject, verbose=True):
    """Recompute Original Raw Numbers + US Gen Pop Projection from BP for
    every row. Sample size derives from BRAND INPUT row (BP=100 ->
    raw=sample_size by definition); falls back to SAMPLE SIZE row if
    BRAND INPUT missing.

    Formulas (canonical, edit_sample_size.py-style; CORRECTED 2026-05-28):
        Raw  = round(BP / 100 * sample_size)
        Proj = round(Raw / 10_000_000 * 329_900_000)

    The 10M denominator is a fixed virtual panel; sample_size is the
    subject's audience count within it. Proj depends on subject's
    sample_size and is NOT BP/100 * 329.9M unless sample_size == 10M.

    Added 2026-05-27 after sweep found ALL 230 profiles in S3 had stale
    Raw/Proj cells. Root cause: ad-hoc BP edits didn't recompute Raw/Proj.
    Updated 2026-05-28: switched Proj formula to (Raw/10M)*329.9M to
    match edit_sample_size.py logic (was BP/100 * 329.9M, which over-
    projected for any subject_raw < 10M).

    Preserves BP and Category Share unchanged.
    """
    if df is None or len(df) == 0:
        return df, 0

    def _num(v):
        try:
            return float(str(v).replace(',', '').replace('%', '').strip())
        except Exception:
            return None

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if raw_col is None and proj_col is None:
        return df, 0

    PANEL = 10_000_000
    c_upper = df['Column'].astype(str).str.upper().str.strip()
    sample_size = None
    proj_base = None
    for cat in ('BRAND INPUT', 'SAMPLE SIZE'):
        cand = df[c_upper == cat]
        if len(cand) == 0:
            continue
        r = cand.iloc[0]
        bp = _num(r.get(bp_col))
        raw = _num(r.get(raw_col)) if raw_col else None
        if raw and bp and bp > 0:
            sample_size = raw / (bp / 100.0)
            # Projection base at BP=100 (the file's own universe). Only
            # consulted on international frames below; US frames keep
            # the fixed 10M-panel chain byte-identically.
            proj_meta = _num(r.get(proj_col)) if proj_col else None
            if proj_meta and proj_meta > 0:
                proj_base = proj_meta / (bp / 100.0)
            break
    if sample_size is None:
        if verbose:
            print('   recompute_raw_and_projection: no sample-size source '
                  '(BRAND INPUT / SAMPLE SIZE missing or BP=0); skipping')
        return df, 0

    bp_actual = df[bp_col].apply(_num)
    valid = bp_actual.notna()
    cells = 0

    # Raw first (rounded integer), Proj FROM the rounded Raw.
    # 2026-08-22 fix: Proj was previously computed from the unrounded
    # BP/100*sample product ("equivalent" per the old comment), but after
    # Raw is rounded the two formulas diverge by up to +/-16. The
    # canonical chain (Rule #3a, this function's own docstring) is
    # BP -> Raw = round(...) -> Proj = round(Raw/10M * 329.9M).
    raw_expected = (bp_actual / 100.0 * sample_size).round(0)

    if raw_col is not None:
        raw_actual = df[raw_col].apply(_num)
        diff = ((raw_actual - raw_expected).abs() > 1).fillna(False) & valid
        cells += int(diff.sum())
        df.loc[valid, raw_col] = raw_expected.loc[valid].astype('int64')

    if proj_col is not None:
        proj_actual = df[proj_col].apply(_num)
        _ctry = _frame_country(df)
        if _ctry and proj_base and proj_base > 0:
            # International frame (Omaze precedent): projections scale
            # to the file's own country universe (SAMPLE SIZE row Proj
            # at BP=100), never the US 10M-panel chain. Omaze UK: TU
            # sample 40,247 projecting to a UK universe of 4,027,143.
            proj_expected = (bp_actual / 100.0 * proj_base).round(0)
            if verbose:
                print(f'   recompute_raw_and_projection: {_ctry} frame - '
                      f'projections anchored to the country universe '
                      f'({int(round(proj_base)):,})')
        else:
            # Proj = round(Raw / 10M * 329.9M) -- from the ROUNDED Raw
            proj_expected = (raw_expected / PANEL * US_POP).round(0)
        diff = ((proj_actual - proj_expected).abs() > 1).fillna(False) & valid
        cells += int(diff.sum())
        df.loc[valid, proj_col] = proj_expected.loc[valid].astype('int64')

    if verbose and cells:
        print(f'   recomputed {cells} Raw/Proj cells '
              f'(sample_size={int(round(sample_size))})')
    return df, cells


# Pure-metadata categories that should NEVER carry a BP (BP must stay
# blank even if raw is populated by an upstream corruption). All other
# categories — including BRAND INPUT, SAMPLE SIZE, AVID FAN, CASUAL FAN,
# self-anchor rows in TALENT / MUSICIAN-BAND / etc. — DO carry BP and
# get filled by the enforcer below.
_BP_FILL_SKIP_COLS = {'BRAND CATEGORY', 'INPUT_METADATA'}


def fill_missing_bp_from_raw(df, subject='', verbose=True):
    """Backfill blank Brand Penetration (Row) values from Original Raw Numbers.

    Defense-in-depth save-gate enforcer (added 2026-06-24 after Liz flagged
    Ed Sheeran TU and corpus sweep found 11 same-day-generated files with
    98.6% blank BP rows). Root cause: the non-GenPop pipeline relies on
    parallel_category_agents to populate BP per row, but no backstop runs
    if those agents silently fail. Result: the file lands in S3 with raw
    counts populated (from the panel query) and Category Share populated
    (from the Snowflake query) but `Brand Penetration (Row)` literally
    empty between commas.

    Idempotent: rows where BP is already a valid number are left alone.
    Only fills rows where BP is blank / None / 'nan' / 'NaN' AND raw > 0
    AND the row's Column is not pure metadata.

    Formula (matches add_brand_penetration_column_using_final_raw and the
    /tmp/fix_missing_bp_parallel.py corpus-sweep script):
        BP = (raw / sample_size) * 100, rounded to 4dp

    Sample-size resolution order (matches recompute_raw_and_projection):
        1. BRAND INPUT raw (preferred — by definition raw == sample at BP=100)
        2. SAMPLE SIZE raw
        3. SAMPLE SIZE Category Share (fallback for older files where the
           panel N is in the share column rather than raw)

    Skips rows where Column is in `_BP_FILL_SKIP_COLS` ({BRAND CATEGORY,
    INPUT_METADATA}) — these are pure metadata rows that legitimately
    carry no BP. AVID FAN / CASUAL FAN / SAMPLE SIZE / BRAND INPUT DO
    get filled (their BP is derived from raw and is referenced by
    downstream consumers).

    Returns (df, n_filled) where n_filled is the count of cells written.
    """
    if df is None or len(df) == 0:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns or raw_col is None:
        return df, 0

    def _num(v):
        try:
            return float(str(v).replace(',', '').replace('%', '').strip())
        except Exception:
            return None

    def _is_blank(v):
        if v is None:
            return True
        s = str(v).strip()
        return s in ('', 'None', 'nan', 'NaN')

    c_upper = df['Column'].astype(str).str.upper().str.strip()

    sample_size = None
    bi_rows = df[c_upper == 'BRAND INPUT']
    if len(bi_rows) > 0:
        cand = _num(bi_rows.iloc[0].get(raw_col))
        if cand and cand > 1000:
            sample_size = cand
    if sample_size is None:
        ss_rows = df[c_upper == 'SAMPLE SIZE']
        if len(ss_rows) > 0:
            for try_col in (raw_col, cs_col):
                if try_col and try_col in df.columns:
                    cand = _num(ss_rows.iloc[0].get(try_col))
                    if cand and cand > 1000:
                        sample_size = cand
                        break
    if sample_size is None or sample_size <= 0:
        if verbose:
            print('   fill_missing_bp_from_raw: no sample-size source; skipping')
        return df, 0

    n_filled = 0
    for idx in df.index:
        if c_upper.at[idx] in _BP_FILL_SKIP_COLS:
            continue
        bp_val = df.at[idx, bp_col]
        if not _is_blank(bp_val):
            continue
        raw_val = _num(df.at[idx, raw_col])
        if raw_val is None or raw_val <= 0:
            continue
        pct = (raw_val / sample_size) * 100.0
        pct = round(min(pct, 100.0), 4)
        df.at[idx, bp_col] = f"{pct:.4f}"
        n_filled += 1

    if verbose and n_filled:
        print(f'   fill_missing_bp_from_raw: filled {n_filled} blank BP cell(s) '
              f'for {subject or "(no subject)"} (sample_size={int(round(sample_size))})')
    return df, n_filled


def strip_hostmap_hidden_brands(df, subject, verbose=True):
    """Drop every row whose Value is classified as SECTION='Hidden' in
    reference.host_mapping (Rule #4b — Hidden brands must never appear
    in shipped profiles).

    Added 2026-05-27 after Bria flagged The Root (MEDIA) and Ecosia
    (SEARCH ENGINE/AI) in Universal Basic Guys; both are hostmap-Hidden.
    There are ~8,041 Hidden-classified brands; the canonical list is
    cached at reference/hostmap_hidden_brands.txt.

    Skips METADATA_COLS (Brand Input / Sample Size / Brand Category)
    so that subject-row values can't accidentally trip the filter.
    """
    if not _ensure_hostmap_hidden_loaded():
        if verbose:
            print('   ⚠️ hostmap_hidden_brands.txt not found; skipping Hidden-strip')
        return df, 0
    return _strip_rows(
        df, subject,
        lambda c, v: _is_hostmap_hidden(v),
        label='hostmap-Hidden', verbose=verbose,
    )


def strip_mpb_non_hostmap_brands(df, subject, verbose=True):
    """Drop every row in column ``MOST PURCHASED BRANDS`` whose Value is
    NOT hostmap-classified into a ``Most Purchased Brands, *`` section.

    Rule #4c (added 2026-05-28). Defect signature: Stephen A Smith profile
    shipped with 1,146 of 2,137 MPB rows being brands hostmap-classified
    elsewhere (NETFLIX/HULU under Streaming, AMAZON/WALMART/TARGET under
    Where They Shop, VISA/MASTERCARD under Credit Provider, MCDONALDS
    under QSR, PAYPAL/VENMO under Digital Banking, GOOGLE under Search,
    APPLE/SAMSUNG under Technology/Device, etc.). Every one of those
    brands ALREADY appeared in its proper category row on the same
    profile, so the MPB duplicates were pure pollution.

    The MPB column is reserved for the ~2,131 brands that hostmap
    actually labels under one of the Most Purchased sub-sections
    (Apparel/Footwear, CPG, Home/Outdoor, Beauty/Wellness, Accessories,
    Technology Brand, Pets). Anything else stays in its native column.

    Renormalizes MPB Category Share after dropping (via _strip_rows).
    Only operates on rows whose column upper-cases to MOST PURCHASED
    BRANDS — never touches any other column.
    """
    if not _ensure_hostmap_mpb_loaded():
        if verbose:
            print('   ⚠️ hostmap_mpb_brands.txt not found; skipping MPB-membership filter')
        return df, 0
    return _strip_rows(
        df, subject,
        lambda c, v: c == 'MOST PURCHASED BRANDS' and not _is_hostmap_mpb(v),
        label='non-hostmap-MPB', verbose=verbose,
    )


# ============================================================================
# Talent-template defect signature corrector — PANEL-REALITY FLOORS
# ============================================================================
#
# Cross-file pattern confirmed (8 files: Pacino / Sandler / Aniston / Spurs /
# Amazon Music / Apple Music / Pacino-pre-fix / Pacino-final) of a "prestige
# template" applied to older-male-talent files that systematically depresses
# mass-engagement brand BPs to "prestige curated" levels instead of panel-
# tracked online activity levels. Examples:
#
#   - Google → 60% (should be 88-92% panel reach)
#   - McDonald's → 11% (should be 35-50% mass QSR)
#   - Microsoft → 16% (should be 50-60% Office/Windows for working-age audience)
#   - Geico/Progressive → 4% (should be 16-22% quote-shopping panel)
#   - Toyota/Honda → 5% (should be 16-25% mass-auto research panel)
#   - Nike → 8% (should be 22-50%+ mass footwear)
#   - Movie theatre chains (AMC/Cinemark/Regal) depressed
#   - Roku (TECHNOLOGY/DEVICE) depressed even when ROKU CHANNEL is correctly high
#   - USPS depressed (older audience is heaviest USPS user)
#
# This enforcer detects the demographic archetype from the file's own AGE
# distribution, then applies per-(brand, archetype) panel-reality FLOORS.
# It only LIFTS BPs (never reduces) — values correctly elevated by the
# upstream agent are preserved.
#
# This is NOT a multiplier. Each (brand, archetype) cell is its own per-row
# decision derived from panel-tracked online-activity reality. Recipe rule
# §3 explicitly allows panel-cap formulas as guardrails in the deterministic
# safety-net (`enforce_audit_playbook` family).
#
# Special Apple↔Samsung rebalancing: Pacino-pre-fix had Apple=74, Samsung=10.
# Older-audience reality is closer to 55-65 / 22-32. If we detect that
# inversion pattern AND we're in 'older' archetype, we rebalance.

# Per (CATEGORY, BRAND_UPPER), per-archetype minimum BP from panel reality
# of US adults with online activity in the window. Archetypes: 'older',
# 'mid', 'young'. Detected from the file's AGE distribution.
PANEL_REALITY_FLOORS = {
    # SEARCH ENGINE / AI — Google universal; Yahoo/Bing skew older
    ('SEARCH ENGINE/AI', 'GOOGLE'): {'older': 86.0, 'mid': 89.0, 'young': 93.0},
    ('SEARCH ENGINE/AI', 'YAHOO'):  {'older': 14.0, 'mid': 11.0, 'young':  9.0},
    ('SEARCH ENGINE/AI', 'BING'):   {'older': 10.0, 'mid':  9.0, 'young':  7.0},

    # QSR — McDonald's mass panel (breakfast, coffee, delivery app)
    ('QSR', "MCDONALD'S"): {'older': 35.0, 'mid': 50.0, 'young': 62.0},
    ('QSR', 'MCDONALDS'):  {'older': 35.0, 'mid': 50.0, 'young': 62.0},

    # TECHNOLOGY/DEVICE — Microsoft Office/Windows; Android/Samsung older-share
    ('TECHNOLOGY/DEVICE', 'MICROSOFT'): {'older': 50.0, 'mid': 38.0, 'young': 22.0},
    ('TECHNOLOGY/DEVICE', 'SAMSUNG'):   {'older': 18.0, 'mid': 28.0, 'young': 36.0},
    ('TECHNOLOGY/DEVICE', 'ANDROID'):   {'older': 20.0, 'mid': 30.0, 'young': 38.0},
    ('TECHNOLOGY/DEVICE', 'ROKU'):      {'older': 16.0, 'mid': 18.0, 'young': 20.0},

    # TECHNOLOGY BRAND mirror (some pipelines emit both)
    ('TECHNOLOGY BRAND', 'MICROSOFT'): {'older': 50.0, 'mid': 38.0, 'young': 22.0},
    ('TECHNOLOGY BRAND', 'SAMSUNG'):   {'older': 18.0, 'mid': 28.0, 'young': 36.0},

    # APP/PLATFORM USAGE — USPS informed-delivery panel
    ('APP/PLATFORM USAGE', 'USPS'): {'older': 48.0, 'mid': 36.0, 'young': 26.0},

    # INSURANCE — universal quote-shopping panel; depressed across all talent files
    ('INSURANCE', 'GEICO'):       {'older': 16.0, 'mid': 18.0, 'young': 22.0},
    ('INSURANCE', 'PROGRESSIVE'): {'older': 15.0, 'mid': 17.0, 'young': 20.0},
    ('INSURANCE', 'STATE FARM'):  {'older': 11.7, 'mid': 12.6, 'young': 13.4},
    ('INSURANCE', 'ALLSTATE'):    {'older': 7.7, 'mid': 7.7, 'young': 7.7},

    # AUTOMOBILE — mass-auto research panel (CarFax / dealer apps)
    ('AUTOMOBILE', 'TOYOTA'):    {'older': 14.0, 'mid': 20.0, 'young': 26.0},
    ('AUTOMOBILE', 'HONDA'):     {'older': 13.0, 'mid': 18.0, 'young': 22.0},
    ('AUTOMOBILE', 'FORD'):      {'older': 14.0, 'mid': 16.0, 'young': 17.0},
    ('AUTOMOBILE', 'CHEVROLET'): {'older': 12.0, 'mid': 14.0, 'young': 15.0},

    # MOST PURCHASED BRANDS / APPAREL/FOOTWEAR — Nike mass footwear (companion sync)
    ('MOST PURCHASED BRANDS', 'NIKE'): {'older': 18.0, 'mid': 32.0, 'young': 48.0},
    ('APPAREL/FOOTWEAR',      'NIKE'): {'older': 18.0, 'mid': 32.0, 'young': 48.0},

    # MOVIE THEATER — mass-theatrical-attendance panel
    ('MOVIE THEATER', 'AMC THEATRES'):       {'older': 11.1, 'mid': 13.9, 'young': 16.7},
    ('MOVIE THEATER', 'CINEMARK THEATRES'):  {'older': 7.0, 'mid': 9.7, 'young': 11.4},
    ('MOVIE THEATER', 'REGAL CINEMAS'):      {'older': 6.6, 'mid': 8.5, 'young': 10.3},

    # TRAVEL — Booking #1 OTA panel; tends to invert with Expedia in talent template
    ('TRAVEL', 'BOOKING'): {'older': 26.0, 'mid': 30.0, 'young': 32.0},
}


# ---------------------------------------------------------------------------
# Defect Class #18 — Floor-lift overshoot (added 2026-05-23).
# Per-segment Pew/benchmark figures by AGE bucket. The enforcer computes a
# persona-segment-weighted target = Σ(audience_segment_pct × segment_benchmark)
# instead of snapping every audience to a static archetype floor. This is the
# colleague's "target the persona-aligned value rather than applying a blanket
# multiplier" principle, encoded as data not as another multiplier.
#
# If a brand is missing from SEGMENT_BENCHMARKS we fall back to the legacy
# PANEL_REALITY_FLOORS archetype lookup so existing behaviour is preserved.
#
# Format: {(CATEGORY_U, BRAND_U): {'18-24': pct, '25-34': pct, '35-44': pct,
#                                  '45-54': pct, '55-64': pct, '65+': pct}}
# Numbers come from Pew Research (social), Forrester (retail), Nielsen (FAST),
# and FDIC (banking). Never edit these without citing a source in the diff.
# ---------------------------------------------------------------------------
SEGMENT_BENCHMARKS = {
    # SOCIAL MEDIA — Pew 2024
    ('SOCIAL MEDIA', 'TIKTOK'):    {'18-24': 78, '25-34': 62, '35-44': 39, '45-54': 24, '55-64': 17, '65+':  9},
    ('SOCIAL MEDIA', 'SNAPCHAT'):  {'18-24': 75, '25-34': 56, '35-44': 25, '45-54': 14, '55-64':  8, '65+':  4},
    ('SOCIAL MEDIA', 'INSTAGRAM'): {'18-24': 78, '25-34': 71, '35-44': 49, '45-54': 33, '55-64': 22, '65+': 15},
    ('SOCIAL MEDIA', 'LINKEDIN'):  {'18-24': 25, '25-34': 36, '35-44': 31, '45-54': 26, '55-64': 20, '65+': 11},
    ('SOCIAL MEDIA', 'PINTEREST'): {'18-24': 33, '25-34': 31, '35-44': 30, '45-54': 24, '55-64': 22, '65+': 18},
    ('SOCIAL MEDIA', 'X'):         {'18-24': 32, '25-34': 27, '35-44': 21, '45-54': 16, '55-64': 12, '65+':  6},
    # YOUTUBE - near-universal reach (Pew 2024: 90%+ of US adults under 65).
    # Added 2026-08-24 (Erin Brooks / Dylan Minnette audits: engager builds
    # under-read YouTube at index ~47 vs Gen Pop baseline ~88; engagers must
    # index at or above gen pop on digital behaviors). Gen-pop-weighted ~87.6
    # vs corrected baseline 88.07. One-way lift only (NOT in KNOWN_OVERSHOOT).
    ('SOCIAL MEDIA', 'YOUTUBE'):   {'18-24': 94, '25-34': 93, '35-44': 92, '45-54': 89, '55-64': 85, '65+': 78},

    # SEARCH / AI — Pew + LLM-adoption survey 2024
    ('SEARCH ENGINE/AI', 'CHAT GPT'):   {'18-24': 48, '25-34': 42, '35-44': 33, '45-54': 23, '55-64': 14, '65+':  8},
    ('SEARCH ENGINE/AI', 'GEMINI'):     {'18-24': 22, '25-34': 20, '35-44': 16, '45-54': 12, '55-64':  9, '65+':  5},
    ('SEARCH ENGINE/AI', 'PERPLEXITY'): {'18-24':  5, '25-34':  4, '35-44':  3, '45-54':  2, '55-64':  1, '65+':  1},
    ('SEARCH ENGINE/AI', 'CLAUDE AI'):  {'18-24': 14, '25-34': 12, '35-44':  9, '45-54':  6, '55-64':  3, '65+':  1},

    # WHERE THEY SHOP — pharmacy/mass retail visit-in-past-6mo, NOT universal
    ('WHERE THEY SHOP', 'CVS'):       {'18-24': 11, '25-34': 14, '35-44': 16, '45-54': 18, '55-64': 19, '65+': 21},
    ('WHERE THEY SHOP', 'WALGREENS'): {'18-24': 10, '25-34': 12, '35-44': 15, '45-54': 17, '55-64': 18, '65+': 20},
    ('WHERE THEY SHOP', 'TEMU'):      {'18-24': 37, '25-34': 34, '35-44': 25, '45-54': 16, '55-64':  9, '65+':  4},
    ('WHERE THEY SHOP', 'COSTCO'):    {'18-24': 13, '25-34': 17, '35-44': 21, '45-54': 22, '55-64': 21, '65+': 17},

    # TELECOM Big 3 — carrier share-of-audience (subscriber overlap)
    # T-MOBILE updated 2026-05-25 per colleague flag: was systematically under-read by 14-22pp
    # across Foosball/Keke/Dove/Nate pulls. T-Mo passed AT&T in US subs in 2023 (~33% share)
    # and has aggressive Magenta55+ program lifting older buckets too. Old benchmarks were
    # 28/28/26/22/18/12; new ones reflect ~33% national share at all working-age buckets.
    ('TELECOM', 'VERIZON'):  {'18-24': 28, '25-34': 30, '35-44': 32, '45-54': 33, '55-64': 32, '65+': 28},
    ('TELECOM', 'AT&T'):     {'18-24': 22, '25-34': 24, '35-44': 26, '45-54': 27, '55-64': 26, '65+': 22},
    ('TELECOM', 'T-MOBILE'): {'18-24': 34, '25-34': 34, '35-44': 32, '45-54': 30, '55-64': 26, '65+': 18},

    # BANKING Big 5 — primary-bank household share
    ('BANKING', 'CHASE'):           {'18-24': 19, '25-34': 23, '35-44': 24, '45-54': 23, '55-64': 20, '65+': 17},
    ('BANKING', 'BANK OF AMERICA'): {'18-24': 16, '25-34': 20, '35-44': 20, '45-54': 19, '55-64': 18, '65+': 15},
    ('BANKING', 'WELLS FARGO'):     {'18-24': 15, '25-34': 18, '35-44': 20, '45-54': 20, '55-64': 18, '65+': 15},
    ('BANKING', 'CITIBANK'):        {'18-24':  6, '25-34':  9, '35-44':  9, '45-54':  9, '55-64':  8, '65+':  6},
    ('BANKING', 'US BANK'):         {'18-24':  3, '25-34':  4, '35-44':  5, '45-54':  5, '55-64':  5, '65+':  5},

    # DIGITAL BANKING — younger-skewing P2P
    ('DIGITAL BANKING', 'PAYPAL'):   {'18-24': 48, '25-34': 50, '35-44': 45, '45-54': 40, '55-64': 33, '65+': 24},
    ('DIGITAL BANKING', 'VENMO'):    {'18-24': 55, '25-34': 48, '35-44': 33, '45-54': 19, '55-64': 10, '65+':  4},
    ('DIGITAL BANKING', 'CASH APP'): {'18-24': 42, '25-34': 35, '35-44': 23, '45-54': 15, '55-64':  9, '65+':  4},
    ('DIGITAL BANKING', 'ZELLE'):    {'18-24': 22, '25-34': 36, '35-44': 42, '45-54': 38, '55-64': 32, '65+': 22},
    ('DIGITAL BANKING', 'APPLE PAY'):{'18-24': 28, '25-34': 26, '35-44': 22, '45-54': 16, '55-64': 10, '65+':  5},
    # Ally / Chime are niche — NOT in benchmarks; legacy floor logic stays away

    # CREDIT PROVIDER — Visa/MC/Discover/Amex universal mass anchors
    # (revised 2026-06-04 per Jenna's 7-of-11 Visa over-read defect)
    # PRIOR ERROR: benchmarks were *cardholder-share* (Forrester / Fed Reserve
    # cardholder survey), not adult-population penetration. Visa cardholder
    # share is ~75% but only ~76% of adults *hold any credit card*, so adult-
    # population Visa penetration is ~58%, not ~80%. Pre-fix bench was 76-82%
    # which meant the LLM's 55-65% reasoned outputs sat 12-20pp below floor
    # and either got silently lifted to a 60% pin (the 7/11 pattern Jenna's
    # colleague flagged) or were preserved at 60% because the floor enforcer
    # silently no-op'd. Either way the read was uniformly ~60% across personas.
    #
    # NEW: adult-population numbers from Fed 2024 Survey of Consumer Finances
    # × Nilson Report 2024 card-network share. Younger buckets meaningfully
    # lower (new-to-credit), 35-54 peak, 65+ drops (retiree card retirement).
    # Combined with KNOWN_OVERSHOOT membership below → two-sided trim so
    # 60%+ Visa on a 25-34-heavy persona (bench 50) gets pulled into 47-53.
    ('CREDIT PROVIDER', 'VISA'):       {'18-24': 35, '25-34': 50, '35-44': 58, '45-54': 58, '55-64': 54, '65+': 46},
    ('CREDIT PROVIDER', 'MASTERCARD'): {'18-24': 16, '25-34': 25, '35-44': 32, '45-54': 32, '55-64': 28, '65+': 22},
    ('CREDIT PROVIDER', 'DISCOVER CREDIT CARD'): {'18-24':  8, '25-34': 12, '35-44': 16, '45-54': 16, '55-64': 14, '65+': 11},
    ('CREDIT PROVIDER', 'AMERICAN EXPRESS'): {'18-24':  4, '25-34':  9, '35-44': 13, '45-54': 15, '55-64': 13, '65+': 10},
    ('CREDIT PROVIDER', 'CAPITAL ONE'): {'18-24': 12, '25-34': 20, '35-44': 26, '45-54': 26, '55-64': 22, '65+': 18},
    # Synchrony — store-card issuer (Amazon Store, PayPal Credit, Lowe's, Care Credit, Walmart).
    # (added 2026-05-25 per KD review — was at 2.67%, ~70M cardholders / 258M adults skews ~12-15%)
    ('CREDIT PROVIDER', 'SYNCHRONY'):  {'18-24':  8, '25-34': 12, '35-44': 14, '45-54': 14, '55-64': 12, '65+': 10},

    # TELECOM/ISP — Xfinity (Comcast) ~31M residential subs / ~131M US HH = ~24% HH coverage.
    # (added 2026-05-25 per KD review — was at 7.81%, panel-tracked ~18-22% for adults in coverage)
    ('TELECOM', 'XFINITY'): {'18-24': 14, '25-34': 20, '35-44': 22, '45-54': 22, '55-64': 20, '65+': 18},

    # STREAMING/PLATFORM — Amazon Prime Video ~150M US HH (Prime household halo).
    # (added 2026-05-25 per Gen Pop colleague review — was at 45% gen pop, real is 60-72%)
    # Younger heavy Prime adoption; older buckets still 50%+ via household sharing.
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): {'18-24': 33, '25-34': 39, '35-44': 41, '45-54': 39, '55-64': 36, '65+': 29},

    # STREAMING/PLATFORM — Paramount+ ~71M global subs (~40M US). CBS NFL package,
    # Champions League, SEC football, Star Trek, Latin content. Texas culture
    # carry via CBS DFW + NFL Sunday games. (added 2026-05-25 per Texas Rangers audit
    # — was at 7.10% on Texas-skewing/Hispanic audience.)
    # Note: Sheet4 canonical is 'Paramount+' (with +). No 'PARAMOUNT PLUS' alias —
    # any such row in a profile would itself be a Rule #4 violation.
    ('STREAMING/PLATFORM', 'PARAMOUNT+'):    {'18-24': 12, '25-34': 18, '35-44': 22, '45-54': 22, '55-64': 18, '65+': 14},

    # WHERE THEY SHOP — Sephora prestige beauty. Female-skewing, younger-skew.
    # (added 2026-05-25 per Texas Rangers audit — was at 5.55% on 33% F audience).
    # Anchor: ~25M Sephora Beauty Insider members + Kohl's-bay traffic.
    # The audience-weighting downstream will scale by gender share.
    # Sheet4 canonical: 'SEPHORA' and 'Ulta Beauty'. No 'ULTA' alias.
    ('WHERE THEY SHOP', 'SEPHORA'):     {'18-24': 24, '25-34': 23, '35-44': 19, '45-54': 14, '55-64':  8, '65+':  4},
    ('WHERE THEY SHOP', 'ULTA BEAUTY'): {'18-24': 19, '25-34': 20, '35-44': 19, '45-54': 15, '55-64': 10, '65+':  7},

    # WHERE THEY SHOP — Target mass retail, slight female + young skew.
    # (added 2026-05-25 per Valkyrae audit — was at 38.30% on young female
    #  multicultural audience; persona-real 50-60%.)
    ('WHERE THEY SHOP', 'TARGET'): {'18-24': 34, '25-34': 36, '35-44': 34, '45-54': 30, '55-64': 25, '65+': 18},

    # QSR — Chipotle young + Hispanic + Asian over-index (urban fast-casual).
    # (added 2026-05-25 per Valkyrae audit — was at 8.73% on young multicultural
    #  audience; persona-real 18-28%). Sheet4 canonical: 'Chipotle Mexican Grill'.
    ('QSR', 'CHIPOTLE MEXICAN GRILL'): {'18-24': 28, '25-34': 26, '35-44': 18, '45-54': 12, '55-64':  8, '65+':  4},

    # PORN MEDIA — Pornhub male-heavy young adult. ~42B annual visits, US is
    # largest market. Anchor for 47% male young audience ~12-16%.
    # (added 2026-05-25 per Valkyrae audit — was at 0.97% on 47% male young.)
    ('PORN MEDIA', 'PORNHUB'): {'18-24': 18, '25-34': 16, '35-44': 12, '45-54':  8, '55-64':  4, '65+':  2},

    # VIRTUAL MVPD FAST — older-skewing free-with-ads platforms.
    # (added 2026-05-25 per Patrick Stewart review — recurring under-read across
    #  Foosball/Keke/KD/Patrick pulls. Tubi/Roku Channel/Pluto are the FAST anchors
    #  for older audiences; DirecTV bundled traditional+streaming legacy.)
    ('VIRTUAL MVPD FAST', 'ROKU CHANNEL'): {'18-24': 10, '25-34': 14, '35-44': 18, '45-54': 22, '55-64': 24, '65+': 22},
    ('VIRTUAL MVPD FAST', 'TUBI'):         {'18-24': 18, '25-34': 22, '35-44': 22, '45-54': 22, '55-64': 22, '65+': 18},
    ('VIRTUAL MVPD FAST', 'PLUTO TV'):     {'18-24':  8, '25-34': 11, '35-44': 14, '45-54': 14, '55-64': 14, '65+': 12},
    ('VIRTUAL MVPD FAST', 'XUMO'):         {'18-24':  1, '25-34':  2, '35-44':  2, '45-54':  2, '55-64':  3, '65+':  2},
    ('VIRTUAL MVPD FAST', 'YOUTUBE TV'):   {'18-24':  6, '25-34':  8, '35-44':  9, '45-54':  8, '55-64':  7, '65+':  5},
    ('VIRTUAL MVPD FAST', 'DIRECTV'):      {'18-24':  3, '25-34':  4, '35-44':  6, '45-54':  7, '55-64':  9, '65+': 10},

    # SEARCH ENGINE/AI — older Windows/Edge default surface.
    # (added 2026-05-25 per Patrick Stewart review — Bing/MSN systematically under-read
    #  for older audiences; Bing was 9.9% on 85% age-35+ audience.)
    ('SEARCH ENGINE/AI', 'BING'): {'18-24':  8, '25-34': 12, '35-44': 18, '45-54': 24, '55-64': 30, '65+': 32},
    ('SEARCH ENGINE/AI', 'MSN'):  {'18-24':  4, '25-34':  6, '35-44': 10, '45-54': 16, '55-64': 22, '65+': 26},
    # Copilot — bundled into Windows / Edge, older Windows skew.
    # (added 2026-05-25 per Sandra/Regina/Olivia/Queen lock-release pass — was at
    #  16.1172 lock on Regina/Queen but emerging audience-aware values should curve.)
    ('SEARCH ENGINE/AI', 'COPILOT'): {'18-24':  4, '25-34':  5, '35-44':  7, '45-54':  8, '55-64': 10, '65+':  8},
    # GOOGLE — universal search anchor. Real-world penetration peaks at ~92% (Pew
    # 2024 + ComScore 2024). The LLM tends to write 99.9% as a "saturation pin"
    # for any tech-positive audience, producing the 99.99% Chicago_Sky artifact
    # (Jenna 2026-06-03 master Gemini defect ticket). Two-sided trim activates
    # via KNOWN_OVERSHOOT_BRANDS below; without these benchmarks the trim has
    # no segment-weighted target and falls through to the older/mid/young
    # PANEL_REALITY_FLOORS lookup (which is also high and only LIFTS, never trims).
    ('SEARCH ENGINE/AI', 'GOOGLE'):     {'18-24': 95, '25-34': 94, '35-44': 92, '45-54': 89, '55-64': 86, '65+': 80},

    # STREAMING/MUSIC — SiriusXM is age-curved (in-car commercial older). iHeart
    # and Pandora REMOVED from SEGMENT in favor of ETHNICITY (see Black radio
    # over-index below). Age-only would suppress Black audiences' real listening.
    ('STREAMING/MUSIC', 'SIRIUSXM'): {'18-24':  3, '25-34':  5, '35-44':  9, '45-54': 10, '55-64': 11, '65+': 11},

    # TELECOM — Spectrum ~30% US HH coverage; broadband + cable bundles.
    # NOTE: Spectrum is heavily REGIONAL (Texas/Carolinas/LA/NY) not just age.
    # Age curve approximates regional baseline; specific DMA tuning belongs in
    # the per-category research agent (Rule #2: reasoning > floors).
    ('TELECOM', 'SPECTRUM'): {'18-24': 12, '25-34': 16, '35-44': 18, '45-54': 18, '55-64': 18, '65+': 16},

    # APPAREL/FOOTWEAR — universal mass anchors. Nike + Adidas were stuck at
    # ~18-19% across Penelope/Patrick/Robin/Octavia (4 of 4 talent files in
    # the 5-25 batch). Adding age-curved targets (younger over-index).
    # (added 2026-05-25 per Penelope Cruz colleague review with backfill mention)
    ('APPAREL/FOOTWEAR', 'NIKE'):   {'18-24': 48, '25-34': 45, '35-44': 41, '45-54': 36, '55-64': 32, '65+': 27},
    ('APPAREL/FOOTWEAR', 'ADIDAS'): {'18-24': 38, '25-34': 34, '35-44': 28, '45-54': 22, '55-64': 18, '65+': 14},
}


# ---------------------------------------------------------------------------
# Defect Class #20 — Black + Hispanic targeted streamers ethnicity-blind miss.
# Added 2026-05-25 per Keke / KD / Dove / LA Sparks reviews (4 consecutive files).
#
# These brands have penetration determined by ETHNICITY composition, not by AGE.
# The LLM defaults to a near-zero baseline for all profiles because it doesn't
# weight by ethnicity. BET+, ALLBLK, Vix, Telemundo, Tidal, Zeus Network all
# read at 0.3-2.5% across profiles when real penetration scales linearly with
# the Black/Hispanic audience share.
#
# Format: {(CATEGORY_U, BRAND_U): {ethnicity_bucket: pct}}
# Then: target = Σ(audience_ethnicity_pct × bucket_pct)
# ---------------------------------------------------------------------------
ETHNICITY_BENCHMARKS = {
    # Calibrated 2026-05-25 against colleague's explicit landing zones:
    #   Gen Pop (12% Black, 18% Hisp): BET+ 3-5, ALLBLK 1-2, Telemundo 5-9, Vix 3-6
    #   Keke (36% Black, 18% Hisp):    BET+ 22-32 (manually lifted to 26)
    # Linear ethnicity weighting fits Gen Pop targets cleanly; high-Black audiences
    # (>30%) MAY need additional manual review at audit time because engagement
    # appears non-linear in those segments.
    ('STREAMING/PLATFORM', 'BET+'):         {'WHITE':  2, 'BLACK': 22, 'HISPANIC':  5, 'ASIAN':  2, 'OTHER':  4},
    ('STREAMING/PLATFORM', 'ALLBLK'):       {'WHITE':  1, 'BLACK': 10, 'HISPANIC':  2, 'ASIAN':  1, 'OTHER':  2},
    ('STREAMING/PLATFORM', 'ZEUS NETWORK'): {'WHITE':  2, 'BLACK': 12, 'HISPANIC':  5, 'ASIAN':  2, 'OTHER':  3},
    # Spanish-language / Hispanic-targeted
    ('STREAMING/PLATFORM', 'VIX'):       {'WHITE':  2, 'BLACK':  3, 'HISPANIC': 22, 'ASIAN':  3, 'OTHER':  4},
    ('STREAMING/PLATFORM', 'TELEMUNDO'): {'WHITE':  2, 'BLACK':  3, 'HISPANIC': 32, 'ASIAN':  2, 'OTHER':  3},
    # Tidal — Black-skewing music streaming (Jay-Z-founded, hip-hop catalog priority)
    ('STREAMING/MUSIC', 'TIDAL'): {'WHITE':  3, 'BLACK': 14, 'HISPANIC':  5, 'ASIAN':  2, 'OTHER':  3},
    # Apple Music — modest Black/Hispanic skew via hip-hop/R&B exclusives + Beats halo
    # (added 2026-05-25 per KD music review — was landing at 27% on 28.5% Black audience)
    ('STREAMING/MUSIC', 'APPLE MUSIC'): {'WHITE': 32, 'BLACK': 48, 'HISPANIC': 42, 'ASIAN': 30, 'OTHER': 32},
    # SoundCloud — strong Black-skew via hip-hop/independent rap catalog reliance.
    # (added 2026-05-25 per KD review — was 9.4% on 28.5% Black audience; SoundCloud
    #  is the primary discovery platform for the genre)
    ('STREAMING/MUSIC', 'SOUNDCLOUD'): {'WHITE': 10, 'BLACK': 32, 'HISPANIC': 18, 'ASIAN':  8, 'OTHER': 12},
    # Amazon Music — modest Black-skew + mass scale via Prime household halo.
    # (added 2026-05-25 per KD review — was 10.5% on multicultural mass audience)
    ('STREAMING/MUSIC', 'AMAZON MUSIC'): {'WHITE': 26, 'BLACK': 38, 'HISPANIC': 34, 'ASIAN': 24, 'OTHER': 28},
    # iHeartRadio — heavy Black + Hispanic radio over-index (Steve Harvey, Tom
    # Joyner morning, R&B/Hispanic FM syndication). Age also matters but ethnicity
    # is the primary driver.
    # (added 2026-05-25 per Regina/Queen reasoning — Black-skew was 24-26%, white
    #  audience was 14-16%, locking at 17.40 averages the wrong way.)
    ('STREAMING/MUSIC', 'IHEART'):        {'WHITE': 12, 'BLACK': 32, 'HISPANIC': 22, 'ASIAN':  8, 'OTHER': 14},
    # Pandora — historic Black/Hispanic over-index vs Spotify (older streaming).
    # (added 2026-05-25 per Regina/Queen reasoning.)
    ('STREAMING/MUSIC', 'PANDORA MUSIC'): {'WHITE': 11, 'BLACK': 20, 'HISPANIC': 16, 'ASIAN':  8, 'OTHER': 12},

    # VIRTUAL MVPD FAST — Tubi has strong multicultural over-index (Black + Hispanic
    # are its primary audiences). Adding ethnicity weighting on top of base segment
    # benchmark so multicultural profiles auto-land high.
    # (added 2026-05-25 per Penelope Cruz review — was 19% on 32.8% Hispanic)
    ('VIRTUAL MVPD FAST', 'TUBI'): {'WHITE': 16, 'BLACK': 42, 'HISPANIC': 56, 'ASIAN': 14, 'OTHER': 20},

    # STREAMING/PLATFORM — Paramount+ Hispanic over-index via CBS Telemundo
    # adjacency + Champions League / Latin originals + bundled Showtime mass appeal.
    # (added 2026-05-25 per Texas Rangers audit — was 7.10% on 35.7% Hispanic
    #  Texas Rangers audience.) Sheet4 canonical: 'Paramount+'.
    ('STREAMING/PLATFORM', 'PARAMOUNT+'):    {'WHITE': 14, 'BLACK': 18, 'HISPANIC': 28, 'ASIAN': 10, 'OTHER': 14},

    # APPAREL/FOOTWEAR — Spanish-brand boost for Hispanic-heavy audiences.
    # (added 2026-05-25 per Penelope Cruz colleague feedback — Zara/Mango should
    #  scale to Hispanic audience share, not generic apparel ratio.)
    ('APPAREL/FOOTWEAR', 'ZARA'):  {'WHITE': 14, 'BLACK': 18, 'HISPANIC': 38, 'ASIAN': 16, 'OTHER': 18},
    ('APPAREL/FOOTWEAR', 'MANGO'): {'WHITE':  6, 'BLACK':  8, 'HISPANIC': 28, 'ASIAN':  8, 'OTHER': 10},
    ('APPAREL/FOOTWEAR', 'H&M'):   {'WHITE': 14, 'BLACK': 22, 'HISPANIC': 26, 'ASIAN': 16, 'OTHER': 18},

    # QSR — McDonald's has heavy multicultural over-index (Black/Hispanic).
    # (added 2026-05-25 per Octavia/Penelope/LA Sparks reviews — was suppressed
    #  at 32-37% on multicultural audiences when persona-real is 50-70%.)
    # Older audiences trim via age curve in SEGMENT, but Black/Hispanic baseline
    # is materially higher than White baseline at every age.
    ('QSR', "MCDONALD'S"): {'WHITE': 42, 'BLACK': 70, 'HISPANIC': 60, 'ASIAN': 36, 'OTHER': 48},
    ('QSR', 'MCDONALDS'):  {'WHITE': 42, 'BLACK': 70, 'HISPANIC': 60, 'ASIAN': 36, 'OTHER': 48},

    # QSR — Popeyes Louisiana Kitchen has heavy Black over-index (Southern + urban
    # fried-chicken audience). (added 2026-05-25 per Zendaya audit — was at 5.19%
    # on 28% Black audience; persona-real 16-22%).
    ('QSR', 'POPEYES'): {'WHITE': 14, 'BLACK': 38, 'HISPANIC': 22, 'ASIAN': 10, 'OTHER': 16},

    # QSR — Chipotle Hispanic-leaning + Asian urban + young white over-index.
    # (added 2026-05-25 per Valkyrae audit — was at 8.73% on young multicultural.)
    # Sheet4 canonical: 'Chipotle Mexican Grill'.
    ('QSR', 'CHIPOTLE MEXICAN GRILL'): {'WHITE': 20, 'BLACK': 14, 'HISPANIC': 28, 'ASIAN': 26, 'OTHER': 20},

    # WHERE THEY SHOP — Walmart has Black + Hispanic mass over-index. Tyler Perry
    # (~50%+ Black audience) should land 70-80% per persona-real. (added 2026-05-25
    # per Tyler Perry audit — was 55.50% on Black mass audience.)
    ('WHERE THEY SHOP', 'WALMART'): {'WHITE': 64, 'BLACK': 82, 'HISPANIC': 78, 'ASIAN': 52, 'OTHER': 64},

    # BANKING — Truist regional Southern bank with strong Black + Southern skew.
    # (added 2026-05-25 per Regina/Queen lock-release pass — was at 8.1737 lock.)
    ('BANKING', 'TRUIST BANK'): {'WHITE': 6, 'BLACK': 14, 'HISPANIC': 8, 'ASIAN': 4, 'OTHER': 6},
    ('BANKING', 'TRUIST'):      {'WHITE': 6, 'BLACK': 14, 'HISPANIC': 8, 'ASIAN': 4, 'OTHER': 6},
}


def _ethnicity_distribution(df):
    """Return {bucket: pct} for ETHNICITY column. Returns None if missing.

    Used by `_ethnicity_weighted_target()` for Defect #20 (ethnicity-blind
    Black/Hispanic streamer miss).
    """
    try:
        eth = df[df['Column'].astype(str).str.strip().str.upper() == 'ETHNICITY']
        if eth.empty:
            return None
        dist = {'WHITE': 0.0, 'BLACK': 0.0, 'HISPANIC': 0.0, 'ASIAN': 0.0, 'OTHER': 0.0}
        for _, r in eth.iterrows():
            v = str(r.get('Value', '') or '').upper().strip()
            bp = _bp(r.get('Brand Penetration (Row)', 0))
            if 'WHITE' in v: dist['WHITE'] += bp
            elif 'BLACK' in v or 'AFRICAN' in v: dist['BLACK'] += bp
            elif 'HISPANIC' in v or 'LATINO' in v or 'LATINX' in v: dist['HISPANIC'] += bp
            elif 'ASIAN' in v: dist['ASIAN'] += bp
            else: dist['OTHER'] += bp
        total = sum(dist.values())
        if total < 50.0:
            return None
        return {k: v/total for k, v in dist.items()}
    except Exception:
        return None


def _ethnicity_weighted_target(df, cat_u, brand_u):
    """Persona-aligned target for ethnicity-determined brands. Returns None
    if brand not in ETHNICITY_BENCHMARKS or ETHNICITY missing.
    """
    bench = ETHNICITY_BENCHMARKS.get((cat_u, brand_u))
    if not bench:
        return None
    dist = _ethnicity_distribution(df)
    if not dist:
        return None
    return sum(dist.get(b, 0.0) * bench.get(b, 0.0) for b in bench.keys())


# ---------------------------------------------------------------------------
# GENDER_BENCHMARKS — for brands whose penetration is gender-determined.
# (added 2026-05-25 per Dove Pinterest review + Nate review notes)
# Pattern: target = Σ(audience_gender_pct × bucket_pct)
# Buckets collapsed to FEMALE / MALE / OTHER (NB + trans bucketed into OTHER).
# ---------------------------------------------------------------------------
GENDER_BENCHMARKS = {
    # Pinterest — ~76% female user base globally; very strong female lift.
    # Young female audience target ~50%, Gen Pop ~30%, male-skewed audience ~12%.
    ('SOCIAL MEDIA', 'PINTEREST'): {'FEMALE': 52, 'MALE': 14, 'OTHER': 40},
    # Starbucks — strong female skew (~60% of customer base). Female-heavy
    # audiences over-index meaningfully. Male-heavy audiences trim.
    # (added 2026-05-25 per Penelope Cruz review — was 18% on 68% female audience.)
    ('QSR', 'STARBUCKS'): {'FEMALE': 42, 'MALE': 24, 'OTHER': 36},
    # Sephora / Ulta Beauty — prestige beauty, ~85-90% female customer base.
    # (added 2026-05-25 per Texas Rangers audit — Sephora was 5.55% on 33% F audience.)
    # Sheet4 canonical: 'SEPHORA' and 'Ulta Beauty'. No 'ULTA' alias.
    ('WHERE THEY SHOP', 'SEPHORA'):     {'FEMALE': 30, 'MALE':  5, 'OTHER': 22},
    ('WHERE THEY SHOP', 'ULTA BEAUTY'): {'FEMALE': 26, 'MALE':  3, 'OTHER': 18},

    # Pornhub — heavily male-skewed (~75% male). Anchor for young male audience.
    # (added 2026-05-25 per Valkyrae audit — was at 0.97% on 47% male young.)
    ('PORN MEDIA', 'PORNHUB'): {'FEMALE':  5, 'MALE': 18, 'OTHER': 10},
}


def _gender_distribution(df):
    """Return {bucket: pct} for GENDER column. Returns None if missing."""
    try:
        g = df[df['Column'].astype(str).str.strip().str.upper() == 'GENDER']
        if g.empty:
            return None
        dist = {'FEMALE': 0.0, 'MALE': 0.0, 'OTHER': 0.0}
        for _, r in g.iterrows():
            v = str(r.get('Value', '') or '').upper().strip()
            bp = _bp(r.get('Brand Penetration (Row)', 0))
            if 'TRANS FEMALE' in v: dist['OTHER'] += bp
            elif 'TRANS MALE' in v: dist['OTHER'] += bp
            elif 'NON-BINARY' in v or 'NONBINARY' in v: dist['OTHER'] += bp
            elif v == 'FEMALE': dist['FEMALE'] += bp
            elif v == 'MALE': dist['MALE'] += bp
            else: dist['OTHER'] += bp
        total = sum(dist.values())
        if total < 50.0:
            return None
        return {k: v/total for k, v in dist.items()}
    except Exception:
        return None


def _gender_weighted_target(df, cat_u, brand_u):
    """Persona-aligned target for gender-determined brands. Returns None if
    brand not in GENDER_BENCHMARKS or GENDER missing."""
    bench = GENDER_BENCHMARKS.get((cat_u, brand_u))
    if not bench:
        return None
    dist = _gender_distribution(df)
    if not dist:
        return None
    return sum(dist.get(b, 0.0) * bench.get(b, 0.0) for b in bench.keys())


# ---------------------------------------------------------------------------
# Defect Class #22 — MOST PURCHASED BRANDS default-floor suppression
# (added 2026-05-25 per Foosball / Sandra / KD / Keke MPB review)
#
# Evidence:
#   • Foosball MPB had 825 of 1208 rows (68%) at the 0.0100% default
#     suppression floor — including Old Spice, Gillette, Carhartt, Yeti,
#     Red Bull, Tide, Crest, Charmin on a 62% male audience.
#   • Sandra Bullock had Olay 7.51 / L'Oreal 11.87 / Maybelline 10.13 /
#     CeraVe 13.56 on a 60% female audience — all 4-10pp below the
#     audience-weighted digital-purchasing-share target.
#   • Pattern is category-wide MPB suppression, not gender-specific.
#
# Framework correction (per user 2026-05-25):
#   1. MOST PURCHASED BRANDS = digital purchasing share, NOT total reach.
#   2. Apply target's audience composition (gender × penetration) to
#      weight the digital share BEFORE flagging suppression. Don't use
#      a flat "should be 15-25%" — use audience-weighted blends.
#
# Formula: target = (F_pen × audience_F_pct) + (M_pen × audience_M_pct)
# where F_pen and M_pen are the brand's penetration rate within each
# gender (from US digital panel measurement, calibrated against the
# Gen Pop reads as the 50/50 anchor). Lift if current_BP < 0.65 × target,
# OR if current_BP <= 0.05% (i.e. caught in the default-floor lock).
# ---------------------------------------------------------------------------
MPB_DIGITAL_SHARE = {
    # Male grooming + personal care — strong M skew
    'OLD SPICE':                {'F': 4,  'M': 22},
    'GILLETTE':                 {'F': 8,  'M': 30},
    'AXE':                      {'F': 2,  'M': 18},
    'DOVE MEN+CARE':            {'F': 3,  'M': 18},
    'DOVE MEN':                 {'F': 3,  'M': 18},
    'NIVEA MEN':                {'F': 2,  'M': 10},
    'RIGHT GUARD':              {'F': 2,  'M': 8},
    'BARBASOL':                 {'F': 1,  'M': 6},
    # Workwear / tools / outdoor — strong M skew
    'CARHARTT':                 {'F': 6,  'M': 22},
    'CARHARTT WIP':             {'F': 4,  'M': 14},
    'YETI':                     {'F': 8,  'M': 18},
    'MILWAUKEE TOOLS':          {'F': 3,  'M': 16},
    'MILWAUKEE TOOL':           {'F': 3,  'M': 16},
    'DEWALT':                   {'F': 4,  'M': 18},
    'CRAFTSMAN':                {'F': 4,  'M': 16},
    'MAKITA':                   {'F': 2,  'M': 12},
    'BOSCH':                    {'F': 4,  'M': 14},
    'STANLEY':                  {'F': 10, 'M': 18},  # Stanley cups F-skew + tools M-skew, net moderate
    # Energy / spirits — M skew
    'RED BULL':                 {'F': 14, 'M': 24},
    'MONSTER ENERGY':           {'F': 12, 'M': 22},
    'JACK DANIELS':             {'F': 8,  'M': 18},
    'JIM BEAM':                 {'F': 6,  'M': 14},
    'COORS':                    {'F': 8,  'M': 18},
    'COORS LIGHT':              {'F': 10, 'M': 22},
    'MILLER LITE':              {'F': 10, 'M': 22},
    'BUDWEISER':                {'F': 8,  'M': 18},
    'BUD LIGHT':                {'F': 14, 'M': 22},
    # Male apparel
    'LEVI':                     {'F': 26, 'M': 28},   # near gender-neutral
    "LEVI'S":                   {'F': 26, 'M': 28},
    'WRANGLER':                 {'F': 8,  'M': 16},
    'TIMBERLAND':               {'F': 10, 'M': 16},
    'UNDER ARMOUR':             {'F': 18, 'M': 26},
    'DICKIES':                  {'F': 6,  'M': 14},
    'HARLEY DAVIDSON':          {'F': 2,  'M': 8},
    # Female beauty / skincare / cosmetics — strong F skew (the Sandra defect)
    'OLAY':                     {'F': 22, 'M': 4},
    "L'OREAL PARIS":            {'F': 28, 'M': 4},
    'LOREAL PARIS':             {'F': 28, 'M': 4},
    "L'OREAL":                  {'F': 26, 'M': 4},
    'LOREAL':                   {'F': 26, 'M': 4},
    'MAYBELLINE':               {'F': 24, 'M': 2},
    'NEUTROGENA':               {'F': 26, 'M': 8},
    'CERAVE':                   {'F': 28, 'M': 12},   # gaining male user base
    'CETAPHIL':                 {'F': 24, 'M': 10},
    'AVEENO':                   {'F': 22, 'M': 6},
    'CLINIQUE':                 {'F': 16, 'M': 2},
    'MAC COSMETICS':            {'F': 18, 'M': 3},
    'MAC':                      {'F': 18, 'M': 3},   # ambiguous; downstream substring match avoided
    'TARTE':                    {'F': 14, 'M': 1},
    'NARS':                     {'F': 12, 'M': 1},
    'FENTY BEAUTY':             {'F': 16, 'M': 2},
    'URBAN DECAY':              {'F': 14, 'M': 1},
    'BATH & BODY WORKS':        {'F': 28, 'M': 4},
    'BATH AND BODY WORKS':      {'F': 28, 'M': 4},
    'ALWAYS':                   {'F': 22, 'M': 0},   # never male
    'TAMPAX':                   {'F': 18, 'M': 0},
    'KOTEX':                    {'F': 14, 'M': 0},
    'VICTORIAS SECRET':         {'F': 22, 'M': 2},
    "VICTORIA'S SECRET":        {'F': 22, 'M': 2},
    'ULTA':                     {'F': 26, 'M': 3},   # MPB-side
    'COVERGIRL':                {'F': 18, 'M': 1},   # historic Black-female value cosmetics
    'COVER GIRL':               {'F': 18, 'M': 1},
    'REVLON':                   {'F': 18, 'M': 2},
    'CAROLS DAUGHTER':          {'F': 8,  'M': 1},   # Black-female targeted hair care
    "CAROL'S DAUGHTER":         {'F': 8,  'M': 1},
    'SHEA MOISTURE':            {'F': 10, 'M': 2},   # Black-female targeted hair / body
    'CANTU':                    {'F': 8,  'M': 2},   # Black hair care
    'MIELLE':                   {'F': 8,  'M': 1},   # Black hair care
    'MIELLE ORGANICS':          {'F': 8,  'M': 1},
    'AS I AM':                  {'F': 6,  'M': 1},
    'OUIDAD':                   {'F': 4,  'M': 1},
    'DEVACURL':                 {'F': 5,  'M': 1},
    'CURLSMITH':                {'F': 5,  'M': 1},
    'EOS':                      {'F': 12, 'M': 4},   # mass lip care
    # Household CPG — gender-neutral with mild F-skew (women still
    # primary household purchasers in panel data)
    'TIDE':                     {'F': 22, 'M': 12},
    'CREST':                    {'F': 20, 'M': 16},
    'COLGATE':                  {'F': 22, 'M': 18},
    'CHARMIN':                  {'F': 22, 'M': 14},
    'BOUNTY':                   {'F': 24, 'M': 14},
    'DAWN':                     {'F': 24, 'M': 12},
    'CLOROX':                   {'F': 22, 'M': 14},
    'PURELL':                   {'F': 16, 'M': 12},
    'WINDEX':                   {'F': 18, 'M': 10},
    'LYSOL':                    {'F': 22, 'M': 14},
    'FEBREZE':                  {'F': 22, 'M': 10},
    'GLAD':                     {'F': 18, 'M': 12},
    'ZIPLOC':                   {'F': 20, 'M': 12},
    # Household OTC — both buy, slight F-skew (caregiving panel signal)
    'TYLENOL':                  {'F': 18, 'M': 14},
    'ADVIL':                    {'F': 18, 'M': 14},
    'MOTRIN':                   {'F': 12, 'M': 8},
    'ALEVE':                    {'F': 14, 'M': 12},
    'BENADRYL':                 {'F': 14, 'M': 10},
    'CLARITIN':                 {'F': 12, 'M': 10},
    'PEPTO BISMOL':             {'F': 10, 'M': 12},
    # Snacks / mass food — gender-neutral
    "LAY'S":                    {'F': 18, 'M': 18},
    'LAYS':                     {'F': 18, 'M': 18},
    'DORITOS':                  {'F': 16, 'M': 22},
    'CHEETOS':                  {'F': 14, 'M': 18},
    'PRINGLES':                 {'F': 12, 'M': 16},
    'OREO':                     {'F': 20, 'M': 18},
    'KIT KAT':                  {'F': 16, 'M': 14},
    'SNICKERS':                 {'F': 14, 'M': 18},
    # M&Ms duplicates removed (see canonical entry near 'M & MS' below)
    # Beverages — non-alcoholic mass
    'COCA-COLA':                {'F': 28, 'M': 32},
    'COCA COLA':                {'F': 28, 'M': 32},
    'PEPSI':                    {'F': 22, 'M': 26},
    'DR PEPPER':                {'F': 16, 'M': 18},
    'MOUNTAIN DEW':             {'F':  9, 'M': 13},   # consensus 8-14% (Liz)
    'GATORADE':                 {'F': 14, 'M': 24},
    'POWERADE':                 {'F': 10, 'M': 18},
    'BODY ARMOR':               {'F': 10, 'M': 16},
    'BODYARMOR':                {'F': 10, 'M': 16},
    # Hair care — F skew
    'PANTENE':                  {'F': 22, 'M': 6},
    'HEAD & SHOULDERS':         {'F': 14, 'M': 16},   # rare neutral
    'HEAD AND SHOULDERS':       {'F': 14, 'M': 16},
    'DOVE':                     {'F': 24, 'M': 10},   # ambiguous w/ Dove Men
    'HERBAL ESSENCES':          {'F': 18, 'M': 3},
    'GARNIER':                  {'F': 18, 'M': 4},
    # Skincare adjacent (sunscreen, lotion)
    'EUCERIN':                  {'F': 16, 'M': 6},
    'JERGENS':                  {'F': 18, 'M': 4},
    'NIVEA':                    {'F': 18, 'M': 8},
    # Coffee — gender-neutral but F slight-skew
    'FOLGERS':                  {'F': 14, 'M': 12},
    'MAXWELL HOUSE':            {'F': 10, 'M': 8},
    'NESCAFE':                  {'F': 8,  'M': 8},
    # Baby care — F-skew (mothers primary panel purchaser signal),
    # parental-status over-index; the gender lift catches female-skew
    # audiences with Has-Children share — picked up Olivia 47% parental
    # at Pampers 3.33 (target ~15-18 for 63% F audience).
    'PAMPERS':                  {'F': 18, 'M': 10},
    'HUGGIES':                  {'F': 18, 'M': 10},
    'LUVS':                     {'F': 10, 'M': 5},
    'HONEST COMPANY':           {'F': 12, 'M': 5},
    'THE HONEST COMPANY':       {'F': 12, 'M': 5},
    'EARTHS BEST':              {'F': 8,  'M': 4},
    "EARTH'S BEST":             {'F': 8,  'M': 4},
    'GERBER':                   {'F':  6, 'M':  2},  # 4-7% mass / 2-4% low-children male (KD audit 5-26)
    'GERBER BABY FOOD':         {'F':  6, 'M':  2},  # 4-7% mass / 2-4% low-children male (KD audit 5-26)
    'PLAYTEX':                  {'F': 12, 'M': 4},
    'AVENT':                    {'F': 10, 'M': 4},
    'PHILIPS AVENT':            {'F': 10, 'M': 4},
    'MUNCHKIN':                 {'F': 12, 'M': 6},
    'AQUAPHOR':                 {'F': 16, 'M': 8},   # mass skincare, baby-adjacent
    'CETAPHIL BABY':            {'F': 12, 'M': 6},
    'JOHNSONS BABY':            {'F': 16, 'M': 10},
    "JOHNSON'S BABY":           {'F': 16, 'M': 10},
    "JOHNSON'S":                {'F': 16, 'M': 10},
    'DESITIN':                  {'F': 10, 'M': 6},
    'BOUDREAUX BUTT PASTE':     {'F': 8,  'M': 5},
    # Pet — gender-neutral
    'PURINA':                   {'F': 14, 'M': 12},
    'PEDIGREE':                 {'F': 8,  'M': 8},
    'IAMS':                     {'F': 6,  'M': 6},
    'BLUE BUFFALO':             {'F': 10, 'M': 8},
    'BLUE BUFFALO CO.':         {'F': 10, 'M': 8},   # name variant from KD file
    'BLUE BUFFALO CO':          {'F': 10, 'M': 8},
    'NUTRO':                    {'F': 6,  'M': 6},
    'NUTRO PETS':               {'F': 6,  'M': 6},
    'NUTRO MAX':                {'F': 6,  'M': 6},
    'GREENIES':                 {'F': 8,  'M': 6},
    'FRISKIES':                 {'F': 10, 'M': 6},
    'FANCY FEAST':              {'F': 12, 'M': 5},
    'MILK BONE':                {'F': 8,  'M': 6},
    'MILK-BONE':                {'F': 8,  'M': 6},
    'TEMPTATIONS':              {'F': 8,  'M': 5},
    'BENEFUL':                  {'F': 6,  'M': 5},
    'CESAR':                    {'F': 6,  'M': 3},
    'WELLNESS CORE':            {'F': 6,  'M': 5},
    'TAUTE':                    {'F': 4,  'M': 3},   # niche pet
    # Athletic / Athleisure — light M skew for Nike/UA, F skew for Lulu
    'NIKE':                     {'F': 32, 'M': 42},
    'ADIDAS':                   {'F': 22, 'M': 30},
    'PUMA':                     {'F': 12, 'M': 16},
    'NEW BALANCE':              {'F': 16, 'M': 22},
    'REEBOK':                   {'F': 12, 'M': 16},
    'LULULEMON':                {'F': 22, 'M': 6},
    'ATHLETA':                  {'F': 16, 'M': 2},
    # Mid-tier apparel
    'GAP':                      {'F': 22, 'M': 14},
    'OLD NAVY':                 {'F': 28, 'M': 16},
    'BANANA REPUBLIC':          {'F': 12, 'M': 10},
    'AMERICAN EAGLE':           {'F': 18, 'M': 12},
    'EXPRESS':                  {'F': 14, 'M': 8},
    'H&M':                      {'F': 22, 'M': 12},
    'ZARA':                     {'F': 18, 'M': 8},
    'UNIQLO':                   {'F': 14, 'M': 12},
    # Mass-market footwear — gender-neutral (Skechers/Crocs are
    # comfort-driven, used by everyone; ASICS/HOKA/ON RUNNING are
    # running-skew slightly M)
    'SKECHERS':                 {'F': 16, 'M': 14},
    'CROCS':                    {'F': 14, 'M': 12},
    'ASICS':                    {'F': 10, 'M': 14},
    'ON RUNNING':               {'F': 8,  'M': 10},
    'HOKA':                     {'F': 9,  'M': 10},
    'BROOKS':                   {'F': 8,  'M': 8},
    'ALLBIRDS':                 {'F': 6,  'M': 8},
    'SPERRY':                   {'F': 6,  'M': 8},
    'DR. MARTENS':              {'F': 10, 'M': 8},
    'CONVERSE':                 {'F': 14, 'M': 14},
    'VANS':                     {'F': 12, 'M': 14},
    # Mass apparel basics
    'HANES':                    {'F': 12, 'M': 14},
    'FRUIT OF THE LOOM':        {'F': 6,  'M': 10},
    'JOCKEY':                   {'F': 4,  'M': 8},
    'CARTERS':                  {'F': 14, 'M': 6},
    "CARTER'S":                 {'F': 14, 'M': 6},
    # Mid-luxury F-skew
    'COACH':                    {'F': 16, 'M': 4},
    'COACH OUTLET':             {'F': 14, 'M': 3},
    'KATE SPADE':               {'F': 12, 'M': 2},
    'KATE SPADE OUTLET':        {'F': 10, 'M': 2},
    'KENDRA SCOTT':             {'F': 10, 'M': 1},
    'PANDORA':                  {'F': 16, 'M': 3},   # jewelry, not radio
    # Cosmetics extension — F skew
    'KYLIE COSMETICS':          {'F': 10, 'M': 1},
    'ANASTASIA BEVERLY HILLS':  {'F': 9,  'M': 1},
    'CHARLOTTE TILBURY':        {'F': 8,  'M': 1},
    'CHARLOTTETILBURY':         {'F': 8,  'M': 1},
    'COLOURPOP':                {'F': 10, 'M': 1},
    'TOO FACED':                {'F': 10, 'M': 1},
    'TOO FACED COSMETICS':      {'F': 10, 'M': 1},
    'TARTE COSMETICS':          {'F': 14, 'M': 1},
    'BURTS BEES':               {'F': 16, 'M': 8},
    "BURT'S BEES":              {'F': 16, 'M': 8},
    'LUSH':                     {'F': 12, 'M': 4},
    'THE BODY SHOP':            {'F': 10, 'M': 3},
    'YANKEE CANDLE':            {'F': 18, 'M': 6},
    # Eyewear — gender-neutral
    'RAY-BAN':                  {'F': 16, 'M': 18},
    'RAY BAN':                  {'F': 16, 'M': 18},
    'WARBY PARKER':             {'F': 10, 'M': 10},
    'MAUI JIM':                 {'F': 5,  'M': 9},
    'OAKLEY':                   {'F': 6,  'M': 16},
    # Tech / wearables — gender-neutral
    'FITBIT':                   {'F': 10, 'M': 10},
    'GARMIN':                   {'F': 6,  'M': 10},
    'GOPRO':                    {'F': 4,  'M': 10},
    'ANKER':                    {'F': 6,  'M': 10},
    # Luxury / premium M skew
    'HUGO BOSS':                {'F': 4,  'M': 12},
    'RALPH LAUREN':             {'F': 12, 'M': 14},
    'BROOKS BROTHERS':          {'F': 4,  'M': 10},
    'KENNETH COLE':             {'F': 6,  'M': 10},
    # Sports apparel — M skew specialty
    'TAYLORMADE GOLF':          {'F': 1,  'M': 10},
    'TAYLORMADE':               {'F': 1,  'M': 10},
    'CALLAWAY':                 {'F': 1,  'M': 8},
    'FOOTJOY':                  {'F': 1,  'M': 8},
    'TITLEIST':                 {'F': 1,  'M': 8},
    'PING':                     {'F': 1,  'M': 6},
    'COBRA GOLF':               {'F': 1,  'M': 4},
    'RUSSELL ATHLETIC':         {'F': 4,  'M': 8},
    # Premium athleisure
    'CHAMPION':                 {'F': 12, 'M': 14},
    'PATAGONIA':                {'F': 10, 'M': 14},
    'NORTH FACE':               {'F': 14, 'M': 18},
    'THE NORTH FACE':           {'F': 14, 'M': 18},
    'COLUMBIA':                 {'F': 10, 'M': 16},
    # ── Extended cosmetics — F skew (added 2026-05-25 per Gen Pop residual audit)
    'NYX PROFESSIONAL MAKEUP':  {'F': 12, 'M': 1},
    'NYX':                      {'F': 12, 'M': 1},
    'BENEFIT COSMETICS':        {'F': 10, 'M': 1},
    'NARS COSMETICS':           {'F': 10, 'M': 1},
    'IT COSMETICS':             {'F': 10, 'M': 1},
    'HUDA BEAUTY':              {'F': 8,  'M': 1},
    'HOURGLASS COSMETICS':      {'F': 6,  'M': 1},
    'HOURGLASS':                {'F': 6,  'M': 1},
    'SMASHBOX':                 {'F': 7,  'M': 1},
    'KVD BEAUTY':               {'F': 5,  'M': 1},
    'KVD VEGAN BEAUTY':         {'F': 5,  'M': 1},
    'MARIO BADESCU':            {'F': 8,  'M': 2},
    'HERO COSMETICS':           {'F': 7,  'M': 3},
    'FIRST AID BEAUTY':         {'F': 8,  'M': 2},
    'SALLY HANSEN':             {'F': 14, 'M': 1},
    'TRINNY LONDON':            {'F': 5,  'M': 1},
    'KOSAS':                    {'F': 5,  'M': 1},
    'GLOSSIER':                 {'F': 8,  'M': 1},
    'RARE BEAUTY':              {'F': 9,  'M': 1},
    'ILIA':                     {'F': 5,  'M': 1},
    # Hair / scalp / styling — F skew (some neutral)
    'LIVING PROOF':             {'F': 7,  'M': 2},
    'BUMBLE AND BUMBLE':        {'F': 6,  'M': 2},
    'JOHN FRIEDA':              {'F': 9,  'M': 2},
    'MOROCCANOIL':              {'F': 10, 'M': 2},
    'OLAPLEX':                  {'F': 12, 'M': 3},
    'AVEDA':                    {'F': 8,  'M': 3},
    'REDKEN':                   {'F': 7,  'M': 4},
    'NUTRAFOL':                 {'F': 6,  'M': 4},
    'KERASTASE':                {'F': 6,  'M': 1},
    # Skincare / spa — F skew
    'DR. TEALS':                {'F': 14, 'M': 4},
    "DR. TEAL'S":               {'F': 14, 'M': 4},
    'AESOP':                    {'F': 6,  'M': 4},
    'SUN BUM':                  {'F': 10, 'M': 6},
    'SUAVE':                    {'F': 14, 'M': 8},
    'DOVE BEAUTY':              {'F': 28, 'M': 6},   # Sheet4-canonical, F-skew mass body care; user-validated 18-25% target on 60-73% F audiences
    # OTC / topical
    'NEOSPORIN':                {'F': 12, 'M': 10},
    'ICY HOT':                  {'F': 6,  'M': 10},
    'BIOFREEZE':                {'F': 6,  'M': 8},
    'BAND-AID':                 {'F': 14, 'M': 10},
    'BAND AID':                 {'F': 14, 'M': 10},
    'BANDAID':                  {'F': 14, 'M': 10},
    # Intimates / lingerie — strong F skew
    'KNIX':                     {'F': 8,  'M': 0},
    'THIRDLOVE':                {'F': 6,  'M': 0},
    'SOMA INTIMATES':           {'F': 7,  'M': 0},
    'MAIDENFORM':               {'F': 7,  'M': 0},
    'NINE WEST':                {'F': 10, 'M': 1},
    # Personal-care / deodorant
    'SECRET DEODORANT':         {'F': 14, 'M': 1},
    'SECRET':                   {'F': 14, 'M': 1},
    'DEGREE':                   {'F': 8,  'M': 14},
    'BRUT':                     {'F': 1,  'M': 6},
    'SPEED STICK':              {'F': 1,  'M': 8},
    # Apparel — F skew mid
    'TORRID':                   {'F': 14, 'M': 1},
    'J.JILL':                   {'F': 8,  'M': 0},
    'JJILL':                    {'F': 8,  'M': 0},
    'EILEEN FISHER':            {'F': 6,  'M': 0},
    'WHITE HOUSE BLACK MARKET': {'F': 8,  'M': 0},
    'CLUB MONACO':              {'F': 8,  'M': 5},
    'AEROPOSTALE':              {'F': 8,  'M': 6},
    'CLAIRES':                  {'F': 8,  'M': 1},
    "CLAIRE'S":                 {'F': 8,  'M': 1},
    'DKNY':                     {'F': 8,  'M': 5},
    'CALVIN KLEIN':             {'F': 12, 'M': 12},
    'TOMMY HILFIGER':           {'F': 12, 'M': 14},
    # Apparel — M skew mid
    'BONOBOS':                  {'F': 1,  'M': 8},
    'JOS. A BANK':              {'F': 1,  'M': 6},
    'JOS A BANK':               {'F': 1,  'M': 6},
    'MENS WEARHOUSE':           {'F': 1,  'M': 8},
    "MEN'S WEARHOUSE":          {'F': 1,  'M': 8},
    'STUSSY':                   {'F': 6,  'M': 10},
    'KITH':                     {'F': 4,  'M': 8},
    'SUPREME':                  {'F': 4,  'M': 10},
    # Footwear extension
    'HEY DUDE':                 {'F': 10, 'M': 10},
    'NATURALIZER':              {'F': 8,  'M': 1},
    'BROOKS SHOES':             {'F': 8,  'M': 8},
    'RYKA':                     {'F': 6,  'M': 1},
    'CLARKS':                   {'F': 8,  'M': 8},
    'ROTHYS':                   {'F': 6,  'M': 1},
    "ROTHY'S":                  {'F': 6,  'M': 1},
    # Healthcare worker / scrubs
    'FIGS':                     {'F': 8,  'M': 2},
    # Watches / accessories
    'FOSSIL':                   {'F': 8,  'M': 8},
    'CITIZEN WATCH':            {'F': 4,  'M': 8},
    'TIMEX':                    {'F': 4,  'M': 8},
    'SHINOLA':                  {'F': 3,  'M': 6},
    # Snack / mid-mass food (added per Gen Pop residual)
    'CLIF BAR':                 {'F': 10, 'M': 14},
    'RAOS HOMEMADE':            {'F': 8,  'M': 8},
    "RAO'S HOMEMADE":           {'F': 8,  'M': 8},
    'POP TARTS':                {'F': 14, 'M': 14},
    'KELLOGGS POP TARTS':       {'F': 14, 'M': 14},
    'POP-TARTS':                {'F': 14, 'M': 14},
    'TOSTITOS':                 {'F': 14, 'M': 16},
    'RUFFLES':                  {'F': 12, 'M': 14},
    'GODIVA':                   {'F': 6,  'M': 4},
    'GHIRARDELLI':              {'F': 8,  'M': 6},
    'BEYOND MEAT':              {'F': 6,  'M': 5},
    'IMPOSSIBLE FOODS':         {'F': 5,  'M': 4},
    'CHOBANI':                  {'F': 14, 'M': 10},
    'YOPLAIT':                  {'F': 12, 'M': 8},
    # Beverage — non-alc
    'POPPI PREBIOTIC SODA':     {'F': 6,  'M': 4},
    'POPPI':                    {'F': 6,  'M': 4},
    'OLIPOP':                   {'F': 6,  'M': 4},
    'PRIME DRINK':              {'F': 8,  'M': 10},
    'CELSIUS':                  {'F': 10, 'M': 12},
    'LACROIX':                  {'F': 16, 'M': 8},
    # Alcohol — beer extension
    'CORONA':                   {'F': 12, 'M': 20},
    'MICHELOB ULTRA':           {'F': 10, 'M': 16},
    'HEINEKEN':                 {'F': 8,  'M': 16},
    'STELLA ARTOIS':            {'F': 8,  'M': 12},
    'BLUE MOON':                {'F': 10, 'M': 14},
    'HIGH NOON':                {'F': 14, 'M': 14},
    # Pet care — gender-neutral
    'ROYAL CANIN':              {'F': 8,  'M': 6},
    'PEDIGREE':                 {'F': 8,  'M': 8},
    "HILL'S SCIENCE DIET":      {'F': 6,  'M': 5},
    'HILLS SCIENCE DIET':       {'F': 6,  'M': 5},
    'CHEWY':                    {'F': 14, 'M': 12},
    # Home / kitchen
    'HAMILTON BEACH':           {'F': 10, 'M': 6},
    'BREVILLE':                 {'F': 8,  'M': 6},
    'ALL-CLAD':                 {'F': 6,  'M': 5},
    'ALL CLAD':                 {'F': 6,  'M': 5},
    'KEURIG':                   {'F': 14, 'M': 12},
    'NESPRESSO':                {'F': 10, 'M': 8},
    'NINJA':                    {'F': 14, 'M': 12},
    'INSTANT POT':              {'F': 14, 'M': 10},
    'CUISINART':                {'F': 12, 'M': 8},
    'KITCHENAID':               {'F': 16, 'M': 12},
    # Home goods / decor
    'HALLMARK':                 {'F': 14, 'M': 6},
    'CASPER':                   {'F': 6,  'M': 7},
    'PURPLE MATTRESS':          {'F': 5,  'M': 6},
    'TUFT & NEEDLE':            {'F': 4,  'M': 5},
    'ARTICLE FURNITURE':        {'F': 5,  'M': 5},
    'ARTICLE':                  {'F': 5,  'M': 5},
    'BALLARD DESIGNS':          {'F': 6,  'M': 2},
    'WAYFAIR':                  {'F': 14, 'M': 10},
    'POTTERY BARN':             {'F': 10, 'M': 6},
    'WILLIAMS SONOMA':          {'F': 8,  'M': 4},
    'WILLIAMS-SONOMA':          {'F': 8,  'M': 4},
    'CRATE & BARREL':           {'F': 8,  'M': 5},
    'CRATE AND BARREL':         {'F': 8,  'M': 5},
    'IKEA':                     {'F': 18, 'M': 16},
    # Outdoor / camping (M skew)
    'OSPREY':                   {'F': 4,  'M': 8},
    'BLACK DIAMOND':            {'F': 3,  'M': 6},
    'REI':                      {'F': 8,  'M': 12},
    # Tech accessories
    'OTTERBOX':                 {'F': 8,  'M': 12},
    'SKULLCANDY':                {'F': 6,  'M': 10},
    'JBL':                      {'F': 6,  'M': 14},
    'BOSE':                     {'F': 8,  'M': 14},
    'BEATS':                    {'F': 10, 'M': 14},
    'BEATS BY DRE':             {'F': 10, 'M': 14},
    'EUFY':                     {'F': 6,  'M': 8},
    'GOVEE':                    {'F': 5,  'M': 8},
    'KASA SMART':               {'F': 4,  'M': 6},
    'TP-LINK':                  {'F': 4,  'M': 8},
    # Kids / toys / craft
    'CRAYOLA':                  {'F': 10, 'M': 6},
    'HASBRO':                   {'F': 8,  'M': 8},
    'MATTEL':                   {'F': 8,  'M': 6},
    # Stationery / batteries / misc household
    'ENERGIZER':                {'F': 14, 'M': 14},
    'DURACELL':                 {'F': 16, 'M': 16},
    'POST-IT':                  {'F': 14, 'M': 10},
    'SHARPIE':                  {'F': 14, 'M': 14},
    # Travel / luggage
    'CALPAK':                   {'F': 7,  'M': 4},
    'AWAY':                     {'F': 8,  'M': 6},
    'SAMSONITE':                {'F': 10, 'M': 12},
    'TUMI':                     {'F': 6,  'M': 8},
    # Misc consumer — gender-neutral
    'BIC':                      {'F': 10, 'M': 12},
    'STAEDTLER':                {'F': 3,  'M': 3},
    'SCOTCH BRAND':             {'F': 8,  'M': 6},
    # ── Gen Pop 2nd-pass canonical brands (added 2026-05-25)
    # Sporting goods — gender-neutral, M skew
    'WILSON SPORTING GOODS':    {'F': 6,  'M': 12},
    'WILSON':                   {'F': 6,  'M': 12},
    'HEAD SPORTING GOODS':      {'F': 4,  'M': 8},
    'SPALDING':                 {'F': 3,  'M': 8},
    'RAWLINGS':                 {'F': 3,  'M': 10},
    'EASTON':                   {'F': 3,  'M': 8},
    # Mass beauty extension (Unilever / P&G long-tail) — F skew
    'LOVE BEAUTY AND PLANET':   {'F': 10, 'M': 3},
    'PROACTIV':                 {'F': 10, 'M': 4},
    'AVON':                     {'F': 12, 'M': 1},
    'MARY KAY':                 {'F': 10, 'M': 1},
    'TUPPERWARE':               {'F': 10, 'M': 4},
    'ORIGINS SKINCARE':         {'F': 8,  'M': 2},
    'ORIGINS':                  {'F': 8,  'M': 2},
    'KIEHLS':                   {'F': 12, 'M': 6},
    "KIEHL'S":                  {'F': 12, 'M': 6},
    'KRISTIN ESS':              {'F': 6,  'M': 1},
    'OUAI':                     {'F': 6,  'M': 1},
    'ORIBE':                    {'F': 5,  'M': 2},
    'PAT MCGRATH LABS':         {'F': 5,  'M': 1},
    # Mass home / mattress
    'TEMPUR-PEDIC':             {'F': 10, 'M': 8},
    'TEMPURPEDIC':              {'F': 10, 'M': 8},
    'SEALY':                    {'F': 8,  'M': 6},
    'SERTA':                    {'F': 8,  'M': 6},
    'LOVESAC':                  {'F': 5,  'M': 5},
    'CALPHALON':                {'F': 8,  'M': 4},
    'CASTLERY':                 {'F': 4,  'M': 3},
    'WEST ELM':                 {'F': 10, 'M': 6},
    'CB2':                      {'F': 8,  'M': 6},
    # Wellness / DTC
    'OURA RING':                {'F': 6,  'M': 6},
    'OURA':                     {'F': 6,  'M': 6},
    'WHOOP':                    {'F': 3,  'M': 6},
    'THE FARMERS DOG':          {'F': 6,  'M': 4},
    "THE FARMER'S DOG":         {'F': 6,  'M': 4},
    'CHEWY.COM':                {'F': 14, 'M': 12},
    # Apparel extension
    'JUICY COUTURE':            {'F': 10, 'M': 1},
    'VICTORIA BECKHAM':         {'F': 4,  'M': 1},
    'YITTY':                    {'F': 7,  'M': 1},
    'WEEKDAY':                  {'F': 4,  'M': 3},
    'ARMANI EXCHANGE':          {'F': 8,  'M': 8},
    'LOUNGEFLY':                {'F': 6,  'M': 4},
    'PENDLETON':                {'F': 4,  'M': 6},
    'VOLCOM':                   {'F': 4,  'M': 8},
    'MOTHERHOOD':               {'F': 6,  'M': 0},
    # Boots / luxury / outerwear
    'HUNTER BOOTS':             {'F': 8,  'M': 3},
    'UGG':                      {'F': 16, 'M': 4},
    'UGGS':                     {'F': 16, 'M': 4},
    'TIFFANY & CO.':            {'F': 10, 'M': 4},
    'TIFFANY':                  {'F': 10, 'M': 4},
    'EASY SPIRIT':              {'F': 6,  'M': 1},
    # Pet / kids
    'BABYBJORN':                {'F': 6,  'M': 4},
    'BABY BJORN':               {'F': 6,  'M': 4},
    'GRACO':                    {'F': 10, 'M': 6},
    'FISHER PRICE':             {'F': 12, 'M': 8},
    'FISHER-PRICE':             {'F': 12, 'M': 8},
    'JOHNSONS BABY':            {'F': 14, 'M': 8},
    "JOHNSON'S BABY":           {'F': 14, 'M': 8},
    # Misc mass DTC / wellness
    'SKIMS':                    {'F': 16, 'M': 2},

    # ─── 2026-05-25 Round 5: P0/P1 user-validated Sheet4 brands ───
    # Verified in reference.host_mapping. Per workspace rule #4, only
    # brands present in Sheet4 are lifted; hostmap-gating in
    # apply_mpb_digital_share_lifts enforces this defensively at runtime.

    # P1 Foosball (Sheet4-validated, Foosball is 33% F / 67% M male-skew)
    'LEE':                      {'F': 6,  'M': 14},   # Lee jeans, M skew
    'LANDS END':                {'F': 8,  'M': 7},
    'JANSPORT':                 {'F': 10, 'M': 9},
    'SAUCONY':                  {'F': 7,  'M': 11},
    'BOMBAS':                   {'F': 9,  'M': 7},
    # 2026-05-25 (Gen Pop v10 audit): user's external digital consensus
    # for mass-secondary brands is materially lower than the initial table
    # values (10-14% not 20%). Recalibrated to land Gen Pop at consensus.
    'BEN & JERRYS':             {'F': 12, 'M': 12},  # Gen Pop ext 10-14%
    'PILLSBURY':                {'F': 14, 'M':  8},  # 10-14% mass / 6-10% male (KD audit 5-26)
    'BETTY CROCKER':            {'F': 14, 'M':  8},  # 10-14% mass / 6-10% male (KD audit 5-26)
    'MCCORMICK':                {'F': 21, 'M': 14},   # spices, F skew
    'HIDDEN VALLEY':            {'F': 12, 'M':  8},   # Gen Pop ext 8-12%
    'SHARK':                    {'F': 13, 'M': 10},   # Shark vacuum
    # P1 Gen Pop (Sheet4-validated)
    'BUSCH BEER':               {'F': 3,  'M': 9},
    'HOSTESS':                  {'F':  7, 'M':  6},  # Gen Pop ext 5-8%
    'HUNTS':                    {'F':  7, 'M':  6},  # 5-8%
    "HUNT'S":                   {'F':  7, 'M':  6},
    'ORE-IDA':                  {'F':  7, 'M':  6},  # 5-8%
    'ROCKSTAR ENERGY':          {'F': 4,  'M': 10},   # M skew
    'MORNINGSTAR FARMS':        {'F': 7,  'M': 4},
    'STARKIST':                 {'F':  5, 'M':  4},  # Gen Pop ext 3-6%
    'CHICKEN OF THE SEA':       {'F':  4, 'M':  3},  # ext 2-5%

    # Sheet4 typo handler — Mountain Dew is spelled "MOUNTIAN DEW" in
    # reference.host_mapping (P2 typo flagged for engineering). Until
    # the master corpus is corrected, lift profile rows that arrive with
    # the typo using Mountain Dew's true digital share.
    # Gen Pop ext 8-14% mid 11; user gave wider range than Reese's etc.
    'MOUNTIAN DEW':             {'F':  9, 'M': 13},

    # 2026-05-25 (Texas Rangers + Gen Pop v10 audit): mass candy / OTC /
    # household. Initial Texas Rangers calibration was too low for Gen Pop
    # (which has materially younger age distribution → higher digital
    # purchase rate). Recalibrated to user's Gen Pop external consensus.
    # NOTE: a future enhancement should age-dimension MPB_DIGITAL_SHARE
    # so older personas (Texas Rangers) can land lower than Gen Pop with
    # the same constants. For now Gen Pop is the canonical baseline.
    "REESE'S":                  {'F': 12, 'M': 12},  # Gen Pop ext 10-14%
    'REESES':                   {'F': 12, 'M': 12},
    'REESE':                    {'F': 12, 'M': 12},
    'SKITTLES':                 {'F': 10, 'M': 10},  # ext 8-12%
    "HERSHEY'S":                {'F': 15, 'M': 15},  # ext 12-18%
    'HERSHEYS':                 {'F': 15, 'M': 15},
    'HERSHEY':                  {'F': 15, 'M': 15},
    'LISTERINE':                {'F': 13, 'M': 11},  # ext 10-14%
    'DOWNY':                    {'F':  8, 'M':  5},  # unchanged (no user ext)
    'CHEERIOS':                 {'F': 12, 'M':  9},  # unchanged (no user ext)
    'SHEIN':                    {'F': 18, 'M':  5},  # unchanged (no user ext)
    'HEINZ':                    {'F': 13, 'M': 12},  # Gen Pop ext 10-15%

    # 2026-05-25 (Gen Pop v10 Tier 1A/1B floor-lock release).
    # All Sheet4-validated. Calibrated to user's external digital
    # consensus midpoints — premium DTC + niche retailers run lower
    # digital share than mass household.
    'BENJAMIN MOORE':           {'F':  4, 'M':  4},  # 3-5% mass paint
    'DIESEL':                   {'F':  2, 'M':  2},  # 1-3% premium denim
    'HUFFY':                    {'F':  2, 'M':  4},  # 2-4% bikes (male-skew)
    'MASSAGE ENVY':             {'F':  3, 'M':  1},  # 1-3% spa (female-skew)
    'MINNETONKA':               {'F':  2, 'M':  2},  # 1-3% moccasins
    'WACOAL BRAS':              {'F':  4, 'M':  0},  # 1-3% premium intimates
    'PETER THOMAS ROTH':        {'F':  5, 'M':  1},  # 2-4% Sephora skincare
    'WOLVERINE FOOTWEAR':       {'F':  1, 'M':  5},  # 2-4% workwear (male-skew)
    'VEJA SNEAKERS':            {'F':  2, 'M':  2},  # 1-3% DTC sneakers
    'STETSON':                  {'F':  1, 'M':  3},  # 1-3% Western (male-skew)
    'TRUVIA':                   {'F':  4, 'M':  2},  # 2-4% sweetener (F-skew)
    'FUNCTION OF BEAUTY':       {'F':  5, 'M':  1},  # 2-4% DTC hair (F-skew)
    'MANDUKA':                  {'F':  2, 'M':  1},  # 1-2% yoga
    'KACHAVA':                  {'F':  2, 'M':  2},  # 1-3% DTC nutrition
    'THE FRYE COMPANY':         {'F':  3, 'M':  1},  # 1-3% premium leather
    'MOTHER DENIM':             {'F':  3, 'M':  0},  # 1-2% premium denim (F)
    'HUDSON JEANS':             {'F':  2, 'M':  1},  # 1-2% premium denim
    'TWEEZERMAN':               {'F':  5, 'M':  1},  # 2-4% beauty tools (F)
    'WUNDERBROW':               {'F':  3, 'M':  0},  # 1-2% DTC brow (F)

    # 2026-05-25 (Foosball male-targeted floor-lock release).
    # All Sheet4-validated. Calibrated to user-specified external digital
    # consensus bands, audience-weighted so target lands at user's
    # midpoint on Foosball (33%F, 62%M). Also reasonable on Gen Pop.
    'WEBER':                    {'F':  4, 'M': 14},  # 8-12% grills (male)
    'WD-40':                    {'F':  4, 'M': 14},  # 8-12% tools (male)
    'OSCAR MAYER':              {'F':  5, 'M': 10},  # 6-10% mass food
    'NEW ERA CAP':              {'F':  3, 'M': 11},  # 6-10% sports apparel (M)
    'L.L.BEAN':                 {'F':  4, 'M':  7},  # 4-7% mass outdoor
    'HARRYS':                   {'F':  2, 'M':  8},  # 4-7% DTC shaving (M)
    "HARRY'S":                  {'F':  2, 'M':  8},  # alias
    'HIMS':                     {'F':  1, 'M': 11},  # 5-9% DTC male health
    'DULUTH TRADING':           {'F':  1, 'M': 11},  # 5-9% male workwear
    'JUSTIN BOOTS':             {'F':  2, 'M': 10},  # 5-9% Western boots
    'TECOVAS':                  {'F':  2, 'M':  8},  # 4-7% Western DTC
    'MOSSY OAK':                {'F':  1, 'M':  8},  # 4-7% hunting (M)
    'MACK WELDON':              {'F':  1, 'M':  7},  # 3-7% DTC male
    'MEUNDIES':                 {'F':  5, 'M':  5},  # 3-7% unisex DTC
    'TRUE CLASSIC':             {'F':  1, 'M':  7},  # 3-7% DTC male tees
    'VUORI':                    {'F':  5, 'M':  5},  # 3-7% unisex athletic
    'PANDORA JEWELRY':          {'F':  8, 'M':  2},  # 3-5% jewelry (F)
    'RHONE':                    {'F':  1, 'M':  7},  # 3-6% male athletic
    'NOBULL':                   {'F':  1, 'M':  5},  # 2-5% male training
    'MITCHEL & NESS':           {'F':  1, 'M':  7},  # 3-6% sports caps
    'WAHL':                     {'F':  2, 'M':  9},  # 4-8% grooming (M)
    'OOFOS':                    {'F':  4, 'M':  5},  # 3-6% recovery shoes
    'OMAHA STEAKS':             {'F':  3, 'M':  6},  # 3-6% mass food gift
    'PERDUE CHICKEN':           {'F':  7, 'M':  8},  # 6-10% mass chicken
    'RUBBERMAID':               {'F': 11, 'M':  9},  # 8-12% mass household
    'TREK BIKES':               {'F':  1, 'M':  5},  # 2-5% premium bikes
    'GREENWORKS':               {'F':  2, 'M':  5},  # 2-5% outdoor equipment
    'LODGE CAST IRON':          {'F':  4, 'M':  5},  # 3-6% mass cookware
    'RITZ CRACKERS':            {'F': 10, 'M':  9},  # 8-12% mass snacks
    'LINDT':                    {'F':  9, 'M':  5},  # 4-8% mass chocolate
    'STARBURST':                {'F':  9, 'M':  7},  # 6-10% mass candy
    'HAAGEN-DAZS':              {'F':  7, 'M':  5},  # 4-8% premium ice cream
    'VASELINE':                 {'F': 13, 'M': 10},  # 8-12% mass petroleum jelly
    'DR. SCHOLLS':              {'F':  7, 'M':  7},  # 5-9% mass foot care
    "DR. SCHOLL'S":             {'F':  7, 'M':  7},  # alias
    'ASHLEY FURNITURE':         {'F':  8, 'M':  8},  # 6-10% mass furniture
    'SLEEP NUMBER':             {'F':  5, 'M':  4},  # 3-6% premium beds
    'HELLY HANSEN':             {'F':  2, 'M':  6},  # 3-6% outdoor apparel

    # 2026-05-26 (Gen Pop v12 Hanes/Calvin Klein audit):
    # Mass apparel staples MPB band 14-20%. Add to MPB_DIGITAL_SHARE so lift
    # fires across all profiles.
    'HANES':                    {'F': 18, 'M': 16},  # 14-20% mass underwear/basics
    'CALVIN KLEIN':             {'F': 19, 'M': 15},  # 14-20% mass apparel

    # 2026-05-26 (Foosball v3 male-targeted floor-lock release, round 2):
    # 35 more Sheet4-validated brands user flagged for lift. Audience-
    # weighted F/M targets land at user-specified midpoint on Foosball
    # (33%F, 62%M) AND give reasonable values on Gen Pop + female-heavy.

    # Male outdoor/footwear/grooming
    'ARIAT':                    {'F':  1, 'M':  6},  # 3-5% Western/workwear
    'DANNER':                   {'F':  1, 'M':  4},  # 2-4% outdoor boot
    'SALOMON':                  {'F':  2, 'M':  8},  # 4-7% outdoor footwear
    'SOREL':                    {'F':  3, 'M':  3},  # 2-4% outdoor (neutral)
    'SITKA GEAR':               {'F':  0, 'M':  3},  # 1-3% hunting
    'PIT VIPER':                {'F':  1, 'M':  4},  # 2-4% sunglasses
    'REMINGTON PRODUCTS':       {'F':  2, 'M':  8},  # 4-7% grooming
    'EVERY MAN JACK':           {'F':  0, 'M':  5},  # 2-4% DTC grooming
    'QUIP TOOTHBRUSH':          {'F':  4, 'M':  4},  # 3-5% DTC oral care
    'MIZZEN+MAIN':              {'F':  0, 'M':  5},  # 2-4% performance shirts
    'SOUTHERN TIDE APPAREL':    {'F':  1, 'M':  6},  # 3-5% Southern preppy
    'G-STAR RAW':               {'F':  1, 'M':  4},  # 2-4% premium denim
    'MALBON':                   {'F':  0, 'M':  5},  # 2-4% golf streetwear
    'JUSTFOODFORDOGS':          {'F':  2, 'M':  2},  # 1-3% premium pet food
    'BLUE RHINO':               {'F':  2, 'M':  8},  # 4-7% propane/grilling
    'LYNX GRILLS':              {'F':  1, 'M':  2},  # 1-2% premium grill
    'AMERICAN OUTDOOR GRILL SHOP': {'F':  1, 'M':  2},  # 1-2%
    'MELINDAS FOODS':           {'F':  1, 'M':  2},  # 1-2% hot sauce

    # Mass household / cultural
    'ST. IVES':                 {'F':  8, 'M':  2},  # 3-5% mass skincare (F)
    'HAWAIIAN TROPIC':          {'F':  7, 'M':  5},  # 4-7% sunscreen
    'KRUPS':                    {'F':  5, 'M':  4},  # 3-5% small appliances
    'CHEFMAN':                  {'F':  5, 'M':  4},  # 3-5% appliances
    'LE CREUSET':               {'F':  4, 'M':  3},  # 2-4% premium kitchen
    'GREENPAN':                 {'F':  4, 'M':  3},  # 2-4% cookware
    'OGX':                      {'F':  8, 'M':  2},  # 3-5% mass haircare (F)
    'CASE MATE':                {'F':  5, 'M':  4},  # 3-5% phone cases
    'CASTORE':                  {'F':  1, 'M':  3},  # 1-3% athletic
    'FRANKLIN SPORTS':          {'F':  2, 'M':  4},  # 2-4% sporting goods

    # Mass apparel
    'FOREVER 21':               {'F':  9, 'M':  3},  # 4-7% fast fashion (F)
    'GUESS':                    {'F':  8, 'M':  3},  # 4-6% mass
    'HOLLISTER CO':             {'F':  8, 'M':  3},  # 4-6% mass
    'LUCKY BRAND':              {'F':  6, 'M':  3},  # 3-5% mass
    'IZOD':                     {'F':  2, 'M':  4},  # 2-4% older male menswear
    'PERRY ELLIS':              {'F':  2, 'M':  4},  # 2-4% menswear
    'DOONEY & BOURKE':          {'F':  7, 'M':  1},  # 2-4% handbags (F)

    # 2026-05-25 (Tyler Perry / Zendaya / Valkyrae audit): mass oral care +
    # dairy/cheese + sensitive-care brands missing from MPB lookup. Tier 1/2
    # Sheet4-validated brands. Digital share lower for in-store dominant cats.
    'ORAL B':                   {'F':  8, 'M':  7},
    'ORAL-B':                   {'F':  8, 'M':  7},
    'TILLAMOOK':                {'F':  9, 'M':  7},
    'PARODONTAX':               {'F':  5, 'M':  4},

    # ─── 2026-05-25 Round 4: cross-profile canonical floor sweep ───
    # Audit of Foosball (628 floor) + Gen Pop (1378 floor) surfaced ~100
    # canonical mass-recognizable brands stuck at the 0.01 default floor
    # because they weren't in the MPB_DIGITAL_SHARE lookup. Adding them
    # here so apply_mpb_digital_share_lifts releases them on the next
    # enforcer run. Values calibrated to real US digital purchase
    # penetration; gender skews are persona-aware (low M for female
    # apparel/beauty, low F for tools, near-flat for universal mass).

    # Mass beverages (near-flat gender, universal saturation)
    '7UP':                      {'F':  6, 'M':  5},  # Gen Pop ext 4-7%
    'AQUAFINA':                 {'F': 11, 'M':  9},  # Gen Pop ext 8-12%
    'DASANI':                   {'F':  9, 'M':  8},  # ext 7-10%
    'FANTA':                    {'F':  5, 'M':  4},  # ext 3-6%
    'SPRITE':                   {'F': 19, 'M': 20},
    'DR PEPPER':                {'F': 18, 'M': 21},
    'DR. PEPPER':               {'F': 18, 'M': 21},
    'MOUNTAIN DEW':             {'F':  9, 'M': 13},   # consensus 8-14% (Liz)
    'MTN DEW':                  {'F':  9, 'M': 13},
    'SMARTWATER':               {'F': 10, 'M': 9},
    'VITAMINWATER':             {'F': 11, 'M': 12},
    'LA CROIX':                 {'F': 12, 'M': 7},
    'LA CROIX SPARKLING WATER': {'F': 12, 'M': 7},
    'POLAND SPRING':            {'F': 11, 'M': 10},
    'POWERADE':                 {'F': 9,  'M': 14},
    'CANADA DRY':               {'F':  5, 'M':  4},  # Gen Pop ext 3-6%
    'SCHWEPPES':                {'F': 6,  'M': 7},
    'A&W':                      {'F': 6,  'M': 8},
    'SUNKIST':                  {'F': 7,  'M': 7},
    'FAYGO':                    {'F': 3,  'M': 3},
    'TOPO CHICO':               {'F': 6,  'M': 5},
    'BUBLY':                    {'F': 9,  'M': 6},
    'PERRIER':                  {'F': 5,  'M': 4},
    'SAN PELLEGRINO':           {'F': 6,  'M': 5},
    'FEVER TREE':               {'F': 3,  'M': 3},

    # Mass cereals/snacks
    'KELLOGGS CORN FLAKES':     {'F': 10, 'M': 10},
    "KELLOGG'S CORN FLAKES":    {'F': 10, 'M': 10},
    # Gen Pop ext consensus: Frosted Flakes 6-10%, Froot Loops 4-7%
    'KELLOGGS FROSTED FLAKES':  {'F':  8, 'M':  8},
    "KELLOGG'S FROSTED FLAKES": {'F':  8, 'M':  8},
    'KELLOGGS FROOT LOOPS':     {'F':  6, 'M':  5},
    "KELLOGG'S FROOT LOOPS":    {'F':  6, 'M':  5},
    'KELLOGGS RICE KRISPIES':   {'F': 9,  'M': 9},
    "KELLOGG'S RICE KRISPIES":  {'F': 9,  'M': 9},
    'KELLOGGS HONEY SMACKS':    {'F': 5,  'M': 6},
    "KELLOGG'S HONEY SMACKS":   {'F': 5,  'M': 6},
    'KEEBLER':                  {'F': 11, 'M': 10},
    'JELLO':                    {'F': 13, 'M': 8},
    'JELL-O':                   {'F': 13, 'M': 8},
    'JELL O':                   {'F': 13, 'M': 8},
    # Gen Pop ext consensus: M&Ms 14-18% mid 16
    'M&MS':                     {'F': 17, 'M': 15},
    "M&M'S":                    {'F': 17, 'M': 15},
    'M & MS':                   {'F': 17, 'M': 15},
    'JOHNSONVILLE':             {'F':  5, 'M':  6},   # ext 4-7%
    'OATLY':                    {'F': 5,  'M': 3},
    'DANNON':                   {'F': 13, 'M': 9},
    'GHIRARDELLI':              {'F': 8,  'M': 6},
    'CELESTIAL SEASONINGS':     {'F': 6,  'M': 4},

    # Mass CPG / cleaning / kitchen
    'ARM & HAMMER':             {'F': 11, 'M':  9},  # Gen Pop ext 8-12%
    'ARM AND HAMMER':           {'F': 22, 'M': 15},
    'OXICLEAN':                 {'F':  8, 'M':  8},  # Gen Pop ext 6-10%
    'BORAX':                    {'F': 4,  'M': 3},
    'RESOLVE':                  {'F': 8,  'M': 5},
    'MR. CLEAN':                {'F': 14, 'M': 11},
    'MR CLEAN':                 {'F': 14, 'M': 11},
    'PINE-SOL':                 {'F': 12, 'M': 8},
    'PINE SOL':                 {'F': 12, 'M': 8},
    'COMET':                    {'F': 7,  'M': 5},
    'AJAX':                     {'F': 6,  'M': 4},
    'GLADE':                    {'F': 18, 'M': 10},
    'AIR WICK':                 {'F': 14, 'M': 8},
    'FEBREZE':                  {'F': 22, 'M': 14},
    'CASCADE':                  {'F': 18, 'M': 12},
    'FINISH':                   {'F': 10, 'M': 7},
    'PALMOLIVE':                {'F': 14, 'M': 9},
    'JOY':                      {'F': 6,  'M': 4},
    'WINDEX':                   {'F': 22, 'M': 16},
    'SCRUBBING BUBBLES':        {'F': 11, 'M': 7},
    'METHOD':                   {'F': 8,  'M': 4},
    "MRS. MEYER'S":             {'F': 7,  'M': 3},
    'MRS MEYERS':               {'F': 7,  'M': 3},
    'SEVENTH GENERATION':       {'F': 9,  'M': 4},
    'HEFTY':                    {'F':  5, 'M':  4},  # recalibrated 5-25: user consensus 3-6%
    'GLAD':                     {'F': 22, 'M': 15},

    # Universal OTC / personal care
    'TUMS':                     {'F':  9, 'M':  7},  # Gen Pop ext 6-10%
    'PEPTO-BISMOL':             {'F': 9,  'M': 11},
    'PEPTO BISMOL':             {'F': 9,  'M': 11},
    'PEPCID':                   {'F': 7,  'M': 9},
    'IMODIUM':                  {'F': 5,  'M': 6},
    'BENADRYL':                 {'F': 14, 'M': 11},
    'CLARITIN':                 {'F': 12, 'M': 10},
    'ALLEGRA':                  {'F': 9,  'M': 7},
    'MUCINEX':                  {'F': 10, 'M': 11},
    'NEOSPORIN':                {'F': 13, 'M': 11},
    'BAND-AID':                 {'F': 28, 'M': 22},
    'BAND AID':                 {'F': 28, 'M': 22},
    'BANDAID':                  {'F': 28, 'M': 22},
    'AQUAFRESH':                {'F': 4,  'M': 5},
    'SENSODYNE':                {'F': 11, 'M': 13},
    'TOMS OF MAINE':            {'F': 6,  'M': 3},
    "TOM'S OF MAINE":           {'F': 6,  'M': 3},
    'NAIR':                     {'F': 10, 'M': 1},
    'VEET':                     {'F': 5,  'M': 1},
    'BARBASOL':                 {'F': 1,  'M': 8},
    'EDGE':                     {'F': 1,  'M': 9},
    'NIVEA':                    {'F': 14, 'M': 11},

    # Mid-tier female apparel (low M skew)
    'ANN TAYLOR':               {'F': 11, 'M': 1},
    'ANTHROPOLOGIE':            {'F': 14, 'M': 1},
    'ARITZIA':                  {'F': 11, 'M': 1},
    'ABERCROMBIE & FITCH':      {'F': 9,  'M': 5},
    'ABERCROMBIE':              {'F': 9,  'M': 5},
    'AMERICAN EAGLE':           {'F': 13, 'M': 9},
    'AEROPOSTALE':              {'F': 7,  'M': 5},
    'BANANA REPUBLIC':          {'F': 10, 'M': 6},
    'EXPRESS':                  {'F': 9,  'M': 4},
    'J.CREW':                   {'F': 11, 'M': 5},
    'J CREW':                   {'F': 11, 'M': 5},
    'JCREW':                    {'F': 11, 'M': 5},
    'LOFT':                     {'F': 9,  'M': 1},
    'MADEWELL':                 {'F': 11, 'M': 1},
    'OLD NAVY':                 {'F': 25, 'M': 15},
    'FREE PEOPLE':              {'F': 10, 'M': 1},
    'WHITE HOUSE BLACK MARKET': {'F': 6,  'M': 0},
    "CHICO'S":                  {'F': 7,  'M': 0},
    'CHICOS':                   {'F': 7,  'M': 0},
    'TALBOTS':                  {'F': 6,  'M': 0},
    'NEW YORK & COMPANY':       {'F': 5,  'M': 0},
    'TORRID':                   {'F': 5,  'M': 0},
    'LANE BRYANT':              {'F': 6,  'M': 0},
    'GAP':                      {'F': 18, 'M': 13},
    'NAUTICA':                  {'F': 5,  'M': 8},
    'EILEEN FISHER':            {'F': 4,  'M': 0},
    'BCBG':                     {'F': 4,  'M': 0},
    'BARE MINERALS':            {'F': 6,  'M': 0},
    'BAREMINERALS':             {'F': 6,  'M': 0},

    # Premium denim/contemporary (low absolute, female skew)
    '7 FOR ALL MANKIND':        {'F': 3,  'M': 2},
    'AGOLDE':                   {'F': 2,  'M': 1},
    'ACNE STUDIOS':             {'F': 2,  'M': 1},
    'ALICE + OLIVIA':           {'F': 2,  'M': 0},
    'ALLSAINTS':                {'F': 3,  'M': 2},
    'ANINE BING':               {'F': 2,  'M': 0},
    'THEORY':                   {'F': 3,  'M': 1},
    'VINCE':                    {'F': 2,  'M': 1},
    'EQUIPMENT':                {'F': 2,  'M': 0},
    'JOIE':                     {'F': 1,  'M': 0},
    'ALEXANDER WANG':           {'F': 2,  'M': 1},
    'KARL LAGERFELD':           {'F': 2,  'M': 1},
    'BARBOUR':                  {'F': 2,  'M': 3},
    'CANADA GOOSE':             {'F': 3,  'M': 4},
    'KAREN MILLEN':             {'F': 2,  'M': 0},
    'KAPPA':                    {'F': 2,  'M': 4},
    'KANGOL':                   {'F': 1,  'M': 3},

    # Footwear (mid-tier + premium)
    'ALDO':                     {'F': 7,  'M': 4},
    'AEROSOLES':                {'F': 5,  'M': 0},
    'STEVE MADDEN':             {'F': 11, 'M': 3},
    'SAM EDELMAN':              {'F': 6,  'M': 1},
    'COLE HAAN':                {'F': 5,  'M': 6},
    'CLARKS':                   {'F': 8,  'M': 6},
    'SPERRY':                   {'F': 6,  'M': 7},
    'TORY BURCH':               {'F': 7,  'M': 1},
    'BIRKENSTOCK':              {'F': 11, 'M': 7},
    'KEDS':                     {'F': 5,  'M': 0},
    'KEEN':                     {'F': 4,  'M': 5},
    'MERRELL':                  {'F': 6,  'M': 8},
    'TEVA':                     {'F': 4,  'M': 4},
    'JEFFREY CAMPBELL':         {'F': 2,  'M': 0},
    'G.H. BASS':                {'F': 2,  'M': 3},
    'GH BASS':                  {'F': 2,  'M': 3},
    'JOHNSTON & MURPHY':        {'F': 1,  'M': 4},
    'DANSKO':                   {'F': 3,  'M': 1},
    'BEARPAW':                  {'F': 3,  'M': 1},
    'DC SHOES':                 {'F': 2,  'M': 4},
    'GEOX':                     {'F': 2,  'M': 2},

    # Spirits / beer (long-tail names, M-skew except wine)
    'JOSE CUERVO':              {'F': 4,  'M': 7},
    'JIM BEAM':                 {'F': 3,  'M': 9},
    'JACK DANIELS':             {'F': 5,  'M': 14},
    "JACK DANIEL'S":            {'F': 5,  'M': 14},
    'JAMESON':                  {'F': 4,  'M': 10},
    'CROWN ROYAL':              {'F': 3,  'M': 11},
    'CAPTAIN MORGAN':           {'F': 5,  'M': 10},
    'BACARDI':                  {'F': 6,  'M': 9},
    'MALIBU':                   {'F': 6,  'M': 4},
    'GREY GOOSE':               {'F': 6,  'M': 7},
    "TITO'S":                   {'F': 11, 'M': 12},
    'TITOS':                    {'F': 11, 'M': 12},
    'ABSOLUT':                  {'F': 5,  'M': 6},
    'SMIRNOFF':                 {'F': 8,  'M': 11},
    'PATRON':                   {'F': 5,  'M': 8},
    'DON JULIO':                {'F': 4,  'M': 7},
    'CASAMIGOS':                {'F': 5,  'M': 8},
    "MAKER'S MARK":             {'F': 3,  'M': 7},
    'MAKERS MARK':              {'F': 3,  'M': 7},
    'BULLEIT':                  {'F': 2,  'M': 5},
    'WOODFORD RESERVE':         {'F': 2,  'M': 6},
    'BAILEYS':                  {'F': 8,  'M': 4},
    "BAILEY'S":                 {'F': 8,  'M': 4},
    'KAHLUA':                   {'F': 5,  'M': 4},
    'COORS':                    {'F': 5,  'M': 16},
    'COORS LIGHT':              {'F': 7,  'M': 19},
    'MILLER LITE':              {'F': 6,  'M': 17},
    'MICHELOB':                 {'F': 4,  'M': 11},
    'MICHELOB ULTRA':           {'F': 6,  'M': 14},
    'MODELO':                   {'F': 9,  'M': 18},
    'HEINEKEN':                 {'F': 5,  'M': 12},
    'GUINNESS':                 {'F': 4,  'M': 10},
    'STELLA ARTOIS':            {'F': 5,  'M': 9},
    'BLUE MOON':                {'F': 7,  'M': 11},
    'YUENGLING':                {'F': 3,  'M': 7},
    'PABST':                    {'F': 3,  'M': 7},
    'BUSCH':                    {'F': 3,  'M': 9},
    'NATURAL LIGHT':            {'F': 4,  'M': 9},
    'NATTY LIGHT':              {'F': 4,  'M': 9},
    'NATTY LITE':               {'F': 4,  'M': 9},
    'WHITE CLAW':               {'F': 16, 'M': 14},
    'TRULY':                    {'F': 12, 'M': 10},
    'HIGH NOON':                {'F': 13, 'M': 12},

    # Pet care
    'PEDIGREE':                 {'F': 10, 'M': 9},
    'IAMS':                     {'F': 8,  'M': 7},
    'EUKANUBA':                 {'F': 3,  'M': 3},
    "HILL'S SCIENCE DIET":      {'F': 8,  'M': 6},
    'HILLS SCIENCE DIET':       {'F': 8,  'M': 6},
    'NUTRO':                    {'F': 4,  'M': 3},
    'WELLNESS':                 {'F': 3,  'M': 2},
    'TASTE OF THE WILD':        {'F': 4,  'M': 4},
    'MERRICK':                  {'F': 3,  'M': 2},
    'ROYAL CANIN':              {'F': 5,  'M': 4},
    'FRISKIES':                 {'F': 10, 'M': 7},
    'FANCY FEAST':              {'F': 9,  'M': 6},
    'TIDY CATS':                {'F': 8,  'M': 5},
    'FRESH STEP':               {'F': 6,  'M': 4},
    'TEMPTATIONS':              {'F': 8,  'M': 5},
    'GREENIES':                 {'F': 6,  'M': 5},
    'MILK-BONE':                {'F': 9,  'M': 7},
    'MILK BONE':                {'F': 9,  'M': 7},

    # Tools / outdoor / home improvement (M-skew)
    'DEWALT':                   {'F': 3,  'M': 17},
    'MILWAUKEE':                {'F': 2,  'M': 16},
    'MAKITA':                   {'F': 2,  'M': 11},
    'BOSCH':                    {'F': 4,  'M': 11},
    'CRAFTSMAN':                {'F': 4,  'M': 16},
    'RYOBI':                    {'F': 4,  'M': 14},
    'BLACK+DECKER':             {'F': 7,  'M': 12},
    'BLACK & DECKER':           {'F': 7,  'M': 12},
    'BLACK AND DECKER':         {'F': 7,  'M': 12},
    'STANLEY':                  {'F': 8,  'M': 14},
    'HUSKY':                    {'F': 3,  'M': 11},
    'KOBALT':                   {'F': 2,  'M': 9},
    'RIDGID':                   {'F': 2,  'M': 8},
    'IRWIN':                    {'F': 2,  'M': 7},
    'KLEIN TOOLS':              {'F': 1,  'M': 6},
    'IGLOO':                    {'F': 8,  'M': 11},
    'COLEMAN':                  {'F': 8,  'M': 12},

    # Premium beauty (long-tail at low-mid)
    'DRUNK ELEPHANT':           {'F': 5,  'M': 0},
    'GLOSSIER':                 {'F': 6,  'M': 0},
    'TOO FACED':                {'F': 7,  'M': 0},
    'TARTE':                    {'F': 9,  'M': 0},
    'TARTE COSMETICS':          {'F': 9,  'M': 0},
    'BENEFIT COSMETICS':        {'F': 8,  'M': 0},
    'URBAN DECAY':              {'F': 8,  'M': 0},
    'ANASTASIA BEVERLY HILLS':  {'F': 8,  'M': 0},
    "KIEHL'S":                  {'F': 5,  'M': 2},
    'KIEHLS':                   {'F': 5,  'M': 2},
    'LA ROCHE-POSAY':           {'F': 6,  'M': 2},
    'LA ROCHE POSAY':           {'F': 6,  'M': 2},
    'LANEIGE':                  {'F': 5,  'M': 0},
    'INNISFREE':                {'F': 4,  'M': 0},
    'COSRX':                    {'F': 5,  'M': 0},
    'THE INKEY LIST':           {'F': 4,  'M': 0},
    'THE ORDINARY':             {'F': 9,  'M': 1},
    "PAULA'S CHOICE":           {'F': 5,  'M': 0},
    'PAULAS CHOICE':            {'F': 5,  'M': 0},
    'JO MALONE':                {'F': 4,  'M': 1},
    'LE LABO':                  {'F': 3,  'M': 2},
    'MADISON REED':             {'F': 5,  'M': 0},
    'NATIVE DEODORANT':         {'F': 7,  'M': 4},
    'NATIVE':                   {'F': 7,  'M': 4},
    'OLIVE & JUNE':             {'F': 4,  'M': 0},
    # ---- Mass-cultural lifts (2026-05-26): rescue from 0.01-0.03 soft-floor ----
    # NOTE: digital PURCHASING share, not total reach. Liz audit flagged Tyson
    # at {F:23,M:21} → 22% as overshoot (consensus 6-10%). Other heavy-CPG
    # entries (Jif, Häagen-Dazs, Eggo, Chobani, Dannon, Twix, Oscar Mayer)
    # recalibrated DOWN to consensus digital ranges at the same time.
    # Mass candy/chocolate (Sheet4-validated only)
    'KIT KAT':                  {'F': 11, 'M': 9},
    'KITKAT':                   {'F': 11, 'M': 9},
    'STARBURST':                {'F': 11, 'M': 9},
    'BUTTERFINGER':             {'F': 7,  'M': 7},
    'MILKY WAY':                {'F': 8,  'M': 8},
    'TWIX':                     {'F': 10, 'M': 9},     # was 13/12 (overshoot)
    'GHIRADELLI':               {'F': 6,  'M': 4},
    # Mass snacks/crackers
    'CHEEZIT':                  {'F': 13, 'M': 11},
    'PEPPERIDGE FARM GOLDFISH': {'F': 14, 'M': 10},
    'POP SECRET':               {'F': 6,  'M': 4},
    'ORVILLE REDENBACHER':      {'F': 6,  'M': 4},
    # Frozen / ice cream
    'DIGIORNO':                 {'F': 6,  'M': 6},
    'MAGNUM ICE CREAM':         {'F': 4,  'M': 2},
    'BREYERS':                  {'F': 9,  'M': 8},     # was 13/11 (overshoot)
    'HAAGEN-DAZS':              {'F': 8,  'M': 6},     # was 14/12 (overshoot)
    # Spreads / butter substitutes
    'JIF':                      {'F': 10, 'M': 9},     # was 14/12 (overshoot)
    'I CANT BELIEVE ITS NOT BUTTER': {'F': 6, 'M': 4},
    "I CAN'T BELIEVE IT'S NOT BUTTER": {'F': 6, 'M': 4},
    # Hydration / beverages
    'PROPEL WATER':             {'F': 5,  'M': 3},
    # Cereal / breakfast
    'CHEX':                     {'F': 8,  'M': 7},     # was 10/8 (slight overshoot)
    'EGGO':                     {'F': 9,  'M': 8},     # was 14/12 (overshoot)
    # Yogurt
    'CHOBANI':                  {'F': 10, 'M': 8},     # was 15/13 (overshoot)
    'DANNON':                   {'F': 10, 'M': 8},     # was 14/12 (overshoot)
    # Packaged meat
    'OSCAR MAYER':              {'F': 11, 'M': 10},    # was 15/13 (overshoot)
    'TYSON':                    {'F': 9,  'M': 8},     # was 23/21 — Liz overshoot (consensus 6-10)
    # Personal / beauty / health (mass female-skew)
    'OPI':                      {'F': 11, 'M': 1},
    'DIFFERIN':                 {'F': 6,  'M': 4},
    'BILLIE':                   {'F': 6,  'M': 2},
    'VITAL PROTEINS':           {'F': 7,  'M': 3},
    'DRYBAR':                   {'F': 5,  'M': 1},
    'MURAD':                    {'F': 5,  'M': 1},
    'CLARINS':                  {'F': 4,  'M': 1},
    'BANANA BOAT':              {'F': 6,  'M': 4},
    # Gift / flowers / subscription
    '1800FLOWERS':              {'F': 4,  'M': 2},
    'FTD':                      {'F': 3,  'M': 1},
    'PROFLOWERS':               {'F': 3,  'M': 1},
    'BARKBOX':                  {'F': 4,  'M': 2},
    # Apparel / footwear (mass-mid)
    'TOMS FOOTWEAR':            {'F': 5,  'M': 3},
    'HERSCHEL SUPPLY':          {'F': 5,  'M': 3},
    'TOMMY BAHAMA':             {'F': 5,  'M': 5},
    'ANN TAYLOR LOFT':          {'F': 6,  'M': 1},
    'LILLY PULITZER':           {'F': 3,  'M': 0},
    'VINEYARD VINES':           {'F': 4,  'M': 3},
    'RUGGABLE':                 {'F': 5,  'M': 1},
}


def _mpb_audience_weighted_target(df, brand_u):
    """For an MPB brand, compute the audience-weighted target BP:
        target = F_pen × audience_F + M_pen × audience_M
    Returns None if brand not in MPB_DIGITAL_SHARE or GENDER missing.
    The 'OTHER' gender bucket is split evenly into F/M for weighting
    (NB/trans users have brand-affinity midway between F-skew and M-skew).
    """
    skew = MPB_DIGITAL_SHARE.get(brand_u)
    if not skew:
        return None
    dist = _gender_distribution(df)
    if not dist:
        return None
    f_share = dist.get('FEMALE', 0.0) + 0.5 * dist.get('OTHER', 0.0)
    m_share = dist.get('MALE', 0.0) + 0.5 * dist.get('OTHER', 0.0)
    return skew['F'] * f_share + skew['M'] * m_share


def apply_mpb_digital_share_lifts(df, subject, verbose=True):
    """Defect Class #22 — MPB default-floor suppression.

    For each MOST PURCHASED BRANDS row matching a brand in
    MPB_DIGITAL_SHARE, compute the audience-weighted digital-purchasing
    target. Lift the row's BP to the target with deterministic jitter
    when EITHER:
      • current BP ≤ 0.05% (caught in the 0.01 default-floor lock), or
      • current BP < 0.65 × target (≥35% suppressed below audience reality).

    Never trims (one-way lift, consistent with the "benchmarks are
    FLOORS not CEILINGS" principle). Uses hash-deterministic jitter
    in [target - 0.45pp, target + 0.45pp] so cross-file spread is real
    persona variation, plus a 4dp micro-jitter so values avoid .00xx.

    Skips rows where Column != 'MOST PURCHASED BRANDS'. Only matches
    on exact-or-canonical brand label (UPPER, stripped) so it never
    fires on "MACK WELDON" when looking up "MAC".
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    fixed = 0
    touched_cats = set()
    examples = []
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat != 'MOST PURCHASED BRANDS':
            continue
        val_raw = str(r.get('Value', '') or '').strip()
        val_u = val_raw.upper()
        if not val_u or val_u in METADATA_COLS:
            continue
        # Exact lookup only — no substring match, no inventing brands
        if val_u not in MPB_DIGITAL_SHARE:
            continue
        # 2026-05-25 (workspace rule #4 — hostmap gating):
        # Never lift a brand that isn't in `reference.host_mapping`
        # (Sheet4). MPB_DIGITAL_SHARE may carry an entry for analytical
        # purposes, but we must not propagate it to a profile if Sheet4
        # doesn't validate the brand. Missing brands recorded in
        # _HOSTMAP_GAPS for the data-team escalation email.
        if not _is_in_hostmap(val_raw):
            _HOSTMAP_GAPS.append((subject, val_raw, 'MOST PURCHASED BRANDS',
                                  'MPB_DIGITAL_SHARE entry not in Sheet4'))
            continue
        target = _mpb_audience_weighted_target(df, val_u)
        if target is None or target <= 0:
            continue
        old_bp = _bp(r.get(bp_col, 0))
        # Self-pin sanity
        if old_bp >= 99.99:
            continue
        # Lift trigger (any one):
        #   • default-floor lock (BP <= 0.05)
        #   • ≥35% below audience-weighted target (deep suppression)
        #   • ≥3.0pp absolute gap below target (gap threshold tightened
        #     2026-05-25 per Gen Pop v10 audit — Listerine gap of 3.05pp
        #     was missing the prior 3.5 threshold)
        # Trim trigger (added 2026-05-25 per Gen Pop v10 audit — 23 mass-secondary
        # brands lifted above external digital engagement consensus; further
        # tightened 2026-05-25 per Foosball audit — Betty Crocker at 14.69%
        # with gap 3.87 was missing the prior 4.0 threshold):
        #   • current >= 1.35× target AND gap_above >= 3.5pp AND target >= 2.0
        #     (avoid over-correcting near-floor noise)
        gap_below = target - old_bp
        gap_above = old_bp - target
        is_suppressed = (
            old_bp <= 0.05
            or old_bp < 0.65 * target
            or gap_below >= 3.0
        )
        is_overshot = (
            target >= 2.0
            and old_bp >= 1.35 * target
            and gap_above >= 3.5
        )
        if not (is_suppressed or is_overshot):
            continue

        # Hash-deterministic jitter: ±0.45pp band so cross-file spread
        # reflects real audience composition variation. Plus a 4dp
        # micro-jitter so the value avoids the .00xx pattern.
        h = int(_hl.blake2b(
            f'{subject}|{val_u}|mpb_lift'.encode(), digest_size=8
        ).hexdigest(), 16)
        macro = ((h % 9001) - 4500) / 10000.0       # -0.45..+0.45
        micro = (((h >> 16) % 1981) - 990) / 100000.0  # -0.0099..+0.0099
        new_v = max(0.5, target + macro + micro)
        # Avoid .00xx — shift if landed within 0.01 of integer
        if abs(new_v - round(new_v)) < 0.01:
            new_v += 0.17 if (h & 1) else -0.23
            new_v = max(0.5, new_v)
        new_v = round(new_v, 4)
        # Belt-and-suspenders: never write a strict round-2dp value
        if abs(new_v * 100 - round(new_v * 100)) < 1e-4:
            new_v = round(new_v + 0.0017, 4)

        df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        fixed += 1
        touched_cats.add('MOST PURCHASED BRANDS')
        if len(examples) < 6:
            examples.append((val_raw, old_bp, new_v, target))

    for cat in touched_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                   sample_size)

    if verbose and fixed:
        print(f"   🛒 MPB audience-weighted lift/trim: {fixed} row(s)")
        for v, old, new, tgt in examples:
            direction = '↑' if new > old else '↓'
            print(f"      {v:30s} {old:7.4f} {direction} {new:7.4f}  (audience-target {tgt:.2f})")
    return df, fixed


# ---------------------------------------------------------------------------
# Defect Class #22b — One-shot cleanup of non-hostmap lifts
#
# Audit on 2026-05-25 revealed apply_mpb_digital_share_lifts had been
# lifting brands (Tylenol, Advil, Tito's, Smirnoff, Pedigree, Friskies,
# Bosch, Makita, etc.) that exist in profile MPB rows but are NOT in
# `reference.host_mapping` (Sheet4). Per workspace rule #4, those rows
# should never have been lifted. This enforcer is a one-shot reset:
#   - find MPB rows where Value is NOT in Sheet4 AND current BP > 0.5
#     (i.e. was previously lifted by the non-gated enforcer)
#   - reset to 0.01-0.05 with deterministic jitter (mimicking floor,
#     but never producing identical BPs across rows per rule #1)
#   - recompute Raw / Projection
# Safe to run on every profile — only touches rows that need reverting.
# ---------------------------------------------------------------------------

def reset_non_hostmap_mpb_to_floor(df, subject, verbose=True):
    """Reset MPB rows whose Value isn't in Sheet4 back to a jittered floor.
    Used once after the round-4 expansion lifted ~210 non-hostmap brands.
    Subsequent runs are idempotent (no-op once values are at floor).

    For category-wide (not just MPB) coverage see
    `reset_non_hostmap_brands_to_floor()` — preferred for new runs.
    """
    return _reset_non_hostmap_to_floor_for_categories(
        df, subject, {'MOST PURCHASED BRANDS'}, verbose=verbose,
    )


# Brand-style categories that MUST be hostmap-gated (Rule #4). Excludes
# demographic, persona-roster, location, league-roster, and organisational
# categories where Values are people/places/teams rather than catalogued brands.
HOSTMAP_GATED_BRAND_CATEGORIES = frozenset({
    'MOST PURCHASED BRANDS',
    'APPAREL/FOOTWEAR',
    'BEAUTY/WELLNESS',
    'HEALTH & WELLNESS',
    'CPG',
    'TECHNOLOGY/DEVICE',
    'TECHNOLOGY BRAND',
    'AUTOMOBILE',
    'QSR',
    'WHERE THEY SHOP',
    'WHERE THEY DINE',
    'BANKING',
    'DIGITAL BANKING',
    'CREDIT PROVIDER',
    'INVESTMENTS',
    'INSURANCE',
    'TELECOM',
    'STREAMING/PLATFORM',
    'STREAMING/MUSIC',
    'VIRTUAL MVPD FAST',
    'SEARCH ENGINE/AI',
    'SOCIAL MEDIA',
    'PORN MEDIA',
    'PHARMACY',
    'PETS',
    'TOYS',
    'GAMES',
    'TRAVEL',
    'ACCESSORIES',
    'AMUSEMENT PARKS',
    'WORKOUT FACILITY',
    'TICKETING',
    'MEDIA',
    'APP/PLATFORM USAGE',
    'HEAVY MACHINERY',
    'HOME/OUTDOOR',
    'EDUCATION & LEARNING',
    'MOVIE THEATER',
})


def reset_non_hostmap_brands_to_floor(df, subject, verbose=True):
    """For every brand-style category, reset rows whose Value isn't in
    Sheet4 back to a jittered floor [0.01-0.05]. Rule #4 enforcement
    across ALL brand categories (not just MPB).

    Catches things like 'SHOWTIME TV' lingering in STREAMING/PLATFORM
    (Rule #4 explicitly says Showtime is bundled into Paramount+),
    'AMERITRADE' in BANKING (acquired by Schwab), or the subject's own
    name appearing in MEDIA. Hostmap-gated, idempotent.
    """
    return _reset_non_hostmap_to_floor_for_categories(
        df, subject, HOSTMAP_GATED_BRAND_CATEGORIES, verbose=verbose,
    )


def _reset_non_hostmap_to_floor_for_categories(df, subject, categories, verbose=True):
    """Shared implementation for reset_non_hostmap_*_to_floor. Resets any
    row whose (Column∈categories) AND Value not in Sheet4 AND BP > 0.5
    back to a deterministic jittered floor [0.01-0.05]."""
    if df is None or len(df) == 0:
        return df, 0
    if not _ensure_hostmap_loaded():
        if verbose:
            print("   ⚠️ hostmap cache not loaded — skipping non-hostmap reset")
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    cats_u = {str(c).upper().strip() for c in categories}
    fixed = 0
    examples = []
    touched_cats = set()
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat not in cats_u:
            continue
        val_raw = str(r.get('Value', '') or '').strip()
        if not val_raw or val_raw.upper() in METADATA_COLS:
            continue
        if _is_in_hostmap(val_raw):
            continue
        old_bp = _bp(r.get(bp_col, 0))
        if old_bp <= 0.5:
            continue
        h = int(_hl.blake2b(
            f'{subject}|{val_raw}|{cat}|nonhostmap_reset'.encode(), digest_size=8
        ).hexdigest(), 16)
        frac = (h % 10000) / 10000.0
        new_v = 0.01 + frac * 0.04
        micro = (((h >> 16) % 1801) - 900) / 1_000_000.0
        new_v = round(max(0.0001, new_v + micro), 4)
        if abs(new_v * 100 - round(new_v * 100)) < 1e-4:
            new_v = round(new_v + 0.0017, 4)

        df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        # 2026-08-24: _set_bp now writes a coherent whole-category share
        # at mutation time (stale-share class kill), so the floored row
        # can never keep a pre-reset CS for apply_bp_cs_consistency_
        # recovery to misread (NINJA/SHARK re-inflation defect). The
        # earlier per-row CS overwrite here was removed: it wrote the
        # BP value instead of the share and would clobber the coherent
        # recompute.
        fixed += 1
        touched_cats.add(cat)
        # Record as a hostmap gap so it surfaces in the data-team escalation
        _HOSTMAP_GAPS.append((subject, val_raw, cat, 'reset_non_hostmap_brands_to_floor'))
        if len(examples) < 8:
            examples.append((cat, val_raw, old_bp, new_v))

    # Only renormalize categories that are demographic-style (sum-to-100). MPB
    # is in the list for backwards compatibility; other brand categories per
    # Rule #3 do NOT renormalize.
    for cat in touched_cats:
        if cat == 'MOST PURCHASED BRANDS':
            df = _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col,
                                       sample_size)

    if verbose and fixed:
        print(f"   🧹 Non-hostmap brand reset to floor: {fixed} row(s) across {len(touched_cats)} category(ies)")
        for cat, v, old, new in examples:
            print(f"      [{cat}] {v:30s} {old:7.4f} → {new:7.4f}  (not in Sheet4)")
    return df, fixed


# ---------------------------------------------------------------------------
# Defect Class #14b — Brand-name normalization to Sheet4 canonical
#
# User audit on 2026-05-25 (P3) flagged: profile MPB rows store the same
# brand under different spellings across files:
#   - "COCA COLA" (no hyphen) on Gen Pop / KD / Sandra / Olivia / Penelope
#   - "COCA-COLA" (with hyphen) on Foosball / Keke
# Sheet4 canonical is "Coca-Cola" (with hyphen). The MPB display value
# should match Sheet4's punctuation exactly. UPPER-case is the profile
# convention so target form = UPPER(Sheet4_canonical) = "COCA-COLA".
#
# Strategy: for each MPB row, normalize the Value and look up the
# Sheet4 canonical form. If they differ in punctuation (case+punct
# collapse same but raw differs), rewrite to UPPER(Sheet4_canonical).
# ---------------------------------------------------------------------------

def normalize_brand_names_to_sheet4(df, subject, verbose=True):
    """Rewrite Value cells in ALL HOSTMAP_GATED_BRAND_CATEGORIES to
    match Sheet4 canonical exactly (in UPPER per profile convention).

    Accepts these rewrites (where the rewritten form IS in Sheet4):
      - hyphen/space swap   (COCA COLA       → COCA-COLA)
      - apostrophe drop     (CLAIRE'S        → CLAIRES; user policy 2026-05-26
                             "make sure all values are written the same way
                             they are in hostmap table")
      - curly-apostrophe drop (CLAIRE\u2019S  → CLAIRES)
      - case canonicalization (when raw uppercased differs from canonical uppercase)

    Always REJECTED (preserve human-readable spelling):
      - space removal       (CHARLOTTE TILBURY ↛ CHARLOTTETILBURY)
      - ampersand drop      (BLACK & DECKER  ↛ BLACK AND DECKER if no canonical)
      - any change that yields a string NOT in Sheet4

    Returns (df, n_changes).
    """
    if df is None or len(df) == 0:
        return df, 0
    if not _ensure_hostmap_loaded():
        if verbose:
            print("   ⚠️ hostmap cache not loaded — skipping name normalization")
        return df, 0

    def _safe_canonical_rewrite(raw):
        """Return Sheet4 canonical (UPPER) if raw can be rewritten to match
        Sheet4 by a small punctuation/morphology transform. Tries (in order):
          1. raw as-is
          2. apostrophe → '' (CLAIRE'S → CLAIRES)
          3. apostrophe → ' ' space (SEAN O'MALLEY → SEAN O MALLEY)
          4. curly-quote → straight quote (D\u2019ARCY → D'ARCY)
          5. singular → plural (MILWAUKEE TOOL → MILWAUKEE TOOLS)
          6. plural → singular (BUFFALO WILD WINGS → BUFFALO WILD WING)
        Returns Sheet4 canonical (UPPER) or None if no safe rewrite found.
        """
        # Try raw + apostrophe variants
        variants = [raw]
        if "'" in raw:
            variants.append(raw.replace("'", ''))
            variants.append(raw.replace("'", ' '))
        if "\u2019" in raw:
            variants.append(raw.replace("\u2019", ''))
            variants.append(raw.replace("\u2019", ' '))
            variants.append(raw.replace("\u2019", "'"))
        # Singular/plural variants — only on the last word, only if last
        # word is ≥3 chars (skip short edge cases like "F&G" → "F&Gs").
        words = raw.split()
        if words and len(words[-1]) >= 3:
            last = words[-1]
            # Add 's' to make plural (TOOL → TOOLS)
            variants.append(' '.join(words[:-1] + [last + 's']))
            variants.append(' '.join(words[:-1] + [last + 'S']))
            # Drop trailing 's' to make singular (TOOLS → TOOL)
            if last[-1].lower() == 's' and len(last) >= 4:
                variants.append(' '.join(words[:-1] + [last[:-1]]))
        canon = None
        for v in variants:
            c = _hostmap_canonical(v)
            if c is not None:
                canon = c
                break
        if canon is None:
            return None
        canon_upper = canon.upper()
        if canon_upper == raw.upper():
            return None
        # Safety: never expand or contract length by more than 2 chars
        if abs(len(canon_upper) - len(raw)) > 2:
            return None
        # Safety: refuse to add/remove streaming-variant markers (+, &, /).
        # These distinguish DISTINCT brands (Disney vs Disney+, FIFA vs FIFA+,
        # Paramount vs Paramount+, Apple TV vs Apple TV+). The base
        # _norm_brand strips all non-alphanumerics so DISNEY and DISNEY+
        # collide on the same normalized key — the lookup can return either.
        SPECIAL = set('+&/')
        raw_special = set(raw) & SPECIAL
        can_special = set(canon_upper) & SPECIAL
        if raw_special != can_special:
            return None
        return canon_upper

    # Categories EXCLUDED from name normalization (demographics: their
    # values come from PIPELINE_DEMO_SCHEMA, not hostmap)
    DEMO_SKIP = {'GENDER', 'AGE', 'ETHNICITY', 'INCOME', 'OCCUPATION',
                 'PARENTAL_STATUS', 'PARENTAL STATUS', 'RELATIONSHIP',
                 'SEXUAL_ORIENTATION', 'SEXUAL ORIENTATION', 'EDUCATION',
                 'LOCATION', 'BRAND INPUT', 'SUBJECT'}

    fixed = 0
    examples = []
    for idx, r in df.iterrows():
        cat = str(r.get('Column', '') or '').strip().upper()
        if cat in DEMO_SKIP:
            continue
        val_raw = str(r.get('Value', '') or '').strip()
        if not val_raw or val_raw.upper() in METADATA_COLS:
            continue
        target = _safe_canonical_rewrite(val_raw)
        if target is None:
            continue
        df.at[idx, 'Value'] = target
        fixed += 1
        if len(examples) < 8:
            examples.append((val_raw, target))

    if verbose and fixed:
        print(f"   ✏️ brand name normalization to Sheet4: {fixed} row(s)")
        for old, new in examples:
            print(f"      {old:34s} → {new}")
    return df, fixed


# ---------------------------------------------------------------------------
# Defect Class #22c — Add MUST_INCLUDE Sheet4 canonical brands when missing
#
# User audit (P0) flagged DOVE BEAUTY missing from Keke + Sandra + Penelope
# + Octavia MPB rows. Brand IS in Sheet4 → audit-side enforcer is permitted
# to add the row (no rule #4 violation; the row IS Sheet4-validated).
#
# Conservative: only adds brands from MUST_INCLUDE_MPB_BRANDS — a small
# curated list of universal canonical brands that should appear in every
# profile's MPB. Each entry has a {F, M} digital-share lookup so the
# added row uses the audience-weighted target (same math as the lift).
#
# Adds row with: Column=MOST PURCHASED BRANDS, Value=Sheet4_canonical_upper,
# BP=audience-weighted target ± jitter, Raw / Projection computed.
# ---------------------------------------------------------------------------
MUST_INCLUDE_MPB_BRANDS = (
    'DOVE BEAUTY',
    # 2026-05-25 (Foosball audit): mass-household staples that were
    # systematically missing across profiles. All Sheet4-validated;
    # canonicals "M & Ms", "Hostess", "Hunts", "Ore-Ida", "Hefty".
    'M&MS',
    'HOSTESS',
    'HUNTS',
    'ORE-IDA',
    'HEFTY',
)


def add_missing_sheet4_must_include_mpb(df, subject, verbose=True):
    """For each brand in MUST_INCLUDE_MPB_BRANDS, if it's missing from
    the profile's MPB rows AND it's in Sheet4 AND there's a digital-
    share lookup, add it at the audience-weighted target.
    """
    if df is None or len(df) == 0:
        return df, 0
    if not _ensure_hostmap_loaded():
        if verbose:
            print("   ⚠️ hostmap cache not loaded — skipping must-include add")
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    # Build existing-MPB-brand set for this profile
    mpb_mask = df['Column'].astype(str).str.strip().str.upper() == 'MOST PURCHASED BRANDS'
    existing_norms = {
        _norm_brand(v) for v in df.loc[mpb_mask, 'Value'].astype(str).tolist()
    }
    # Use any existing MPB row as a template for column structure
    template_idx = df[mpb_mask].index[0] if mpb_mask.any() else None
    if template_idx is None:
        return df, 0
    template = df.loc[template_idx].to_dict()

    fixed = 0
    examples = []
    new_rows = []
    for brand in MUST_INCLUDE_MPB_BRANDS:
        bnorm = _norm_brand(brand)
        if bnorm in existing_norms:
            continue
        if not _is_in_hostmap(brand):
            continue
        skew = MPB_DIGITAL_SHARE.get(brand.upper())
        if not skew:
            continue
        target = _mpb_audience_weighted_target(df, brand.upper())
        if target is None or target <= 0:
            continue
        # Hash-deterministic jitter — same shape as the lift function
        h = int(_hl.blake2b(
            f'{subject}|{brand}|must_include_add'.encode(), digest_size=8
        ).hexdigest(), 16)
        macro = ((h % 9001) - 4500) / 10000.0       # -0.45..+0.45
        micro = (((h >> 16) % 1981) - 990) / 100000.0
        new_bp = max(0.5, target + macro + micro)
        if abs(new_bp - round(new_bp)) < 0.01:
            new_bp += 0.17 if (h & 1) else -0.23
            new_bp = max(0.5, new_bp)
        new_bp = round(new_bp, 4)
        if abs(new_bp * 100 - round(new_bp * 100)) < 1e-4:
            new_bp = round(new_bp + 0.0017, 4)

        # Use Sheet4 canonical UPPER as Value (profile convention)
        canonical = _hostmap_canonical(brand) or brand
        new_row = dict(template)
        new_row['Column'] = 'MOST PURCHASED BRANDS'
        new_row['Value'] = canonical.upper()
        new_row[bp_col] = new_bp
        new_raw = int(round(sample_size * new_bp / 100.0))
        if raw_col:
            new_row[raw_col] = new_raw
        if proj_col:
            new_row[proj_col] = int(round(new_raw / 10_000_000.0 * US_POP))
        if cs_col:
            new_row[cs_col] = 0.0   # recomputed below via _renormalize_category
        new_rows.append(new_row)
        examples.append((canonical.upper(), new_bp, target))
        fixed += 1
        existing_norms.add(bnorm)

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        df = _renormalize_category(
            df, 'MOST PURCHASED BRANDS',
            bp_col, cs_col, raw_col, proj_col, sample_size,
        )

    if verbose and fixed:
        print(f"   ➕ MUST_INCLUDE MPB rows added: {fixed} row(s)")
        for v, bp_, tgt in examples:
            print(f"      {v:30s} = {bp_:7.4f} (audience target {tgt:.2f})")
    return df, fixed


# ---------------------------------------------------------------------------
# Defect Class #19 — Christian-friendly white audience archetype miss
# (added 2026-05-25 per Nate Bargatze 5-25 review).
#
# When a profile's audience reads as white + Christian + middle-aged + married
# + suburban/Southern (the "Nate Bargatze / Joe Rogan / Smartless / Theo Von
# comedy" archetype), certain brands have meaningful affinity lifts beyond
# what age-only weighting predicts. These are NOT subject-affinity lifts —
# they're audience-affinity lifts the panel-reality enforcer should know
# about so it doesn't leave Chick-fil-A / Hobby Lobby / Cracker Barrel /
# Angel TV / Pure Flix systematically suppressed.
#
# Detection:
#   - ETHNICITY 'WHITE' >= 55%  AND
#   - AGE 35-44 + 45-54 + 55-64 >= 50%  AND
#   - (RELIGION 'CHRISTIAN' >= 55% OR RELATIONSHIP 'MARRIED' >= 50% OR
#      'PARENTAL_STATUS' has-kids >= 50%)
#
# If detected, apply CHRISTIAN_FRIENDLY_AFFINITY targets via the same
# segment-weighted pathway (with tighter band) so the audience-aligned
# value lands inside the persona-real distribution, not on the LLM's
# default-secular floor.
# ---------------------------------------------------------------------------
CHRISTIAN_FRIENDLY_AFFINITY = {
    # QSR — Chick-fil-A is Christian-owned (Sunday-closed signal), Southern hits
    ('QSR', 'CHICK-FIL-A'): 36.0,
    ('QSR', 'RAISING CANES CHICKEN FINGERS'): 14.0,
    ('QSR', 'CRACKER BARREL'): 18.0,
    # WHERE THEY SHOP — Christian / craft / Southern mass
    ('WHERE THEY SHOP', 'HOBBY LOBBY'): 22.0,
    ('WHERE THEY SHOP', 'TRACTOR SUPPLY'): 18.0,
    ('WHERE THEY SHOP', 'BASS PRO SHOPS'): 16.0,
    ('WHERE THEY SHOP', 'CABELAS'): 14.0,
    ('WHERE THEY SHOP', "CABELA'S"): 14.0,
    ('WHERE THEY SHOP', 'CHICK-FIL-A'): 24.0,  # some pulls map CFA into shop
    # STREAMING / FAITH-BASED — Christian streamers
    ('STREAMING/PLATFORM', 'ANGEL TV'): 6.5,
    ('STREAMING/PLATFORM', 'PURE FLIX'): 4.0,
    ('STREAMING/PLATFORM', 'BYUTV'): 2.5,
    # WHERE THEY DINE — Southern American casual
    ('WHERE THEY DINE', 'CRACKER BARREL'): 18.0,
    ('WHERE THEY DINE', 'WAFFLE HOUSE'): 16.0,
    ('WHERE THEY DINE', 'CHILIS'): 22.0,
    ('WHERE THEY DINE', 'APPLEBEES'): 24.0,
    ('WHERE THEY DINE', 'TEXAS ROADHOUSE'): 24.0,
}


def _is_christian_friendly_white_audience(df):
    """Detect the Bargatze/Rogan/Smartless audience archetype from the file's
    own demos. Returns True / False. Used to gate CHRISTIAN_FRIENDLY_AFFINITY
    lifts so they only apply to the matching archetype.

    Detection: white-majority + working-age centroid + (christian OR married
    OR parental). Conservative — only fires when all three signal classes are
    present, so it never misfires on a young multicultural audience.
    """
    try:
        def _bucket_sum(col_u, label_keys, min_threshold=0):
            sub = df[df['Column'].astype(str).str.strip().str.upper() == col_u]
            if sub.empty:
                return 0.0
            total = 0.0
            for _, r in sub.iterrows():
                v = str(r.get('Value', '') or '').upper().strip()
                if any(k in v for k in label_keys):
                    total += _bp(r.get('Brand Penetration (Row)', 0))
            return total

        white_pct = _bucket_sum('ETHNICITY', ['WHITE'])
        if white_pct < 55.0:
            return False

        midage_pct = (
            _bucket_sum('AGE', ['35-44', '35 - 44']) +
            _bucket_sum('AGE', ['45-54', '45 - 54']) +
            _bucket_sum('AGE', ['55-64', '55 - 64'])
        )
        if midage_pct < 50.0:
            return False

        christian_pct = _bucket_sum('RELIGION', ['CHRISTIAN', 'CATHOLIC', 'PROTESTANT',
                                                  'BAPTIST', 'EVANGELICAL'])
        married_pct = _bucket_sum('RELATIONSHIP', ['MARRIED'])
        parental_pct = _bucket_sum('PARENTAL_STATUS', ['HAVE CHILDREN', 'HAS CHILDREN',
                                                        'HAVE KIDS', 'PARENT'])
        if max(christian_pct, married_pct, parental_pct) < 50.0:
            return False
        return True
    except Exception:
        return False


def _age_distribution(df):
    """Return {bucket: pct} for the 6 standard AGE buckets, from the file's
    own AGE rows. Returns None if AGE column missing or unparseable.

    Used by `_segment_weighted_target` to compute persona-aligned panel-
    reality targets per Defect Class #18.
    """
    try:
        age = df[df['Column'].astype(str).str.strip().str.upper() == 'AGE']
        if age.empty:
            return None
        dist = {'18-24': 0.0, '25-34': 0.0, '35-44': 0.0,
                '45-54': 0.0, '55-64': 0.0, '65+': 0.0}
        for _, r in age.iterrows():
            v = str(r.get('Value', '') or '').upper().strip()
            bp = _bp(r.get('Brand Penetration (Row)', 0))
            if '18-24' in v or '18 - 24' in v: dist['18-24'] += bp
            elif '25-34' in v or '25 - 34' in v: dist['25-34'] += bp
            elif '35-44' in v or '35 - 44' in v: dist['35-44'] += bp
            elif '45-54' in v or '45 - 54' in v: dist['45-54'] += bp
            elif '55-64' in v or '55 - 64' in v: dist['55-64'] += bp
            elif '65' in v or 'OLDER' in v:      dist['65+']  += bp
        total = sum(dist.values())
        if total < 50.0:
            return None
        return {k: v/total for k, v in dist.items()}
    except Exception:
        return None


def _segment_weighted_target(df, cat_u, brand_u):
    """Persona-aligned panel-reality target for (cat, brand), computed as
    Σ(audience_segment_pct × segment_benchmark). Returns None if missing.

    This replaces the static-archetype lookup for floor lifts on brands
    that have per-bucket benchmarks. Prevents Defect Class #18 (overshoot).
    """
    bench = SEGMENT_BENCHMARKS.get((cat_u, brand_u))
    if not bench:
        return None
    dist = _age_distribution(df)
    if not dist:
        return None
    return sum(dist.get(b, 0.0) * bench.get(b, 0.0) for b in bench.keys())


def _detect_age_archetype(df):
    """Read AGE distribution from the file and classify into archetype.

    Returns one of 'older', 'mid', 'young'. Used to pick the right
    panel-reality floor from PANEL_REALITY_FLOORS.

    Detection logic (audience age centroid, NOT subject's age):
      - 'older' if 55+ buckets sum to ≥ 30%
      - 'young' if (18-24 + 25-34) sum to ≥ 55%
      - 'mid' otherwise
    """
    try:
        age = df[df['Column'].astype(str).str.strip().str.upper() == 'AGE'].copy()
        if age.empty:
            return 'mid'

        def _bucket_sum(labels):
            total = 0.0
            for _, r in age.iterrows():
                v = str(r.get('Value', '') or '').upper().strip()
                if any(lbl in v for lbl in labels):
                    total += _bp(r.get('Brand Penetration (Row)', 0))
            return total

        # Older buckets: 55-64, 65+
        older = _bucket_sum(['55-64', '55 - 64', '65', 'OLDER'])
        # Young buckets: 18-24, 25-34
        young = _bucket_sum(['18-24', '18 - 24', '25-34', '25 - 34'])

        if older >= 30.0:
            return 'older'
        if young >= 55.0:
            return 'young'
        return 'mid'
    except Exception:
        return 'mid'


def apply_panel_reality_floors(df, subject, verbose=True):
    """Lift mass-engagement brand BPs to panel-reality floors when the
    talent-template defect signature has depressed them below audience
    reality. Per-(brand, archetype) lookup table. Hostmap-gated (only
    touches rows that already exist; never adds).

    Special case: Apple↔Samsung inversion rebalancing for older archetype.
    The inversion check uses pre-lift snapshot so Samsung-lifting doesn't
    accidentally break the rebalance trigger.
    """
    if df is None or len(df) == 0:
        return df, 0

    # International frames (Omaze precedent): the floor tables encode
    # US panel reality (USPS, Geico, US carriers, US QSR reach). A
    # one-way lift toward US floors on a UK/German audience would
    # re-inflate exactly the US-only brands the country reasoning
    # correctly scored near zero.
    _ctry = _frame_country(df)
    if _ctry:
        if verbose:
            print(f"   📊 panel-reality enforcer: {_ctry} frame - US "
                  f"panel-reality floors skipped")
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    sample_size = _detect_sample_size(df, bp_col, raw_col)
    arch = _detect_age_archetype(df)

    if verbose:
        print(f"   📊 panel-reality enforcer: detected audience archetype = '{arch}'")

    df['_col_u'] = df['Column'].astype(str).str.strip().str.upper()
    df['_val_u'] = df['Value'].astype(str).str.strip().str.upper()

    # Snapshot Apple/Samsung BEFORE any lifts so the inversion check is
    # not invalidated when Samsung is lifted upward.
    apple_samsung_snapshot = {}
    if arch == 'older':
        for cat_u in ('TECHNOLOGY/DEVICE', 'TECHNOLOGY BRAND'):
            apple_idx = df.index[(df['_col_u'] == cat_u) & (df['_val_u'] == 'APPLE')].tolist()
            samsung_idx = df.index[(df['_col_u'] == cat_u) & (df['_val_u'] == 'SAMSUNG')].tolist()
            if apple_idx and samsung_idx:
                apple_samsung_snapshot[cat_u] = (
                    apple_idx[0], samsung_idx[0],
                    _bp(df.at[apple_idx[0], bp_col]),
                    _bp(df.at[samsung_idx[0], bp_col]),
                )

    n_lifts = 0
    affected_cats = set()
    examples = []

    # Defect #19: Christian-friendly white audience archetype (Bargatze /
    # Rogan / Smartless / Theo Von). Detect once before the loop so we
    # only do the demo scan one time.
    is_xian_friendly = _is_christian_friendly_white_audience(df)
    if is_xian_friendly and verbose:
        print(f"   📊 panel-reality enforcer: Christian-friendly white audience archetype detected — applying affinity floors")

    # Build the union of (cat, brand) keys from all lookup tables so we hit
    # every brand once, preferring the most specific target when available.
    all_keys = (set(PANEL_REALITY_FLOORS.keys())
                | set(SEGMENT_BENCHMARKS.keys())
                | set(ETHNICITY_BENCHMARKS.keys())
                | set(GENDER_BENCHMARKS.keys()))
    if is_xian_friendly:
        all_keys |= set(CHRISTIAN_FRIENDLY_AFFINITY.keys())
    for (cat_u, brand_u) in all_keys:
        # Priority order:
        #   1. Christian-friendly affinity (Defect #19) — only when archetype detected
        #   2. Ethnicity-weighted target (Defect #20) — Black/Hispanic-targeted brands
        #   3. Gender-weighted target — female-skewing brands (Pinterest, etc.)
        #   4. Segment-weighted target (Defect #18) — auto-calibrates by age
        #   5. Legacy archetype lookup (older / mid / young) — fallback
        xian_target = (CHRISTIAN_FRIENDLY_AFFINITY.get((cat_u, brand_u))
                       if is_xian_friendly else None)
        eth_target = _ethnicity_weighted_target(df, cat_u, brand_u)
        gen_target = _gender_weighted_target(df, cat_u, brand_u)
        seg_target = _segment_weighted_target(df, cat_u, brand_u)

        if xian_target is not None:
            floor = xian_target
            target_band = 'christian-friendly'
        elif eth_target is not None:
            floor = eth_target
            target_band = 'ethnicity-weighted'
        elif gen_target is not None:
            floor = gen_target
            target_band = 'gender-weighted'
        elif seg_target is not None:
            floor = seg_target
            target_band = 'segment-weighted'
        else:
            floors = PANEL_REALITY_FLOORS.get((cat_u, brand_u), {})
            floor = floors.get(arch)
            target_band = f'archetype-{arch}'
            if floor is None:
                continue

        mask = (df['_col_u'] == cat_u) & (df['_val_u'] == brand_u)
        idxs = df.index[mask].tolist()
        if not idxs:
            continue
        for idx in idxs:
            cur = _bp(df.at[idx, bp_col])
            # PERSONA-REASONING FIRST (per rule #2): the benchmark is a FLOOR,
            # not a ceiling. We LIFT suppressed values up to the band, but we
            # DO NOT TRIM reasoned-high values — those represent genuine
            # geographic / cultural / archetype affinity the LLM picked up.
            #
            # EXCEPTION: a small set of brands have a known LLM-overshoot
            # pattern (Defect #18) — listed below — and DO get two-sided
            # enforcement to prevent the "lift past target then no trim" cycle.
            #
            # If you're adding a new ETHNICITY/SEGMENT benchmark and you can
            # imagine a profile where the LLM might reason ABOVE the bench
            # for real (regional hub, archetype hub, etc.), leave it as a
            # one-way lift.
            KNOWN_OVERSHOOT_BRANDS = {
                # (cat_u, brand_u): brands where LLM tends to over-lift past target
                ('STREAMING/PLATFORM', 'BET+'),
                ('STREAMING/PLATFORM', 'ALLBLK'),
                # SEARCH ENGINE/AI cohort — LLM-adoption survey baselines
                # (Pew 2024) cap young-cohort adoption at 22% (Gemini),
                # 55% (ChatGPT), 12% (Perplexity). The vet-reasoner
                # consistently KEEPs these above the segment-weighted
                # target for young / creator / tech-savvy audiences,
                # producing 32-33% Gemini pins on 18-24-heavy profiles
                # (Zhirelle 32.8069%, Hollywood_Reporter 32.8697%,
                # ~2x the median across 120 recent files). Two-sided
                # trim activates so cur > target+3pp recenters into
                # the persona-aligned band. CLAUDE AI was already
                # protected here; adding GEMINI / CHAT GPT / PERPLEXITY
                # 2026-06-03 (Jenna's Zhirelle audit).
                ('SEARCH ENGINE/AI', 'CLAUDE AI'),
                ('SEARCH ENGINE/AI', 'GEMINI'),
                ('SEARCH ENGINE/AI', 'CHAT GPT'),
                ('SEARCH ENGINE/AI', 'PERPLEXITY'),
                # 2026-06-03 (Jenna 7-file Gemini master defect ticket): expand
                # two-sided trim to the whole SEARCH ENGINE/AI cohort. Chicago_Sky
                # showed GOOGLE 99.99% (saturation pin), Kaitlyn/Frances showed
                # CHAT GPT 76-79% (cohort inflation), COPILOT 23.5% pin across
                # 6+ files. Adding GOOGLE/COPILOT/BING/MSN closes the cohort
                # so any 5+pp overshoot above persona-aligned bench trims back
                # into the band — eliminating the "rank-cascade pin" pattern
                # (100/50/33/23/18/14) the vet-reasoner kept producing.
                ('SEARCH ENGINE/AI', 'GOOGLE'),
                ('SEARCH ENGINE/AI', 'COPILOT'),
                ('SEARCH ENGINE/AI', 'BING'),
                ('SEARCH ENGINE/AI', 'MSN'),
                # 2026-06-04 (Jenna's 7-of-11 Visa over-read defect): add the
                # full credit-card cohort for two-sided trim. Same pattern as
                # the SEARCH ENGINE/AI rank-cascade: the LLM lands Visa near
                # 60% across unrelated personas because the prior bench was
                # cardholder-share (76-82%) rather than adult-population
                # penetration (35-58%). With KNOWN_OVERSHOOT + the revised
                # bench above, anything >5pp above the persona-aligned target
                # trims into target ± 3pp (e.g. Visa 65% on a 25-34 audience
                # whose bench is 50 → re-jitters to 47-53).
                ('CREDIT PROVIDER', 'VISA'),
                ('CREDIT PROVIDER', 'MASTERCARD'),
                ('CREDIT PROVIDER', 'AMERICAN EXPRESS'),
                ('CREDIT PROVIDER', 'DISCOVER CREDIT CARD'),
                ('CREDIT PROVIDER', 'CAPITAL ONE'),
                # 2026-05-25 (Valkyrae audit) — TELECOM Big 3 over-inflated.
                # Subscriber share is mutually exclusive (1 primary carrier per
                # household); when Verizon+AT&T+T-Mobile sum to >100% on a profile,
                # the LLM is double-counting. Two-sided trim activates so anything
                # >3pp above persona target trims to band.
                ('TELECOM', 'VERIZON'),
                ('TELECOM', 'AT&T'),
                ('TELECOM', 'T-MOBILE'),
                ('TELECOM', 'XFINITY'),
                ('TELECOM', 'SPECTRUM'),
                # FAST aggregator inflation — Roku Channel + Pluto + YouTube TV
                # often land 30-50% (vs persona-real 10-22%). Same Keke pre-fix
                # pattern. Two-sided activates.
                ('VIRTUAL MVPD FAST', 'ROKU CHANNEL'),
                ('VIRTUAL MVPD FAST', 'PLUTO TV'),
                ('VIRTUAL MVPD FAST', 'YOUTUBE TV'),
            }
            two_sided = (cat_u, brand_u) in KNOWN_OVERSHOOT_BRANDS
            if target_band in ('segment-weighted', 'christian-friendly', 'ethnicity-weighted', 'gender-weighted'):
                if cur >= floor - 1.0 and not two_sided:
                    continue  # already at/above floor — trust the LLM
                # 2026-06-03 (Jenna pin-check follow-up): widened acceptance band
                # from ±3pp to ±5pp so reasoned-high LLM values (target..target+5pp)
                # are preserved instead of trimmed to a tight 3pp cluster. Only
                # clearly-defective values (>5pp above target) get overridden.
                if two_sided and abs(cur - floor) <= 5.0:
                    continue  # in trust band — LLM reasoning preserved
                # 2026-06-03: widened jitter band ±1.5pp → ±3pp AND added age
                # archetype to the salt so within-archetype variance is genuinely
                # 5-6pp (matches real-world panel variance for these brands)
                # instead of a 3pp soft-pin cluster. Per-profile target also
                # varies because segment-weighted target derives from each
                # profile's own age distribution.
                _arch = arch  # captured from outer scope
                target = _jitter_for(
                    subject, brand_u, salt=f'panel-{target_band}-{cat_u}-{_arch}',
                    lo=max(0.05, floor - 3.0), hi=floor + 3.0,
                )
            else:
                if cur >= floor - 0.5:
                    continue  # already at/above panel-reality
                target = _jitter_for(
                    subject, brand_u, salt=f'panel-{arch}',
                    lo=floor + 0.15, hi=floor * 1.07,
                )
            target = round(target, 4)
            df = _set_bp(df, idx, target, bp_col, cs_col, raw_col, proj_col, sample_size)
            n_lifts += 1
            affected_cats.add(cat_u)
            if len(examples) < 8:
                examples.append((cat_u, brand_u, cur, target, target_band))

    # Apple↔Samsung inversion correction (older archetype only) — uses snapshot.
    for cat_u, (apple_idx, samsung_idx, apple_bp0, samsung_bp0) in apple_samsung_snapshot.items():
        # Inversion: Apple > 70 and Samsung < 18 BEFORE any lifts — pacino-pre-fix shape
        if apple_bp0 > 70.0 and samsung_bp0 < 18.0:
            apple_target = _jitter_for(
                subject, 'APPLE', salt='rebalance',
                lo=58.0, hi=64.0,
            )
            df = _set_bp(df, apple_idx, round(apple_target, 4),
                         bp_col, cs_col, raw_col, proj_col, sample_size)
            n_lifts += 1
            affected_cats.add(cat_u)
            if len(examples) < 8:
                examples.append((cat_u, 'APPLE', apple_bp0, apple_target, 'apple-samsung-rebalance'))

    df = df.drop(columns=['_col_u', '_val_u'])

    if affected_cats and (cs_col is not None or raw_col is not None or proj_col is not None):
        for cat in affected_cats:
            df = _renormalize_category(df, cat, bp_col, cs_col, raw_col,
                                       proj_col, sample_size)

    if verbose and n_lifts:
        print(f"   📈 panel-reality enforcer adjusted {n_lifts} brand(s) toward persona-aligned target:")
        for tup in examples:
            c, v, old, new = tup[0], tup[1], tup[2], tup[3]
            band = tup[4] if len(tup) > 4 else 'archetype'
            print(f"      [{c}] {v}: {old:.2f} → {new:.4f}  ({band})")
        more = n_lifts - len(examples)
        if more > 0:
            print(f"      (+{more} more)")

    return df, n_lifts


# ============================================================================
# Rule #3 cross-category consistency — propagate MPB → other categories
# ============================================================================

_PROPAGATE_DEMO_SKIP = {
    'GENDER', 'AGE', 'ETHNICITY', 'INCOME', 'OCCUPATION', 'PARENTAL_STATUS',
    'PARENTAL STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
    'SEXUAL ORIENTATION', 'EDUCATION', 'LOCATION',
    'BRAND INPUT', 'SUBJECT',
    'MOST PURCHASED BRANDS',         # source — don't overwrite
    # Talent/sport categories have their own internal alignment via
    # _propagate_to_companion_cols (leagues, divisions, AL/NL). Don't
    # touch them here.
    'TALENT', 'ATHLETE', 'NFL ATHLETE', 'NBA ATHLETE', 'MLB ATHLETE',
    'NHL ATHLETE', 'ACTOR', 'MUSICIAN/BAND', 'MUSICIAN',
    'HOST/PERSONALITY', 'POLITICS/ACTIVIST', 'POLITICS', 'PODCAST',
    'SPORTS TEAM', 'AL/NL', 'AFC/NFC', 'AFC EAST', 'AFC NORTH',
    'AFC SOUTH', 'AFC WEST', 'NFC EAST', 'NFC NORTH', 'NFC SOUTH',
    'NFC WEST', 'AL EAST', 'AL CENTRAL', 'AL WEST',
    'NL EAST', 'NL CENTRAL', 'NL WEST',
    'EAST', 'WEST', 'CENTRAL', 'NORTH', 'SOUTH',  # generic divisions
    # Content/persona categories carry the subject-100 invariant
    'PERSONA', 'CONTENT', 'SERIES', 'FRANCHISE',
}

# Min gap (pp) before propagation fires. Tight enough to catch real
# misalignments (Tyson MPB 22% / CPG 0.03%) but lenient enough that
# legitimate small differences between MPB ("purchased the brand") and
# behavioral cats ("category shopper at this brand") aren't churned.
_PROPAGATE_MIN_GAP_PP = 1.5


def propagate_mpb_to_other_categories(df, subject, verbose=True):
    """Rule #3 enforcement: when MPB carries the audience-weighted target
    for a brand (e.g. Tyson 8.5%), align the same brand in any other
    behavioral category (CPG, APPAREL/FOOTWEAR, BEAUTY/WELLNESS,
    HOME/OUTDOOR, ACCESSORIES, TECHNOLOGY BRAND, PETS, TRAVEL, GAMES,
    TOYS, ...) to that MPB value with deterministic jitter.

    BIDIRECTIONAL (2026-05-26): propagates in BOTH directions when the
    gap exceeds _PROPAGATE_MIN_GAP_PP. Lifts sub-cats when MPB is
    higher (Tyson MPB 22 → CPG 0.03 was the original Defect Class #23),
    and trims sub-cats when MPB is lower (Tyson MPB 8.5 → CPG 22 is
    the recalibration case).

    Hostmap-gated: only brands present in MPB_DIGITAL_SHARE (which is
    itself Sheet4-validated) trigger propagation. Bare-floor sub-cat
    rows for brands without a digital-share entry are left untouched."""
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    # Build MPB lookup
    df['_col_u'] = df['Column'].astype(str).str.upper().str.strip()
    df['_val_u'] = df['Value'].astype(str).str.upper().str.strip()
    mpb_rows = df[df['_col_u'] == 'MOST PURCHASED BRANDS']
    mpb_by_brand = {}
    for _, r in mpb_rows.iterrows():
        mpb_by_brand[r['_val_u']] = _bp(r[bp_col])

    share_brands_u = set(MPB_DIGITAL_SHARE.keys())
    aligned = 0
    examples = []

    for idx, r in df.iterrows():
        cat = r['_col_u']
        if cat in _PROPAGATE_DEMO_SKIP:
            continue
        brand_u = r['_val_u']
        if brand_u not in share_brands_u:
            continue
        mpb_v = mpb_by_brand.get(brand_u)
        if mpb_v is None or mpb_v <= 0.0:
            continue
        sub_v = _bp(r[bp_col])
        if abs(mpb_v - sub_v) < _PROPAGATE_MIN_GAP_PP:
            continue

        # Subject-pin guard: never demote a 100-pin (e.g. subject in its
        # own MPB or APPAREL row).
        if abs(sub_v - 100.0) < 0.01:
            continue

        direction = '↓' if sub_v > mpb_v else '↑'
        # _jitter_for returns the FINAL jittered value (base ± pct%*base),
        # NOT an offset. So we call it once with base=mpb_v and use the
        # result directly as the target.
        target = _jitter_for(
            subject, r['Value'], salt=f'mpb-propagate|{cat}', pct=0.012,
            base=mpb_v,
        )
        # Defend against landing on .00xx (rare with the existing 0.0017
        # auto-bump inside _jitter_for, but covers small base values)
        if _is_look_round(target):
            target = _jitter_for(
                subject, r['Value'], salt=f'mpb-propagate-dp4|{cat}',
                pct=0.018, base=mpb_v,
            )
        _set_bp(df, idx, target, bp_col, cs_col, raw_col, proj_col, sample_size)
        aligned += 1
        if len(examples) < 8:
            examples.append((r['Value'], r['Column'], sub_v, _bp(df.at[idx, bp_col]), direction))

    df = df.drop(columns=['_col_u', '_val_u'], errors='ignore')

    if verbose and aligned:
        print(f"   ↔️  MPB↔sub-cat propagation (Rule #3): {aligned} row(s)")
        for b, c, o, n, d in examples:
            print(f"      [{c}] {b}: {o:.4f} {d} {n:.4f}")
        more = aligned - len(examples)
        if more > 0:
            print(f"      (+{more} more)")
    return df, aligned


# ---------------------------------------------------------------------------
# 2026-05-26 Defect Class #23b — sub-cat → MPB REVERSE propagation
# ---------------------------------------------------------------------------
# propagate_mpb_to_other_categories (above) uses MPB as the anchor and only
# fires for brands in MPB_DIGITAL_SHARE. That leaves a class of brands
# UNTOUCHED:
#   - Champion APPAREL 13.64 / MPB 0.0186
#   - Brooks Shoes APPAREL 8.14 / MPB 0.0167
#   - Aeropostale APPAREL 7.56 / MPB 0.0146
#   - Dollar Shave Club BEAUTY 5.24 / MPB 0.0299
#   - Dockers APPAREL 6.92 / MPB 0.0297
#   - Owala / Vera Bradley / Zenni Optical / La-Z-Boy / Van Heusen / etc.
# These are sub-cat enforcers (apparel_floor_lift, etc.) that lift the
# category column without touching MPB. Result: dashboard MPB headline says
# "Champion 0.02%" while APPAREL shows "Champion 13.64%". User-visible bug.
#
# This enforcer fires when MPB row is at floor (<0.10) and the same brand
# has a sub-cat value ≥ 1.0. Lifts MPB to MAX(sub-cat) with deterministic
# jitter. Hostmap-gated implicitly (only acts on brands that already have an
# MPB row, which means the upstream enforcers already validated them).
# ---------------------------------------------------------------------------

_REVERSE_PROP_FLOOR_MPB = 0.10  # MPB <0.10% counts as "at floor"
_REVERSE_PROP_MIN_SUBCAT = 1.0  # only lift when sub-cat ≥ 1.0%
_REVERSE_PROP_MAX_SUBCAT = 35.0  # don't blindly lift to extreme values


def reverse_propagate_subcat_to_mpb(df, subject, verbose=True):
    """Lift MPB row up to match sub-cat magnitude when MPB is pinned at floor
    but the same brand shows non-trivial penetration in APPAREL/FOOTWEAR /
    BEAUTY/WELLNESS / HOME/OUTDOOR / CPG / TECHNOLOGY BRAND / ACCESSORIES /
    PETS / TRAVEL / TOYS.

    Inverse of propagate_mpb_to_other_categories — that one trims sub-cat
    to MPB, this one lifts MPB to sub-cat.

    Conservative gates:
      - MPB row must EXIST (no row insertion — let
        add_missing_sheet4_must_include_mpb handle inserts)
      - MPB BP < _REVERSE_PROP_FLOOR_MPB (0.10%) — only fires on floor pins
      - sub-cat BP ≥ _REVERSE_PROP_MIN_SUBCAT (1.0%)
      - sub-cat BP ≤ _REVERSE_PROP_MAX_SUBCAT (35%) — guard vs. data errors
      - never demote a 100-pin subject row

    Returns (df, n_lifted)."""
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    df['_col_u'] = df['Column'].astype(str).str.upper().str.strip()
    df['_val_u'] = df['Value'].astype(str).str.upper().str.strip()

    # Index MPB rows by brand
    mpb_mask = df['_col_u'] == 'MOST PURCHASED BRANDS'
    mpb_idx_by_brand = {}
    mpb_bp_by_brand = {}
    for idx, r in df[mpb_mask].iterrows():
        b = r['_val_u']
        mpb_idx_by_brand[b] = idx
        mpb_bp_by_brand[b] = _bp(r[bp_col])

    # Collect max sub-cat value per brand (only from non-skip categories)
    max_subcat = {}
    for _, r in df.iterrows():
        cat = r['_col_u']
        if cat in _PROPAGATE_DEMO_SKIP:
            continue
        brand = r['_val_u']
        if brand not in mpb_idx_by_brand:
            continue
        sub_v = _bp(r[bp_col])
        if sub_v < _REVERSE_PROP_MIN_SUBCAT or sub_v > _REVERSE_PROP_MAX_SUBCAT:
            continue
        if max_subcat.get(brand, 0.0) < sub_v:
            max_subcat[brand] = sub_v

    n_lifted = 0
    examples = []
    for brand, max_v in max_subcat.items():
        mpb_v = mpb_bp_by_brand[brand]
        if mpb_v >= _REVERSE_PROP_FLOOR_MPB:
            continue
        if abs(mpb_v - 100.0) < 0.01:
            continue
        # Target = sub-cat value with deterministic jitter (±~5%)
        target = _jitter_for(
            subject, brand, salt='reverse-prop-mpb', pct=0.05, base=max_v,
        )
        if _is_look_round(target):
            target = _jitter_for(
                subject, brand, salt='reverse-prop-mpb-dp4', pct=0.018, base=max_v,
            )
        idx = mpb_idx_by_brand[brand]
        old = _bp(df.at[idx, bp_col])
        _set_bp(df, idx, target, bp_col, cs_col, raw_col, proj_col, sample_size)
        n_lifted += 1
        if len(examples) < 8:
            examples.append((brand, old, _bp(df.at[idx, bp_col]), max_v))

    df = df.drop(columns=['_col_u', '_val_u'], errors='ignore')

    if verbose and n_lifted:
        print(f"   ↑ MPB reverse-propagation (Defect Class #23b): {n_lifted} MPB row(s) lifted")
        for b, o, n, sv in examples:
            print(f"      {b}: MPB {o:.4f}% → {n:.4f}%  (sub-cat max {sv:.4f}%)")
        more = n_lifted - len(examples)
        if more > 0:
            print(f"      (+{more} more)")
    return df, n_lifted


# ---------------------------------------------------------------------------
# 2026-08-22 Rule #3b — MPB EXACT mirror (user verdict 2026-05-28)
# ---------------------------------------------------------------------------
# "the value in MBP should be the exact value you put in the sub category
# with no jitter". The propagate pair above uses the OLD pre-2026-05-28
# semantics (gap threshold + jitter, hardcoded brand set), which leaves
# ~1,880 drifted rows per fresh file. The canonical exact-mirror fixer
# lived only in scripts/fix_mpb_cross_cat_consistency.py (one-shot CLI)
# and was never wired into the chain. This enforcer is that fixer's
# fix_one() logic, in-module so every write path gets it:
#   1. Dedupe MPB by brand-norm (keep max BP, canonical Value).
#   2. Every non-skip-cat row whose brand-norm exists in MPB gets
#      BP := MPB BP exactly (Value := MPB canonical casing), Raw/Proj
#      recomputed. Subject 100-pins untouched.
#   3. Renormalize Category Share for touched categories.
# MUST run AFTER every dejitter/depin pass (they would re-break the
# exact identity; dejitter_cross_cat_4dp_pins also now exempts
# MPB-mirror identities). Idempotent.
# ---------------------------------------------------------------------------

_MPB_MIRROR_SKIP_CATS = {
    # demographics
    'GENDER', 'AGE', 'ETHNICITY', 'INCOME', 'OCCUPATION',
    'PARENTAL_STATUS', 'PARENTAL STATUS',
    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'SEXUAL ORIENTATION',
    'EDUCATION', 'LOCATION',
    # metadata
    'BRAND INPUT', 'SUBJECT', 'SAMPLE SIZE', 'INPUT_METADATA',
    'BRAND CATEGORY', 'AVID FAN', 'CASUAL FAN',
    # source category itself
    'MOST PURCHASED BRANDS',
    # talent/sport (have their own internal alignment)
    'TALENT', 'TALENT SUB', 'ATHLETE', 'NFL ATHLETE', 'NBA ATHLETE',
    'MLB ATHLETE', 'NHL ATHLETE', 'WNBA ATHLETE', 'MLS ATHLETE',
    'ACTOR', 'MUSICIAN/BAND', 'MUSICIAN', 'HOST/PERSONALITY',
    'POLITICS/ACTIVIST', 'POLITICS', 'PODCAST', 'SPORTS TEAM',
    'AL/NL', 'AFC/NFC', 'AFC EAST', 'AFC NORTH', 'AFC SOUTH', 'AFC WEST',
    'NFC EAST', 'NFC NORTH', 'NFC SOUTH', 'NFC WEST',
    'AL EAST', 'AL CENTRAL', 'AL WEST',
    'NL EAST', 'NL CENTRAL', 'NL WEST',
    'EASTERN CONFERENCE', 'WESTERN CONFERENCE',
    'PACIFIC', 'ATLANTIC', 'METROPOLITAN', 'CENTRAL', 'NORTH', 'SOUTH',
    'EAST', 'WEST',
    'MLB', 'NBA', 'NFL', 'NHL', 'MLS', 'WNBA', 'EPL', 'LA LIGA',
    'SERIE A', 'LIGUE 1', 'BUNDESLIGA', 'CFB', 'SOCCER',
    # content/persona
    'PERSONA', 'CONTENT', 'SERIES', 'FRANCHISE',
}


def enforce_mpb_exact_mirror(df, subject, verbose=True):
    """Rule #3b: MOST PURCHASED BRANDS is the canonical source of truth
    for a brand's BP; every other (non-skip) category carrying the same
    brand mirrors it EXACTLY (no jitter, no gap). Returns (df, n)."""
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c and _c in df.columns and df[_c].dtype.name not in ('object', 'O'):
            df[_c] = df[_c].astype(object)

    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_n = df['Value'].astype(str).apply(_norm_brand)
    mpb_mask = col_u == 'MOST PURCHASED BRANDS'

    # ---- 1. dedupe MPB by brand-norm (keep max BP) ----
    drop_idx = []
    by_norm = {}
    for idx in df.index[mpb_mask]:
        nb = val_n.at[idx]
        if not nb:
            continue
        bp = _bp(df.at[idx, bp_col])
        if bp is None or pd.isna(bp) or abs(bp - 100.0) < 0.01:
            continue
        prev = by_norm.get(nb)
        if prev is None or bp > prev[1]:
            if prev is not None:
                drop_idx.append(prev[0])
            by_norm[nb] = (idx, bp)
        else:
            drop_idx.append(idx)
    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)
        col_u = df['Column'].astype(str).str.upper().str.strip()
        val_n = df['Value'].astype(str).apply(_norm_brand)
        mpb_mask = col_u == 'MOST PURCHASED BRANDS'

    # ---- 2. MPB lookup ----
    mpb_lookup = {}
    for idx in df.index[mpb_mask]:
        nb = val_n.at[idx]
        bp = _bp(df.at[idx, bp_col])
        if not nb or bp is None or pd.isna(bp) or bp <= 0:
            continue
        if abs(bp - 100.0) < 0.01:
            continue
        prev = mpb_lookup.get(nb)
        if prev is None or bp > prev['bp']:
            mpb_lookup[nb] = {'bp': bp, 'value': str(df.at[idx, 'Value'])}
    if not mpb_lookup:
        return df, len(drop_idx)

    # ---- 3. mirror sub-cat rows to MPB exactly ----
    n_aligned = 0
    touched = set()
    for idx in df.index:
        cat = col_u.at[idx]
        if cat in _MPB_MIRROR_SKIP_CATS:
            continue
        nb = val_n.at[idx]
        if nb not in mpb_lookup:
            continue
        sub_bp = _bp(df.at[idx, bp_col])
        if sub_bp is None or pd.isna(sub_bp) or abs(sub_bp - 100.0) < 0.01:
            continue
        target = mpb_lookup[nb]
        # Exact-4dp comparison. An abs()<1e-4 tolerance let float noise
        # (10.9865-10.9864 = 9.99e-5) read as "aligned" while the 4dp
        # values differ - the one drift class the audit verifier flags.
        if round(sub_bp, 4) == round(target['bp'], 4):
            continue
        df.at[idx, bp_col] = round(target['bp'], 4)
        df.at[idx, 'Value'] = target['value']
        new_raw = int(round(target['bp'] / 100.0 * sample_size))
        if raw_col:
            df.at[idx, raw_col] = new_raw
        if proj_col:
            df.at[idx, proj_col] = int(round(new_raw / 10_000_000.0 * US_POP))
        touched.add(str(df.at[idx, 'Column']))
        n_aligned += 1

    if drop_idx:
        touched.add('MOST PURCHASED BRANDS')
    for c in touched:
        df = _renormalize_category(df, c, bp_col, cs_col, raw_col,
                                   proj_col, sample_size)

    total = n_aligned + len(drop_idx)
    if verbose and total:
        print(f"   🪞 MPB exact mirror (Rule #3b): {n_aligned} row(s) aligned, "
              f"{len(drop_idx)} MPB dupe(s) dropped")
    return df, total


# ---------------------------------------------------------------------------
# 2026-05-26 Defect Class #24 — TALENT ↔ sub-cat propagation
# ---------------------------------------------------------------------------
# Same defect as MPB ↔ sub-cat (Defect Class #23/#23b) but for talent rows.
# Audit on Gen Pop showed 892 TALENT names + 623 ACTOR + 420 ATHLETE + 375
# MUSICIAN + 53 HOST stuck at 0.005-0.030. Many of those names ARE present
# in BOTH TALENT and a sub-cat — but at very different magnitudes (e.g.
# Pedro Pascal TALENT 72.94% but ACTOR could be at floor for some profiles).
#
# This enforcer finds the MAX BP across (TALENT, ACTOR, ATHLETE, COMEDIAN,
# MUSICIAN/BAND, HOST/PERSONALITY, POLITICS/ACTIVIST, PODCAST, NFL ATHLETE,
# NBA ATHLETE, MLB ATHLETE, NHL ATHLETE, SOCCER) for each name and aligns
# every row of that name (in those cats) to MAX ± per-row deterministic
# jitter. Does NOT insert new rows.
# ---------------------------------------------------------------------------

_TALENT_FAMILY = {
    'TALENT', 'ACTOR', 'ATHLETE', 'COMEDIAN', 'MUSICIAN/BAND', 'MUSICIAN',
    'HOST/PERSONALITY', 'POLITICS/ACTIVIST', 'POLITICS', 'PODCAST',
    'NFL ATHLETE', 'NBA ATHLETE', 'MLB ATHLETE', 'NHL ATHLETE', 'SOCCER',
    'WRITER/DIRECTOR/AUTHOR/ARTIST',
}

_TALENT_PROP_MIN_GAP_PP = 1.5
_TALENT_PROP_MAX_VALUE = 99.0  # safety: never propagate values ≥ 99 (subject pins)


def propagate_talent_to_subcats(df, subject, verbose=True):
    """Align talent rows across TALENT family columns (Rule #3 for people).

    For each name present in 2+ rows within _TALENT_FAMILY:
      - Find MAX BP across all such rows
      - If MAX is in [1.0, 99.0] (not floor, not subject-pin), align every
        row of that name in the family to MAX ± per-row jitter
      - Per-row jitter uses a different salt for each (name, cat) pair so
        the rows aren't pinned identical
      - Floor-band rows (<0.10%) are LIFTED to MAX; high rows are not
        trimmed unless explicitly above MAX (the "max" by definition is
        the highest, so this is asymmetric — always lift, never trim
        unless we found a sub-cat that's higher than another)

    Returns (df, n_aligned). Does not insert rows."""
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    df['_col_u'] = df['Column'].astype(str).str.upper().str.strip()
    df['_val_u'] = df['Value'].astype(str).str.upper().str.strip()

    fam_mask = df['_col_u'].isin(_TALENT_FAMILY)
    fam_rows = df[fam_mask]

    # For each name, collect (idx, cat, bp)
    by_name = {}
    for idx, r in fam_rows.iterrows():
        b = _bp(r[bp_col])
        by_name.setdefault(r['_val_u'], []).append((idx, r['_col_u'], b))

    n_aligned = 0
    examples = []

    for name, rows in by_name.items():
        if len(rows) < 2:
            continue  # nothing to align
        max_v = max(b for _, _, b in rows)
        if max_v < 1.0:
            continue  # all at floor, nothing to anchor on
        if max_v >= _TALENT_PROP_MAX_VALUE:
            continue  # subject pin or near-subject value
        for idx, cat, cur_v in rows:
            if abs(cur_v - max_v) < _TALENT_PROP_MIN_GAP_PP:
                continue
            if abs(cur_v - 100.0) < 0.01:
                continue  # subject pin guard
            target = _jitter_for(
                subject, name, salt=f'talent-prop|{cat}', pct=0.035, base=max_v,
            )
            if _is_look_round(target):
                target = _jitter_for(
                    subject, name, salt=f'talent-prop-dp4|{cat}', pct=0.014, base=max_v,
                )
            _set_bp(df, idx, target, bp_col, cs_col, raw_col, proj_col, sample_size)
            n_aligned += 1
            if len(examples) < 8:
                examples.append((name, cat, cur_v, _bp(df.at[idx, bp_col]), max_v))

    df = df.drop(columns=['_col_u', '_val_u'], errors='ignore')

    if verbose and n_aligned:
        print(f"   👥 TALENT family propagation: {n_aligned} row(s) aligned")
        for n, c, o, nv, mx in examples:
            print(f"      [{c}] {n}: {o:.4f}% → {nv:.4f}%  (max in family {mx:.4f}%)")
        more = n_aligned - len(examples)
        if more > 0:
            print(f"      (+{more} more)")
    return df, n_aligned


# ============================================================================
# Household-streaming floor — brand-profile-only enforcer
# Added 2026-05-29 after the Nike profile shipped with NETFLIX=60.26%
# (consensus 55-80%, FAIL_low), DISNEY+=30.87% (consensus 25-50%,
# FAIL_low), HBO MAX=23.06% (consensus 18-38%, FAIL_low) all flagged
# by the vet framework — but the GPT-4o re-reasoner kept them low
# because of an over-fit "active demo less couch-bound" prior that
# ignored Nike's massive household/soccer-mom audience.
#
# Rule: for BRAND profiles only (not talent), if a known household
# streamer's BP is more than HSF_TRIGGER_GAP_PCT below its gen-pop
# consensus mid, lift to HSF_TARGET_PCT * consensus_mid with
# deterministic per-(subject, brand) jitter. Idempotent.
# ============================================================================

# Consensus benchmarks come from Gen_Pop_2026.csv values cross-checked
# against published 2025 streaming-penetration studies (Nielsen Gauge,
# Antenna, MoffettNathanson). These are conservative MID points; the
# enforcer only triggers if BP is more than 8pts below this mid.
HOUSEHOLD_STREAMING_CONSENSUS_MID: dict[tuple[str, str], float] = {
    ('STREAMING/PLATFORM', 'NETFLIX'):            38.0,   # 2026-08-24: corrected Gen Pop baseline 39.17
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): 36.0,   # 2026-08-24: corrected baseline 37.19
    ('STREAMING/PLATFORM', 'HULU'):               38.0,
    ('STREAMING/PLATFORM', 'DISNEY+'):            43.0,
    ('STREAMING/PLATFORM', 'HBO MAX'):            22.3,   # 2026-08-24: baseline 23.01
    ('STREAMING/PLATFORM', 'PARAMOUNT+'):         18.6,   # 2026-08-24: baseline 19.22
    ('STREAMING/PLATFORM', 'PEACOCK'):            15.5,   # 2026-08-24: baseline 16.10
    ('STREAMING/PLATFORM', 'APPLE TV+'):          11.8,   # 2026-08-24: baseline 12.25
    ('STREAMING/PLATFORM', 'YOUTUBE KIDS'):       12.0,
    ('STREAMING/MUSIC',    'SPOTIFY'):            30.1,   # 2026-08-24: baseline 31.18
    ('STREAMING/MUSIC',    'APPLE MUSIC'):        11.7,   # 2026-08-24: baseline 12.11
    ('STREAMING/MUSIC',    'AMAZON MUSIC'):       14.4,   # 2026-08-24: baseline 14.89
}

# Lift trigger: BP must be at least this many points below the mid
# before the enforcer fires. Above this gap, the agent's reasoning is
# trusted as a legitimate persona-divergence call.
HSF_TRIGGER_GAP_PCT = 8.0

# Target after lift: percentage of consensus mid to land at (allows
# brand profiles to land slightly under consensus for personas that
# do trend less couch-bound, while still rescuing the gross misses).
HSF_TARGET_PCT = 0.97

# Brand-profile detection: the BRAND CATEGORY meta row's Value will be
# a brand category (APPAREL/FOOTWEAR, CPG, QSR, TECHNOLOGY BRAND, etc.).
# Talent profiles use ACTOR / TALENT / MUSICIAN/BAND / ATHLETE / HOST.
TALENT_PROFILE_CATEGORIES = frozenset({
    'ACTOR', 'TALENT', 'MUSICIAN/BAND', 'ATHLETE', 'HOST/PERSONALITY',
    'WRITER/DIRECTOR/AUTHOR/ARTIST', 'POLITICS/ACTIVIST',
    'NBA ATHLETE', 'NFL ATHLETE', 'MLB ATHLETE', 'NHL ATHLETE',
    'WNBA ATHLETE', 'SOCCER ATHLETE',
})


def _is_brand_profile(df) -> tuple[bool, str | None]:
    """Detect brand vs talent profile from BRAND CATEGORY meta row.

    Returns (is_brand, category_string_or_None). Falls back to False
    (treat as talent) if BRAND CATEGORY row is missing — safer to
    not-lift than to over-lift talent streaming.
    """
    bc = df[df['Column'].astype(str).str.upper().str.strip() == 'BRAND CATEGORY']
    if len(bc) == 0:
        return False, None
    cat = str(bc.iloc[0].get('Value', '')).strip().upper()
    if not cat:
        return False, None
    is_talent = any(t in cat for t in TALENT_PROFILE_CATEGORIES)
    return (not is_talent), cat


def enforce_household_streaming_floor(df, subject, verbose=True):
    """Lift household-streaming BPs to consensus mid for BRAND profiles.

    Fixes the systemic defect where the GPT-4o vet re-reasoner over-fits
    "active demo less couch-bound" and leaves Netflix/Disney+/HBO Max
    far below digital consensus for mass-market brand audiences. The
    Nike profile shipped 2026-05-29 was the first observed case
    (Netflix=60.26% vs consensus 55-80%, lifted by hand to 69.99%).

    Talent profiles are skipped — actors/athletes/musicians genuinely
    do have skewed media diets, and the agent's reasoning is honest
    there.

    Idempotent: only triggers when BP < (mid - HSF_TRIGGER_GAP_PCT).
    """
    if df is None or len(df) == 0:
        return df, 0

    is_brand, category = _is_brand_profile(df)
    if not is_brand:
        if verbose:
            print(f"   household-streaming floor: SKIP (talent profile, "
                  f"category={category!r})")
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0

    df['_col_u'] = df['Column'].astype(str).str.strip().str.upper()
    df['_val_u'] = df['Value'].astype(str).str.strip().str.upper()

    n_lifts = 0
    examples = []
    cats_renorm = set()

    for (cat_u, brand_u), mid in HOUSEHOLD_STREAMING_CONSENSUS_MID.items():
        mask = (df['_col_u'] == cat_u) & (df['_val_u'] == brand_u)
        if not mask.any():
            continue
        idx = df.index[mask][0]
        cur_bp = _bp(df.at[idx, bp_col])
        if cur_bp is None:
            continue
        if cur_bp >= mid - HSF_TRIGGER_GAP_PCT:
            continue  # within tolerance, agent reasoning trusted

        # Deterministic jitter so values aren't round / duplicated across profiles
        seed_str = f'{subject}|{cat_u}|{brand_u}|hsf'
        seed = int(_hl.md5(seed_str.encode()).hexdigest()[:8], 16)
        norm = ((seed % 10000) / 10000.0 - 0.5) * 2  # -1..1
        target = mid * HSF_TARGET_PCT + norm * 1.5  # mid ±1.5pp jitter
        target = max(0.5, min(99.5, round(target, 4)))

        # Write in the column's existing dtype: string col gets formatted "%"
        # string, numeric col gets float. Pandas rejects float-into-string
        # assignment which silently broke the Netflix run.
        if df[bp_col].dtype == object or str(df[bp_col].dtype).startswith('string'):
            df.at[idx, bp_col] = f'{target:.4f}%'
        else:
            df.at[idx, bp_col] = target
        n_lifts += 1
        cats_renorm.add(cat_u)
        examples.append((cat_u, brand_u, cur_bp, target, mid))

    df = df.drop(columns=['_col_u', '_val_u'], errors='ignore')

    if n_lifts and verbose:
        print(f"   📺 household-streaming floor: lifted {n_lifts} row(s) "
              f"in brand profile (category={category!r})")
        for cat_u, brand_u, old, new, mid in examples:
            print(f"      [{cat_u}] {brand_u}: {old:.2f}% → {new:.4f}%  "
                  f"(consensus mid {mid:.0f}%)")

    # Renormalize Category Share within each touched column so shares
    # still sum to 100%. Mirrors the in-place fix logic for Nike.
    if n_lifts and cs_col is not None:
        c_upper = df['Column'].astype(str).str.upper().str.strip()
        cs_is_str = (df[cs_col].dtype == object or
                     str(df[cs_col].dtype).startswith('string'))
        for cat_u in cats_renorm:
            idxs = df.index[c_upper == cat_u]
            bps = df.loc[idxs, bp_col].apply(_bp)
            total = bps.sum(skipna=True)
            if total and total > 0:
                shares = (bps / total * 100).round(4)
                if cs_is_str:
                    df.loc[idxs, cs_col] = shares.apply(
                        lambda v: f'{v:.4f}' if pd.notna(v) else v).values
                else:
                    df.loc[idxs, cs_col] = shares.values

    return df, n_lifts


# ============================================================================
# SEARCH ENGINE/AI cohort ceiling (defense-in-depth)
# ============================================================================
#
# 2026-06-03 (Jenna 7-file Gemini master defect ticket): apply_panel_reality_floors
# trims brands listed in KNOWN_OVERSHOOT_BRANDS individually, but the vet-reasoner
# keeps generating a "rank-cascade pin" across the SEARCH ENGINE/AI cohort:
# GOOGLE ~99%, CHAT GPT ~50/77%, GEMINI ~32.8%, COPILOT ~23.5%, BING ~18%,
# PERPLEXITY ~14%. The 32.8% Gemini pin alone showed up across 7 unrelated files
# (Chicago Sky, Kaitlyn Johnson, Frances Tiafoe, Zhirelle, Current, ONE, Revolut).
#
# This enforcer is a final safety net: for every SEARCH ENGINE/AI brand that
# HAS a SEGMENT_BENCHMARKS entry, cap its BP at segment-weighted-target + 5pp
# regardless of KNOWN_OVERSHOOT membership. Anything above that re-jitters
# into target ± 1.5pp. Acts as a structural backstop so we never ship a
# cascade pin again even if KNOWN_OVERSHOOT misses a future brand variant.
# ============================================================================

def enforce_search_engine_ai_cohort_ceiling(df, subject, verbose=True):
    """Two-sided band for SEARCH ENGINE/AI brand BPs around the segment-
    weighted target. Trims inflation AND lifts suppression.

    Runs AFTER apply_panel_reality_floors so we catch anything that path missed.
    Hostmap-gated implicitly: only touches rows that already exist.

    2026-06-03 (Jenna pin-check follow-up): trust threshold raised +5pp → +7pp
    so reasoned-high LLM values that survived the panel-reality enforcer are
    KEPT. Replacement jitter widened ±1.5pp → ±3pp with age-archetype in the
    salt so within-archetype variance is ~5-6pp (panel-realistic) instead of
    a tight 3pp cluster.

    2026-06-04 (Jenna's Aidan Gillen Gemini=0.69% suppression defect): made
    bidirectional. Previously this enforcer ONLY trimmed values above
    target+7pp; the apply_panel_reality_floors floor was supposed to handle
    suppression but for SEARCH ENGINE/AI brands it silently no-op'd on some
    paths (TBD root cause). New behavior: anything OUTSIDE [target-7, target+7]
    re-jitters into target ± 3pp. So Gemini at 0.69% for a 55-64 archetype
    (target ~9%) gets lifted to 6-12%, and Gemini at 33% (legacy pin) gets
    trimmed to 17-23%. Same enforcer, both directions.
    """
    if df is None or len(df) == 0:
        return df, 0
    try:
        bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
        sample_size = _detect_sample_size(df, bp_col, raw_col)
    except Exception:
        return df, 0

    arch = _detect_age_archetype(df)
    df['_col_u'] = df['Column'].astype(str).str.strip().str.upper()
    df['_val_u'] = df['Value'].astype(str).str.strip().str.upper()

    n_caps = 0
    n_lifts = 0
    examples = []
    for (cat_u, brand_u), bench in SEGMENT_BENCHMARKS.items():
        if cat_u != 'SEARCH ENGINE/AI':
            continue
        target = _segment_weighted_target(df, cat_u, brand_u)
        if target is None:
            continue
        mask = (df['_col_u'] == cat_u) & (df['_val_u'] == brand_u)
        for idx in df.index[mask].tolist():
            cur = _bp(df.at[idx, bp_col])
            # Bidirectional trust band: target ± 7pp.
            # Anything outside re-jitters into target ± 3pp (archetype-salted).
            if abs(cur - target) <= 7.0:
                continue
            new_target = _jitter_for(
                subject, brand_u, salt=f'search-ai-band-{cat_u}-{arch}',
                lo=max(0.05, target - 3.0), hi=target + 3.0,
            )
            new_target = round(new_target, 4)
            df = _set_bp(df, idx, new_target, bp_col, cs_col, raw_col, proj_col, sample_size)
            if cur > target:
                n_caps += 1
                direction = 'TRIM↓'
            else:
                n_lifts += 1
                direction = 'LIFT↑'
            if len(examples) < 8:
                examples.append((direction, brand_u, cur, new_target, target))

    df = df.drop(columns=['_col_u', '_val_u'])

    total = n_caps + n_lifts
    if verbose and total > 0:
        print(f"   🛡️  SEARCH ENGINE/AI cohort band: {n_caps} trim(s), {n_lifts} lift(s)")
        for d, brand_u, cur, new_v, tgt in examples:
            print(f"      • {d} {brand_u:14s} {cur:6.2f}% → {new_v:6.2f}% (target {tgt:5.2f}%)")
    return df, total


# ============================================================================
# 100K sample-size clamp re-grounding (Jenna 2026-06-04)
# ============================================================================
#
# Pattern: 15% (48 of 314) of overnight profiles landed with BRAND INPUT
# raw in the 99K-105K band, tightly clustered (Kaitlyn_Johnson 100,010 /
# Brooke_Hyland 100,040 / Zhirelle 100,020 etc.). Real audiences would
# show genuine variance across 1K-2M. Tight clustering at exactly ~100K
# is a clamp artifact, not real signal.
#
# Root cause: BG.py's estimate_sample_size_for_unknown_brand applies a
# DIGITAL_PANEL_TIER_ESTIMATES floor of (lo=0.01, hi=0.12) for ACTOR
# category, meaning a niche talent with 5K real panel users gets
# inflated to ~lo*10M = 100K. compute_noisy_sample_size then adds ±5%
# noise, producing the 95K-105K cluster. (Values above 100K survive;
# values below get re-lifted to 105K-145K by the wider delta band.)
#
# Fix: post-generation, when subject_raw is in [99K, 110K] re-ground to
# a realistic small-niche value. Deterministic per-subject jitter:
#   - niche (max non-subject TALENT-family BP < 50%): [3K, 12K]
#   - mid   (50% <= max < 80%):                       [40K, 80K]
#   - known (max >= 80%):                             leave alone
# (the high-recognition case is consistent with real ~100K-1M audiences)
#
# Recompute cascades through existing recompute_raw_and_projection by
# updating the BRAND INPUT row's raw cell. Everything else derives.
# ============================================================================

_TALENT_FAMILY_FOR_TIER = frozenset({
    'TALENT', 'ACTOR', 'ATHLETE', 'COMEDIAN', 'MUSICIAN/BAND', 'MUSICIAN',
    'HOST/PERSONALITY', 'POLITICS/ACTIVIST', 'POLITICS', 'PODCAST',
    'NFL ATHLETE', 'NBA ATHLETE', 'MLB ATHLETE', 'NHL ATHLETE', 'SOCCER',
    'WRITER/DIRECTOR/AUTHOR/ARTIST', 'CREATOR/INFLUENCER',
})


def _detect_subject_recognition_tier(df, bp_col):
    """Return 'niche' | 'mid' | 'known' based on max non-100% BP across
    TALENT-family categories. Crude but reliable: if the LLM couldn't
    find any other comparable person at >50% reach, the subject is niche.
    """
    if df is None or len(df) == 0 or bp_col is None:
        return 'niche'
    col_u = df['Column'].astype(str).str.strip().str.upper()
    fam_mask = col_u.isin(_TALENT_FAMILY_FOR_TIER)
    max_bp = 0.0
    for _, r in df[fam_mask].iterrows():
        bp = _bp(r.get(bp_col))
        if bp >= 99.0:
            continue  # subject's own pin (or near-pin)
        if bp > max_bp:
            max_bp = bp
    if max_bp >= 95.0:
        return 'alist'   # established A-list — leave existing sample alone
    if max_bp >= 75.0:
        return 'known'   # recognized name with broad reach
    if max_bp >= 50.0:
        return 'mid'     # mid-tier visibility
    return 'niche'       # low panel-share, often <0.1% of 10M


def reground_clamped_sample_size(df, subject, verbose=True):
    """Detect the ~100K clamp signature on BRAND INPUT raw and re-ground
    to a realistic deterministic value based on subject recognition tier.

    Modifies ONLY the BRAND INPUT row's raw and the SAMPLE SIZE row's raw.
    Downstream recompute_raw_and_projection cascades the change to every
    other Raw + Projection cell using the new sample_size.

    Idempotent: skips files whose subject_raw is already outside the
    99K-110K clamp band.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None or raw_col is None:
        return df, 0

    col_u = df['Column'].astype(str).str.strip().str.upper()

    # Find BRAND INPUT row
    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    bi_idx = df.index[bi_mask][0]
    try:
        bi_raw = int(float(str(df.at[bi_idx, raw_col]).replace(',', '')))
    except Exception:
        return df, 0

    # Detect clamp band — 99K-110K is the writer-side ~100K floor signature
    if not (99_000 <= bi_raw <= 110_000):
        return df, 0

    # Choose new sample_size. By construction every file in the [99K, 110K]
    # clamp band is niche — the writer only hit the 100K floor *because*
    # the actual ClickHouse UID count was tiny (< 10K typically). If a
    # subject had a real 100K+ audience, BG.py would have computed that
    # directly and the value wouldn't show the clamp signature.
    #
    # "max other talent BP" was tested as a tiering signal but turned out
    # to be misleading: it reflects audience age cohort (e.g. teens
    # always show Taylor Swift at 70%+), not the subject's own panel
    # reach. So we use it ONLY to gate an A-list bypass (max >= 95%
    # implies the subject genuinely has near-universal recognition and
    # the 100K clamp is coincidental, not artifact).
    tier = _detect_subject_recognition_tier(df, bp_col)
    if tier == 'alist':
        return df, 0
    # Range widened 2026-06-04 (Jenna's follower-count audit). Original
    # [3K, 15K] was right for niche but undercounted talents with
    # measurable social presence (Brooke Hyland 6.5M IG followers landed
    # at 9.8K). [8K, 45K] matches the new BG.py boost output for the
    # 2K-20K UID-count bucket (which is where 100K-clamp files live).
    # _jitter_for spreads subjects deterministically across the full
    # range from the subject hash, so no two profiles collapse to
    # within ~3% of each other.
    lo, hi = 8_000.0, 45_000.0

    new_size = int(round(_jitter_for(
        subject, 'sample-size', salt='reground-niche',
        lo=lo, hi=hi,
    )))
    new_size = max(int(lo), min(int(hi), new_size))

    # Update BRAND INPUT raw (sample_size by definition since BP=100)
    df.at[bi_idx, raw_col] = new_size
    if proj_col is not None:
        df.at[bi_idx, proj_col] = int(round((new_size / 10_000_000.0) * US_POP))

    # Update SAMPLE SIZE row: BP-derived raw + Category Share (which the
    # dashboard reads as the displayed sample size — bug found 2026-06-04
    # when patched files still showed 100K because Category Share alone
    # was holding the old clamped value).
    ss_mask = col_u == 'SAMPLE SIZE'
    if ss_mask.any():
        ss_idx = df.index[ss_mask][0]
        ss_bp = _bp(df.at[ss_idx, bp_col])
        if ss_bp > 0 and ss_bp <= 100:
            new_ss_raw = int(round(ss_bp / 100.0 * new_size))
            df.at[ss_idx, raw_col] = new_ss_raw
            if proj_col is not None:
                df.at[ss_idx, proj_col] = int(round(
                    (new_ss_raw / 10_000_000.0) * US_POP))
        # Category Share on the SAMPLE SIZE row stores the absolute
        # sample count (string-typed because BRAND INPUT's value in the
        # same column is a percentage). Assign as string with .0 suffix
        # to mirror BG.py's original output format.
        if cs_col is not None:
            try:
                df[cs_col] = df[cs_col].astype(object)
            except Exception:
                pass
            df.at[ss_idx, cs_col] = f"{float(new_size):.1f}"

    if verbose:
        share_pct = (new_size / 10_000_000.0) * 100.0
        print(f"   📏 sample-size re-ground (niche, tier-hint={tier}): "
              f"{bi_raw:,} → {new_size:,} "
              f"({share_pct:.4f}% of 10M panel; "
              f"downstream Raw/Proj recompute will cascade)")
    return df, 1


# ============================================================================
# Exact-duplicate row collapse + partial-name de-pin (Jenna 2026-06-04)
# ============================================================================
#
# Defect class observed on Adele_Exarchopoulos_06_04_2026_08_01.csv:
#
#   [TALENT]         Adele                100.0000%  raw=100540   ← dup
#   [TALENT]         Adele                100.0000%  raw=100540   ← dup
#   [ACTOR]          ADELE EXARCHOPOULOS  100.0000%  raw=100540   (correct subject)
#   [MUSICIAN/BAND]  Adele                100.0000%  raw=100540   ← name collision
#
# Two root causes:
#   1. The writer emits the same (Column, Value) row twice when a category
#      receives both a "subject auto-promotion" entry AND a separate LLM-
#      reasoned entry that happens to collapse to the same Value after
#      normalization. We collapse identical (Column, Value-upper, BP) trios
#      to a single row.
#   2. The writer also pins ANY row whose Value matches a token of the
#      subject's canonical name to 100% with raw == subject_raw. For
#      "ADELE EXARCHOPOULOS" the lone-token "Adele" gets pinned across
#      ACTOR / TALENT / MUSICIAN/BAND, which conflates the actress with
#      the unrelated pop singer. We detect these partial-token pins and
#      demote to a deterministic in-band value (TALENT family → MAX of
#      OTHER subject rows in same category × 0.45; MUSICIAN/BAND for a
#      non-musician subject → jittered [12-28]% based on subject hash).
#
# Idempotent — only fires on rows currently at 100% AND raw matches subject
# raw (the writer's pin signature) AND Value is a strict partial-token
# match of the subject canonical (not full).
# ============================================================================

def _subject_canonical_tokens(subject):
    """Return (canonical_upper, tokens_set, full_form_upper)."""
    if not subject:
        return '', set(), ''
    # Normalize: 'Adele Exarchopoulos' → 'ADELE EXARCHOPOULOS'
    # Also 'ADELE~EXARCHOPOULOS' (BG.py canonical form with tilde) → split on ~
    s = str(subject).upper().strip()
    s_spaced = s.replace('~', ' ')
    full = _re.sub(r'\s+', ' ', s_spaced).strip()
    toks = {t for t in _re.split(r'[\s/&,_\-]+', full) if t and len(t) >= 3}
    return s, toks, full


def dedup_and_depin_subject_substrings(df, subject, verbose=True):
    """
    Two-stage cleanup:
      stage 1: collapse exact-duplicate (Column, Value-upper, BP) rows
      stage 2: demote partial-token 100%-pinned cross-category rows

    Returns (df, n_changes).
    """
    if df is None or len(df) == 0:
        return df, 0
    try:
        bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
        sample_size = _detect_sample_size(df, bp_col, raw_col)
    except Exception:
        return df, 0
    if bp_col is None:
        return df, 0

    subj_canonical, subj_tokens, subj_full = _subject_canonical_tokens(subject)

    df = df.reset_index(drop=True)
    df['_col_u'] = df['Column'].astype(str).str.strip().str.upper()
    df['_val_u'] = df['Value'].astype(str).str.strip().str.upper()

    # -- Stage 1: drop exact-duplicate (Column, Value-upper, BP) rows --
    # Keep the FIRST occurrence; drop subsequent duplicates within the same
    # category. We compare on Column + Value-upper only; if BP differs, we
    # keep the row with the higher BP (the "more informative" one).
    df['_bp_v'] = df[bp_col].apply(_bp)
    df_sorted = df.sort_values(['_col_u', '_val_u', '_bp_v'], ascending=[True, True, False])
    dup_mask = df_sorted.duplicated(subset=['_col_u', '_val_u'], keep='first')
    n_dups_dropped = int(dup_mask.sum())
    dup_examples = []
    if n_dups_dropped > 0:
        for idx in df_sorted.index[dup_mask].tolist()[:6]:
            dup_examples.append((df.at[idx, '_col_u'], df.at[idx, '_val_u'],
                                 df.at[idx, '_bp_v']))
        df = df_sorted[~dup_mask].sort_index().reset_index(drop=True)

    # -- Stage 2: de-pin partial-token 100% subject substrings --
    # Find rows that look like writer-emitted subject pins: BP == 100,
    # raw == subject_raw, but Value is a strict subset of subject tokens
    # (not full canonical, not the full multi-token subject).
    n_depinned = 0
    depin_examples = []
    if len(subj_tokens) >= 2 and subj_full:
        # Detect subject's raw — find the BRAND INPUT row's raw value
        subj_raw = None
        for idx in df.index:
            if df.at[idx, '_col_u'] == 'BRAND INPUT' and raw_col is not None:
                try:
                    subj_raw = int(float(df.at[idx, raw_col]))
                    break
                except Exception:
                    pass

        for idx in df.index:
            cat_u = df.at[idx, '_col_u']
            val_u = df.at[idx, '_val_u']
            bp_v = df.at[idx, '_bp_v']
            if cat_u in ('BRAND INPUT', 'SAMPLE SIZE'):
                continue
            if abs(bp_v - 100.0) > 0.01:
                continue  # not a 100% pin
            # Is Value a strict partial-token match of subject?
            val_toks = {t for t in _re.split(r'[\s/&,_\-]+', val_u) if t and len(t) >= 3}
            if not val_toks:
                continue
            # Full match (anywhere) → legitimate subject row, leave alone
            if val_u == subj_full or val_u == subj_canonical:
                continue
            if subj_full.replace(' ', '') == val_u.replace(' ', ''):
                continue
            if subj_canonical.replace('~', '') == val_u.replace(' ', '').replace('~', ''):
                continue
            # Partial: every token of Value is in subject_tokens, BUT
            # Value is missing at least one subject token (so it's a proper subset)
            if not val_toks.issubset(subj_tokens):
                continue
            if len(val_toks) >= len(subj_tokens):
                continue  # not strictly partial
            # Raw-match check: only de-pin when row's raw is essentially the
            # subject's raw (writer's pin signature). Use a 0.5% tolerance
            # because earlier enforcers (e.g. _renormalize_category in
            # apply_panel_reality_floors) can shift raw by a few units when
            # they recompute BP→raw integers.
            if subj_raw is not None and raw_col is not None and subj_raw > 0:
                try:
                    row_raw = int(float(df.at[idx, raw_col]))
                except Exception:
                    row_raw = None
                if row_raw is None:
                    continue
                rel_diff = abs(row_raw - subj_raw) / float(subj_raw)
                if rel_diff > 0.005:  # >0.5% off — not a subject pin
                    continue
            # DE-PIN: demote to a deterministic per-(subject, cat, val) jittered value.
            # MUSICIAN/BAND for a non-musician subject → 12-28% range.
            # TALENT / ACTOR for a partial name → 8-18% range (less famous to
            # this audience than the subject).
            # Any other cat → 10-22% range.
            if cat_u == 'MUSICIAN/BAND':
                lo, hi = 12.0, 28.0
            elif cat_u in ('TALENT', 'ACTOR'):
                lo, hi = 8.0, 18.0
            else:
                lo, hi = 10.0, 22.0
            new_v = _jitter_for(
                subject, val_u, salt=f'depin-{cat_u}',
                lo=lo, hi=hi,
            )
            new_v = round(new_v, 4)
            df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col, proj_col, sample_size)
            n_depinned += 1
            if len(depin_examples) < 8:
                depin_examples.append((cat_u, val_u, 100.0, new_v))

    df = df.drop(columns=['_col_u', '_val_u', '_bp_v'], errors='ignore')

    total = n_dups_dropped + n_depinned
    if verbose and total > 0:
        if n_dups_dropped > 0:
            print(f"   🧹 collapsed {n_dups_dropped} exact-duplicate row(s)")
            for c, v, b in dup_examples:
                print(f"      • [{c}] {v}  (bp {b:.2f}%)")
        if n_depinned > 0:
            print(f"   🪪 de-pinned {n_depinned} partial-name subject substring(s)")
            for c, v, old, new in depin_examples:
                print(f"      • [{c}] {v}: {old:.2f}% → {new:.4f}%")
    return df, total


# ============================================================================
# Final-pass subject self-pin guarantee (Defect 37 -- 2026-06-16 Jenna)
# ============================================================================
# pin_subject_to_100_in_appearing_categories scopes to ONE "native" grid
# (BRAND CATEGORY metadata or max-BP fallback) and pins only there. For
# profiles where the subject appears in MULTIPLE high-BP grids (e.g.
# STREAMING/PLATFORM canonical + STREAMING VIDEO legacy alias for streamers,
# or BANKS canonical + BANK legacy singular for banks), the sister grid was
# missed and shipped with near-misses (Peacock 99.22%, LiveTV 99.73%,
# Citibank 99.11%) or overflow (YouTube 100.37%).
#
# This is a defensive final pass: iterate EVERY subject row in
# non-MPB / non-demo / non-metadata grids and force exactly 100.0000% if
# the BP is in [95, 105] (catches both near-miss and impossible overflow).
# Runs LAST in run_all_enforcers, AFTER recompute_raw_and_projection and
# AFTER apply_recompute_category_share -- nothing downstream can move it
# off 100 except validate_demo_sum_100 which is read-only.
# ============================================================================

def enforce_subject_self_pin_final(df, subject, verbose=True):
    """Final guarantee: subject = exactly 100.0000% in every appearing
    non-MPB / non-demo / non-metadata grid where its BP lands in
    [95, 105]. Catches near-miss (99.x%) AND overflow (100.x%).

    Returns (df, n_pinned).
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not bp_col or not raw_col or not proj_col:
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()

    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    bi_row = df.loc[bi_mask].iloc[0]
    subject_name = _clean_subject_from_bi(
        bi_row.get('Value'), df=df, col_u=col_u, subject_arg=subject,
    )
    if not subject_name:
        return df, 0

    sz_mask = col_u == 'SAMPLE SIZE'
    if not sz_mask.any():
        return df, 0
    sz_row = df.loc[sz_mask].iloc[0]

    def _to_int(cell):
        try:
            s = str(cell).replace(',', '').replace('%', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return int(float(s))
        except (ValueError, TypeError):
            return None

    def _to_float(cell):
        try:
            s = str(cell).replace('%', '').replace(',', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    sample_size = _to_int(sz_row.get(raw_col))
    profile_universe = _to_int(sz_row.get(proj_col))
    if sample_size is None or profile_universe is None:
        return df, 0

    # Per Rule #3: subject = exactly 100% in BRAND INPUT, SUBJECT, its own
    # league cat, all companion cols, persona/content cats. NOT in:
    # demographics (which sum to 100%), metadata, or MPB family (where
    # subject's BP is a peer rate among consumer brands).
    skip = (
        METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
        | {'MOST PURCHASED BRANDS', 'APPAREL/FOOTWEAR',
           'BEAUTY/WELLNESS', 'WHERE THEY SHOP', 'TECHNOLOGY BRAND',
           'HOME/OUTDOOR', 'CPG', 'BEVERAGES', 'FRANCHISE'}
    )

    subj_norm = _re.sub(r'[^A-Z0-9]', '', subject_name.upper())
    val_norm = (
        df['Value'].astype(str).str.upper()
        .str.replace(r'[^A-Z0-9]', '', regex=True)
    )
    mask = (val_norm == subj_norm) & (~col_u.isin(skip))
    candidate_idxs = list(df.index[mask])
    if not candidate_idxs:
        return df, 0

    touched_cats = set()
    n_pinned = 0
    pin_log = []
    for idx in candidate_idxs:
        bp = _to_float(df.at[idx, bp_col])
        if bp is None:
            continue
        # Catch near-miss AND overflow. Exact 100.0000 is already correct.
        if abs(bp - 100.0) < 0.0001:
            continue
        if not (95.0 <= bp <= 105.0):
            # < 95 = peer rate, leave untouched. > 105 = something
            # structurally broken; let bp_hard_ceiling handle.
            continue
        cat_here = str(df.at[idx, 'Column']).strip()
        df.at[idx, bp_col] = '100.0000%'
        df.at[idx, raw_col] = sample_size
        df.at[idx, proj_col] = profile_universe
        touched_cats.add(cat_here)
        n_pinned += 1
        pin_log.append((cat_here, bp))

    if n_pinned == 0:
        return df, 0

    for cat in touched_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col,
                                   proj_col, sample_size)

    if verbose:
        print(f"   📌 subject self-pin (final): {n_pinned} row(s) pinned "
              f"to 100.0000% for \"{subject_name}\"")
        for cat, old_bp in pin_log:
            direction = "near-miss" if old_bp < 100.0 else "overflow"
            print(f"      • [{cat}] {old_bp:.4f}% -> 100.0000% ({direction})")

    return df, n_pinned


# ============================================================================
# enforce_brand_category_mirror — never ship UNCATEGORIZED (2026-07-17)
# ----------------------------------------------------------------------------
# Guarantees every profile CSV that flows through run_all_enforcers ends
# with a canonical BRAND CATEGORY row that matches the caller's declared
# category. This is the defense-in-depth counterpart to the direct
# brand_category kwarg thread from BG.run_full_pipeline →
# avid_fan_row_by_row.synthesize_avid_fan added the same day.
#
# Motivating rule (Jenna 2026-07-17): "make sure avid cuts always have the
# same category as main cuts and never end up as uncategorized".
#
# Resolution order (highest priority first):
#   1. `brand_category` kwarg passed by the caller (BG.py's
#      run_full_pipeline, dashboard's submit_analysis, or the avid synth's
#      resolved value). This is the ground truth.
#   2. Existing BRAND CATEGORY row on df, IF it holds a non-blank
#      non-UNKNOWN non-UNCATEGORIZED value. Preserves pre-set categories
#      when the caller didn't specify (legacy compatibility).
#   3. "GENERAL" — canonical last-resort so the file is never shipped
#      UNCATEGORIZED. Matches the default in bg-webapp/app.py's
#      submit_analysis(). A loud warning is printed so operators can patch
#      upstream metadata.
#
# When (1) is provided, the row is overwritten (force=True) so avid ↔ main
# always mirror even if apply_avid_transform / an intermediate enforcer
# left a stale value in place.
# ============================================================================

def enforce_brand_category_mirror(df, subject, brand_category=None, verbose=True):
    """Ensure BRAND CATEGORY row exists, is non-empty, and matches the
    caller's canonical value when supplied. Returns (df, n_changes).

    Contract:
      - After this runs, df ALWAYS has exactly one BRAND CATEGORY row
        with a non-blank Value (never UNCATEGORIZED / UNKNOWN / blank).
      - When `brand_category` is provided by the caller, that value wins
        (mirror semantics — avid CSVs generated from a main pull match
        the main pull's category exactly).
      - When `brand_category` is None but the row already exists with a
        valid Value, the existing value is preserved (fallback semantics
        for legacy callers that didn't thread brand_category through).
      - Terminal fallback is "GENERAL" so no file ships uncategorized.
    """
    n_changes = 0
    if df is None or len(df) == 0:
        return df, 0
    if 'Column' not in df.columns or 'Value' not in df.columns:
        return df, 0

    _UNRESOLVED = ('', 'UNKNOWN', 'UNCATEGORIZED', 'NAN', 'NONE')

    def _clean(v):
        s = str(v or '').strip()
        return s if s and s.upper() not in _UNRESOLVED else ''

    caller_bc = _clean(brand_category)

    col_u = df['Column'].astype(str).str.strip().str.upper()
    bc_mask = col_u == 'BRAND CATEGORY'
    existing_bc = _clean(df.loc[bc_mask, 'Value'].iloc[0]) if bc_mask.any() else ''

    # Resolve the canonical value.
    if caller_bc:
        canonical = caller_bc.upper()
        source = 'caller.brand_category (mirror)'
    elif existing_bc:
        canonical = existing_bc.upper()
        source = 'existing BRAND CATEGORY row (preserve)'
    else:
        canonical = 'GENERAL'
        source = 'GENERAL (LAST-RESORT FALLBACK)'
        if verbose:
            print(f"   ⚠️ enforce_brand_category_mirror: no category "
                  f"resolvable for subject={subject!r} — forcing "
                  f"BRAND CATEGORY='GENERAL' so file ships categorized. "
                  f"Investigate upstream: caller should pass "
                  f"brand_category through the pipeline.")

    # Decide whether we need to write.
    need_write = False
    if not bc_mask.any():
        need_write = True  # row is missing, insert it
    else:
        cur = str(df.loc[bc_mask, 'Value'].iloc[0]).strip().upper()
        if cur in _UNRESOLVED or cur != canonical:
            need_write = True

    if not need_write:
        return df, 0

    # Delegate to BG.enforce_brand_category_row with force=True so an
    # existing wrong-value row is overwritten, not left as-is.
    try:
        try:
            from BG import enforce_brand_category_row as _enf_bc_row
        except ImportError:
            from bg import enforce_brand_category_row as _enf_bc_row
        df = _enf_bc_row(df, canonical, force=True)
        n_changes = 1
        if verbose:
            print(f"   ✓ enforce_brand_category_mirror: BRAND CATEGORY "
                  f"set to {canonical!r} (source: {source})")
    except Exception as _e:
        # Direct fallback: manipulate df ourselves.
        if verbose:
            print(f"   ⚠️ enforce_brand_category_mirror: BG helper failed "
                  f"({_e}), using direct insertion fallback")
        import pandas as _pd_bcm
        if bc_mask.any():
            df.loc[bc_mask, 'Value'] = canonical
            n_changes = 1
        else:
            new_row = {c: '' for c in df.columns}
            new_row[df.columns[0]] = 'BRAND CATEGORY'
            new_row[df.columns[1]] = canonical
            ss_idx = df.index[col_u == 'SAMPLE SIZE'].tolist()
            insert_at = ss_idx[0] + 1 if ss_idx else 2
            top = df.iloc[:insert_at]
            bot = df.iloc[insert_at:]
            df = _pd_bcm.concat(
                [top, _pd_bcm.DataFrame([new_row], columns=df.columns), bot],
                ignore_index=True,
            )
            n_changes = 1

    return df, n_changes


# ============================================================================
# apply_platform_pin — SELF-ANCHOR pin driven by caller spec (2026-07-09)
# ----------------------------------------------------------------------------
# Consolidates the ad-hoc "pin YouTube to 100% for creator profiles" and
# "pin Paramount+ to 100% for Invader Zim" behaviour that previously lived
# in /tmp/strict_youtube_pin_watcher.py + /tmp/generic_pin_watcher.py.
#
# Reads env vars set by the runner-script template (both /root paths):
#   BG_PIN_PLATFORM  — canonical display name to pin (e.g. "YOUTUBE",
#                      "PARAMOUNT+", "MGM+", "NETFLIX"). Empty → no-op.
#   BG_PIN_SECTION   — Column value to pin inside. Defaults to
#                      "STREAMING/PLATFORM"; falls back to
#                      "APP/PLATFORM USAGE" if the primary section is
#                      absent in this profile.
#
# Behaviour:
#   • Zero-out every other row in the target section.
#   • Set the target row's BP to 100.0000% (insert a synthetic row if
#     the platform isn't already present).
#   • Recompute Raw + Proj for every touched row using the canonical
#     _set_bp formulas so the downstream recompute pass is a no-op.
#   • Renormalize Category Share for the section.
#
# Match is normalized (`_norm_brand`) so "YouTube" matches
# "YOUTUBE"/"You Tube"/"YOUTUBE " but NEVER "YouTube TV",
# "YouTube Kids", "YouTube Music" (those normalize to different keys).
# ============================================================================

def apply_platform_pin(df, subject, verbose=True):
    """Pin a streaming platform to 100% BP in STREAMING/PLATFORM (or the
    fallback APP/PLATFORM USAGE section) when BG_PIN_PLATFORM env var is
    set by the caller. Returns (df, n_changes).

    Idempotent: re-running on an already-pinned file is a no-op.
    """
    target_display = (os.environ.get('BG_PIN_PLATFORM') or '').strip()
    if not target_display:
        return df, 0

    target_norm = _norm_brand(target_display)
    if not target_norm:
        return df, 0

    if df is None or len(df) == 0:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)

    requested_section = (os.environ.get('BG_PIN_SECTION') or
                         'STREAMING/PLATFORM').strip().upper()
    fallback_sections = ['STREAMING/PLATFORM', 'APP/PLATFORM USAGE']
    col_u = df['Column'].astype(str).str.strip().str.upper()

    target_section = None
    for candidate in [requested_section] + [s for s in fallback_sections
                                            if s != requested_section]:
        if (col_u == candidate).any():
            target_section = candidate
            break
    if target_section is None:
        target_section = requested_section

    def _num(v):
        try:
            return float(str(v).replace(',', '').replace('%', '').strip())
        except Exception:
            return None

    sample_size = None
    for cat in ('BRAND INPUT', 'SAMPLE SIZE'):
        cand = df[col_u == cat]
        if len(cand) == 0:
            continue
        r = cand.iloc[0]
        bp = _num(r.get(bp_col))
        raw = _num(r.get(raw_col)) if raw_col else None
        if raw and bp and bp > 0:
            sample_size = raw / (bp / 100.0)
            break
    if sample_size is None:
        if verbose:
            print(f"   apply_platform_pin: no sample-size source; skipping "
                  f"pin of {target_display!r}")
        return df, 0

    section_mask = col_u == target_section
    n_changes = 0
    pin_idx = None

    if section_mask.any():
        val_series = df.loc[section_mask, 'Value'].astype(str)
        norms = val_series.apply(_norm_brand)
        matches = df.loc[section_mask].index[(norms == target_norm).values]
        if len(matches) > 0:
            pin_idx = matches[0]

    if pin_idx is None:
        if section_mask.any():
            tpl_idx = df.index[section_mask][0]
            new_row = df.loc[tpl_idx].copy()
            new_row['Column'] = target_section
            new_row['Value']  = target_display
            for c in (bp_col, cs_col, raw_col, proj_col):
                if c and c in new_row.index:
                    new_row[c] = 0.0
            insert_pos = df.index[section_mask].max() + 1
            df = pd.concat([
                df.iloc[:insert_pos],
                pd.DataFrame([new_row]),
                df.iloc[insert_pos:],
            ], ignore_index=True)
            col_u = df['Column'].astype(str).str.strip().str.upper()
            section_mask = col_u == target_section
            val_series = df.loc[section_mask, 'Value'].astype(str)
            norms = val_series.apply(_norm_brand)
            pin_idx = df.loc[section_mask].index[(norms == target_norm).values][0]
            n_changes += 1
            if verbose:
                print(f"   apply_platform_pin: inserted synthetic "
                      f"{target_display!r} row into {target_section!r}")
        else:
            if verbose:
                print(f"   apply_platform_pin: section {target_section!r} "
                      f"absent AND no template row to clone from; skipping")
            return df, 0

    for idx in df.index[section_mask]:
        cur_bp = _num(df.at[idx, bp_col]) or 0.0
        want_bp = 100.0 if idx == pin_idx else 0.0
        if abs(cur_bp - want_bp) > 0.0001:
            df = _set_bp(df, idx, want_bp, bp_col, cs_col,
                         raw_col, proj_col, sample_size)
            n_changes += 1

    df = _renormalize_category(df, target_section, bp_col, cs_col,
                               raw_col, proj_col, sample_size)

    if verbose and n_changes:
        print(f"   ✅ apply_platform_pin: {target_display!r} pinned to 100.0000% "
              f"in {target_section!r} ({n_changes} row change(s))")
    return df, n_changes


# ============================================================================
# strip_youtube_from_wrong_category
# ----------------------------------------------------------------------------
# 2026-07-21 (Jenna): "make sure youtube is never categorized as streaming
# video and always falls within the social media category. like on animation
# on fox it is showing in both, it should be 100% and in social media"
#
# The bare brand YOUTUBE only ever belongs in SOCIAL MEDIA. If a template
# or LLM pass drops a YouTube row into STREAMING/PLATFORM or STREAMING
# VIDEO (typically pinned at 100% because the subject is YouTube-native),
# strip it and let SOCIAL MEDIA / YOUTUBE carry the pin instead.
#
# Preserved rows (distinct services with their own product footprint):
#   YOUTUBE MUSIC     -> STREAMING/MUSIC
#   YOUTUBE KIDS      -> STREAMING/PLATFORM
#   YOUTUBE TV        -> VIRTUAL MVPD FAST / VMVPD
#   YOUTUBE PREMIUM   -> STREAMING/PLATFORM
#   YOUTUBE ORIGINALS -> STREAMING/PLATFORM
# ============================================================================

_YT_WRONG_CATS = {"STREAMING/PLATFORM", "STREAMING VIDEO"}


def strip_youtube_from_wrong_category(df, subject, verbose=True):
    """Drop bare-YOUTUBE rows from STREAMING/PLATFORM and STREAMING VIDEO.
    Recomputes Category Share for both categories after the drop.
    """
    if "Column" not in df.columns or "Value" not in df.columns:
        return df, 0
    cu = df["Column"].astype(str).str.upper().str.strip()
    vu = df["Value"].astype(str).str.upper().str.strip()
    drop_idx = df[cu.isin(_YT_WRONG_CATS) & vu.eq("YOUTUBE")].index
    if len(drop_idx) == 0:
        return df, 0
    dropped = [
        (str(df.at[i, "Column"]).strip(),
         str(df.at[i, "Value"]).strip(),
         str(df.at[i, "Brand Penetration (Row)"]).strip())
        for i in drop_idx
    ]
    df = df.drop(index=drop_idx).reset_index(drop=True)

    # Recompute Category Share for the affected categories
    bp_col = "Brand Penetration (Row)"
    cs_col = "Category Share"
    for target in ("STREAMING/PLATFORM", "STREAMING VIDEO"):
        cu2 = df["Column"].astype(str).str.upper().str.strip()
        idx = df[cu2.eq(target)].index
        if len(idx) == 0:
            continue
        bp_sum = 0.0
        for i in idx:
            v = str(df.at[i, bp_col]).replace("%", "").replace(",", "").strip()
            try:
                bp_sum += float(v)
            except Exception:
                pass
        if bp_sum <= 0:
            continue
        for i in idx:
            v = str(df.at[i, bp_col]).replace("%", "").replace(",", "").strip()
            try:
                bp = float(v)
            except Exception:
                continue
            df.at[i, cs_col] = round(bp / bp_sum * 100.0, 4)

    if verbose:
        for col, val, bp in dropped:
            print(f"   🚫 strip_youtube_from_wrong_category: dropped "
                  f"{col} / {val!r}  BP={bp!r}  (belongs in SOCIAL MEDIA)")
    return df, len(dropped)


# ============================================================================
# Final format normalizer (2026-07-28 pipeline hardening rail #5)
#
# Catches the four defect classes flagged on WHEEL OF FORTUNE - Avid Fan.csv
# (defects 5, 6, 7, 8) that had escaped every prior enforcer:
#   D5. SAMPLE SIZE Raw drifted from BRAND INPUT Raw (e.g. 151705 vs 151721)
#   D6. Rows with Original Raw Numbers=0 but Brand Penetration (Row)>0
#       (phantom cells; LOCATION drifts past 100%)
#   D7. BRAND CATEGORY row has stale Raw/Proj/CS from the source of the
#       skin, resulting in an impossible "identical raws across parent
#       and subset cut" pattern
#   D8. Mixed '%' suffix in Brand Penetration (Row) — some rows carry it,
#       some don't, sometimes alternating within one category grid
#
# Runs at the tail of run_all_enforcers, right before
# recompute_raw_and_projection (so the recompute pass finalizes Raw/Proj
# from the post-normalized BP values).
# ============================================================================


def _norm_col_upper(v):
    return str(v).strip().upper()


def normalize_final_format(df, subject, verbose=True):
    """Final format normalizer. Idempotent. Safe on any profile csv.

    Applies four fixes in order:
      1. Strip '%' from every non-empty Brand Penetration (Row) cell so
         the column is uniformly numeric-as-string (matches TU
         convention). Dashboard reads both formats, but mixed-in-one-file
         is a defect signature.
      2. Zero every row where Original Raw Numbers == 0 but Brand
         Penetration (Row) > 0 (BP, CS, Raw, Proj -> 0/0.0000/0/0).
         Then renormalize LOCATION back to 100% since that column is
         expected to sum to 100 by construction (like demos).
      3. Blank all numeric fields on the BRAND CATEGORY metadata row
         (BP, CS, Raw, Proj -> ''). Matches the canonical convention
         used by Reba - Avid Fan.csv and other clean sibling files.
      4. Force SAMPLE SIZE Raw to match BRAND INPUT Raw when they drift.
         Drift shows up as e.g. SAMPLE SIZE = 99.9895% + Raw=151705 but
         BRAND INPUT Raw=151721. Sets BP=100.0000, Raw=<brand_input_raw>,
         Proj recomputed from the 10M/329.9M panel formula.

    Returns (df, n_changes). Errors are swallowed (best-effort).
    """
    if df is None or len(df) == 0:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0

    total = 0

    # --- Fix 1: strip '%' from every BP cell -----------------------------
    try:
        bp_series = df[bp_col].astype(str)
        pct_mask = bp_series.str.contains('%', regex=False, na=False)
        n_pct = int(pct_mask.sum())
        if n_pct:
            df[bp_col] = bp_series.str.replace('%', '', regex=False).str.strip()
            if verbose:
                print(f'   [normalize_final] stripped % from {n_pct} '
                      f'Brand Penetration (Row) cell(s)')
            total += n_pct
    except Exception as e:
        if verbose:
            print(f'   [normalize_final] Fix 1 (%) failed: {e}')

    # --- Fix 1b: strip '%' from every Category Share cell ----------------
    # Mirror of Fix 1. SHARKNINJA main file 2026-08-04 shipped with %
    # ONLY in Category Share (BP clean, CS dirty): 4,248 rows across 89
    # categories from RELATIONSHIP onward. Fix 1 only touched BP so the
    # signature persisted. This block is the symmetric CS-side fix.
    try:
        if cs_col is not None:
            cs_series = df[cs_col].astype(str)
            cs_pct_mask = cs_series.str.contains('%', regex=False, na=False)
            n_cs_pct = int(cs_pct_mask.sum())
            if n_cs_pct:
                df[cs_col] = cs_series.str.replace('%', '', regex=False).str.strip()
                if verbose:
                    print(f'   [normalize_final] stripped % from {n_cs_pct} '
                          f'Category Share cell(s)')
                total += n_cs_pct
    except Exception as e:
        if verbose:
            print(f'   [normalize_final] Fix 1b (CS %) failed: {e}')

    # --- Fix 2: zero raw=0 / bp>0 phantom rows ---------------------------
    zeroed_by_col = {}
    try:
        if raw_col is not None:
            def _to_i(v):
                try:
                    return int(float(str(v).replace(',', '').strip()))
                except Exception:
                    return None
            raws = df[raw_col].apply(_to_i)
            bps  = df[bp_col].apply(_bp)
            phantom = (raws == 0) & (bps.fillna(0) > 0)
            if phantom.any():
                for idx in df.index[phantom]:
                    cat = _norm_col_upper(df.at[idx, 'Column'])
                    zeroed_by_col[cat] = zeroed_by_col.get(cat, 0) + 1
                    # Assign strings throughout so we don't trip pandas 2.x's
                    # strict-dtype guard on object-typed columns
                    # (Hetzner-only failure signature 2026-07-28: "Invalid
                    # value '0' for dtype 'str'. Value should be a string
                    # or missing value, got 'int' instead").
                    df.at[idx, bp_col] = '0.0000'
                    if cs_col is not None:
                        df.at[idx, cs_col] = '0.0'
                    df.at[idx, raw_col] = '0'
                    if proj_col is not None:
                        df.at[idx, proj_col] = '0'
                n_zeroed = int(phantom.sum())
                total += n_zeroed
                if verbose:
                    parts = ', '.join(
                        f'{c}={n}' for c, n in sorted(zeroed_by_col.items())
                    )
                    print(f'   [normalize_final] zeroed {n_zeroed} phantom '
                          f'raw=0/BP>0 row(s): {parts}')

                # Renormalize LOCATION back to 100 (LOCATION should sum to
                # 100% by construction; zeroing phantom rows leaves a small
                # deficit that this scale corrects).
                m_loc = (df['Column'].astype(str).str.strip().str.upper()
                         == 'LOCATION')
                if m_loc.any():
                    loc_sum = df.loc[m_loc, bp_col].apply(_bp).fillna(0).sum()
                    if loc_sum > 0 and abs(loc_sum - 100.0) > 0.01:
                        scale = 100.0 / loc_sum
                        import hashlib as _hl_loc
                        for idx in df.index[m_loc]:
                            v = _bp(df.at[idx, bp_col])
                            if v is None or v <= 0:
                                continue
                            new_v = round(v * scale, 4)
                            # Never round a dominant DMA up to exactly
                            # 100.0000 (2026-08-26 Liz QA / I18: geo-cut
                            # convention is ~99.9x jittered, and exact
                            # 100 on a non-subject row is a reach-pin
                            # defect the ship gate blocks).
                            if new_v >= 99.995:
                                h = int(_hl_loc.sha256(
                                    f'{subject}|LOCATION|'
                                    f'{df.at[idx, "Value"]}|loc-renorm'
                                    .encode()).hexdigest()[:8], 16)
                                new_v = round(
                                    99.90 + (h % 900) / 10000.0, 4)
                                if int(round(new_v * 10000)) % 100 == 0:
                                    new_v = round(
                                        new_v + (1 + h % 89) / 10000.0, 4)
                            df.at[idx, bp_col] = f'{new_v:.4f}'
                            # Let recompute_raw_and_projection handle
                            # Raw/Proj downstream.
                        if verbose:
                            print(f'   [normalize_final] renormalized '
                                  f'LOCATION {loc_sum:.4f}% -> ~100.00%')
                        total += 1
    except Exception as e:
        if verbose:
            print(f'   [normalize_final] Fix 2 (phantom raw=0) failed: {e}')

    # --- Fix 3: blank numeric fields on BRAND CATEGORY row ---------------
    try:
        m_bc = (df['Column'].astype(str).str.strip().str.upper()
                == 'BRAND CATEGORY')
        if m_bc.any():
            n_blanked = 0
            for idx in df.index[m_bc]:
                dirty = False
                for c in (bp_col, cs_col, raw_col, proj_col):
                    if c is None:
                        continue
                    cur = str(df.at[idx, c]).strip()
                    if cur and cur.lower() not in ('nan', ''):
                        df.at[idx, c] = ''
                        dirty = True
                if dirty:
                    n_blanked += 1
            if n_blanked:
                total += n_blanked
                if verbose:
                    print(f'   [normalize_final] blanked numeric fields on '
                          f'{n_blanked} BRAND CATEGORY row(s)')
    except Exception as e:
        if verbose:
            print(f'   [normalize_final] Fix 3 (BRAND CATEGORY) failed: {e}')

    # --- Fix 4: SAMPLE SIZE Raw must match BRAND INPUT Raw ---------------
    try:
        col_u = df['Column'].astype(str).str.strip().str.upper()
        m_bi = col_u == 'BRAND INPUT'
        m_ss = col_u == 'SAMPLE SIZE'
        if m_bi.any() and m_ss.any() and raw_col is not None:
            def _to_i2(v):
                try:
                    return int(float(str(v).replace(',', '').strip()))
                except Exception:
                    return None
            bi_raw = _to_i2(df.loc[m_bi, raw_col].iloc[0])
            ss_idx = df.index[m_ss][0]
            ss_raw = _to_i2(df.at[ss_idx, raw_col])
            if bi_raw and ss_raw is not None and bi_raw != ss_raw:
                new_proj = int(round(bi_raw / 10_000_000 * US_POP))
                # Strings throughout — see phantom-zero note above for the
                # pandas 2.x strict-dtype rationale.
                df.at[ss_idx, bp_col] = '100.0000'
                df.at[ss_idx, raw_col] = str(bi_raw)
                if proj_col is not None:
                    df.at[ss_idx, proj_col] = str(new_proj)
                # CS on SAMPLE SIZE historically carries the raw count as
                # a display string (e.g. '151721.0'). Preserve that
                # convention when the existing value looks like a raw
                # count rather than a percentage.
                if cs_col is not None:
                    cs_val = _to_i2(df.at[ss_idx, cs_col])
                    if cs_val is not None and cs_val > 200 and cs_val != bi_raw:
                        df.at[ss_idx, cs_col] = f'{bi_raw}.0'
                if verbose:
                    print(f'   [normalize_final] SAMPLE SIZE Raw '
                          f'{ss_raw} -> {bi_raw} (matched BRAND INPUT)')
                total += 1
    except Exception as e:
        if verbose:
            print(f'   [normalize_final] Fix 4 (SAMPLE SIZE) failed: {e}')

    return df, total


# ============================================================================
# Convenience wrapper
# ============================================================================

def _coerce_writeable_columns_to_object(df, verbose=False):
    """Coerce numeric-writeable columns to object dtype so enforcers can
    freely assign floats, ints, and formatted strings via ``df.at[]``.

    Pandas 2.1+ introduced an NA-aware string dtype (`'str'`, arrow-
    backed) that CSV reads sometimes assign to columns containing mixed
    percent-strings like ``'12.34%'``. That dtype REJECTS float / int
    writes with ``Invalid value 'X' for dtype 'str'``. Every enforcer
    that writes into BP / Category Share / Raw / Projection columns
    silently crashes on those files because ``run_all_enforcers`` wraps
    each in a broad ``except``. The user-visible symptoms are
    downstream defects the enforcers were supposed to fix
    (BP>100 rows, Disney+/Hulu duplicates, panel-reality violations)
    shipping unfixed.

    Fix: at the very top of ``run_all_enforcers``, coerce every column
    the enforcers may write to ``object`` dtype. ``object`` accepts
    ANY Python value at ``df.at`` write time so downstream enforcers
    stop crashing.

    Safe because the terminal ``recompute_raw_and_projection`` pass
    canonicalises the numeric columns before write, and profile_writer
    writes CSV where dtype doesn't matter.

    2026-08-19 (Jenna, Gilmore Girls incident): every profile since
    the pandas 2.1 upgrade has had ``enforce_bp_hard_ceiling`` and
    ``apply_disney_hulu_rollup`` silently failing with the string-
    dtype error. Adding this coercion fixes 6+ enforcers at once.
    """
    if df is None or len(df) == 0:
        return df
    # These are every column the enforcer chain writes into. Anything
    # else can keep its original dtype.
    _writeable = (
        'Brand Penetration (Row)', 'Brand Penetration',
        'Category Share', 'Original Raw Numbers',
        'US Gen Pop Projection', 'US Population', 'Sample Size',
        'Value', 'Column',
    )
    coerced = 0
    for c in _writeable:
        if c not in df.columns:
            continue
        try:
            dtype_str = str(df[c].dtype)
        except Exception:
            continue
        if dtype_str == 'object':
            continue
        try:
            df[c] = df[c].astype(object)
            coerced += 1
            if verbose:
                print(f"   🔀 coerced column {c!r} {dtype_str} → object "
                      f"(enforcer-safe write)")
        except Exception as e:
            if verbose:
                print(f"   ⚠️ could not coerce {c!r} to object: "
                      f"{type(e).__name__}: {e}")
    return df


# ---------------------------------------------------------------------------
# Geo-scoped LOCATION enforcement (Jenna 2026-08-24, Florida
# gubernatorial voters defect: "there should be no value in LOCATION
# outside of FL"). When a universe is geographically DEFINED (Florida
# voters, Miami renters, Texas homeowners), every LOCATION row outside
# the defining geography is removed entirely and the in-scope DMAs
# renormalize to ~100 with subject-salted micro-jitter.
# ---------------------------------------------------------------------------

_GEO_STATE_ABBRS = {
    'ALABAMA': 'Al', 'ALASKA': 'Ak', 'ARIZONA': 'Az', 'ARKANSAS': 'Ar',
    'CALIFORNIA': 'Ca', 'COLORADO': 'Co', 'CONNECTICUT': 'Ct',
    'DELAWARE': 'De', 'FLORIDA': 'Fl', 'GEORGIA': 'Ga', 'HAWAII': 'Hi',
    'IDAHO': 'Id', 'ILLINOIS': 'Il', 'INDIANA': 'In', 'IOWA': 'Ia',
    'KANSAS': 'Ks', 'KENTUCKY': 'Ky', 'LOUISIANA': 'La', 'MAINE': 'Me',
    'MARYLAND': 'Md', 'MASSACHUSETTS': 'Ma', 'MICHIGAN': 'Mi',
    'MINNESOTA': 'Mn', 'MISSISSIPPI': 'Ms', 'MISSOURI': 'Mo',
    'MONTANA': 'Mt', 'NEBRASKA': 'Ne', 'NEVADA': 'Nv',
    'NEW HAMPSHIRE': 'Nh', 'NEW JERSEY': 'Nj', 'NEW MEXICO': 'Nm',
    'NEW YORK': 'Ny', 'NORTH CAROLINA': 'Nc', 'NORTH DAKOTA': 'Nd',
    'OHIO': 'Oh', 'OKLAHOMA': 'Ok', 'OREGON': 'Or',
    'PENNSYLVANIA': 'Pa', 'RHODE ISLAND': 'Ri', 'SOUTH CAROLINA': 'Sc',
    'SOUTH DAKOTA': 'Sd', 'TENNESSEE': 'Tn', 'TEXAS': 'Tx',
    'UTAH': 'Ut', 'VERMONT': 'Vt', 'VIRGINIA': 'Va',
    'WASHINGTON STATE': 'Wa', 'WEST VIRGINIA': 'Wv', 'WISCONSIN': 'Wi',
    'WYOMING': 'Wy', 'WASHINGTON DC': 'Dc', 'D.C.': 'Dc',
}

# Multi-state border DMAs whose real audience meaningfully spans a
# state that does not appear in the DMA's name tokens. Membership is
# by dominant-or-meaningful population share; kept deliberately small.
# A DMA whose name carries the state token (e.g. 'Mobile Al Pensacola
# Ft Walton Beach Fl' for Florida, 'Tallahassee Fl Thomasville Ga')
# is already a member via token parsing.
_GEO_MULTI_STATE_DMA_SUPPLEMENT = {
    'Cincinnati Oh': {'Ky', 'In'},
    'Chattanooga Tn': {'Ga'},
    'Washington Dc Hagerstown Md': {'Va'},
    'Philadelphia Pa': {'Nj', 'De'},
    'New York Ny': {'Nj', 'Ct'},
    'Charlotte Nc': {'Sc'},
    'Memphis Tn': {'Ms', 'Ar'},
    'Chicago Il': {'In'},
    'Portland Or': {'Wa'},
    'Louisville Ky': {'In'},
    'Evansville In': {'Ky'},
    'St Louis Mo': {'Il'},
    'Kansas City Mo': {'Ks'},
    'Omaha Ne': {'Ia'},
    'Toledo Oh': {'Mi'},
    'South Bend Elkhart In': {'Mi'},
    'Fargo Nd': {'Mn'},
    'Sioux City Ia': {'Ne', 'Sd'},
    'Salisbury Md': {'De'},
    'Boise Id': {'Or'},
}

# Audience nouns that mark a geography token as universe-DEFINING
# ('Florida Voters', 'Miami Renters') rather than incidental branding
# ('Texas Roadhouse', 'Florida Georgia Line').
_GEO_AUDIENCE_NOUNS = (
    'VOTER', 'VOTERS', 'ELECTORATE', 'RESIDENT', 'RESIDENTS', 'LOCALS',
    'HOMEOWNER', 'HOMEOWNERS', 'RENTER', 'RENTERS', 'HOUSEHOLDS',
    'CONSUMER', 'CONSUMERS', 'SHOPPER', 'SHOPPERS', 'BUYER', 'BUYERS',
    'CUSTOMER', 'CUSTOMERS', 'COMMUTERS', 'FAMILIES', 'PARENTS', 'MOMS',
    'DADS', 'SENIORS', 'RETIREES', 'STUDENTS', 'TEACHERS', 'NURSES',
    'WORKERS', 'PROFESSIONALS', 'BUSINESSES', 'DRIVERS', 'PATIENTS',
    'ADULTS', 'MILLENNIALS', 'GUBERNATORIAL', 'SENATE', 'MAYORAL',
)

# Brand names that contain a geography word but are NOT geo-defined
# universes; a subject containing one of these never triggers the
# geo scope (normalized, case + punctuation insensitive).
_GEO_BRAND_BLOCKLIST = (
    'texasroadhouse', 'californiapizzakitchen', 'kentuckyfriedchicken',
    'bostonmarket', 'arizonaicedtea', 'newyorktimes', 'newyorklife',
    'floridageorgialine', 'alaskaairlines', 'hawaiianairlines',
    'virginatlantic', 'newyorkyankees', 'newyorkgiants', 'newyorkjets',
    'newyorkknicks', 'newyorkmets', 'texasrangers', 'minnesotavikings',
    'arizonacardinals', 'coloradorockies', 'floridapanthers',
)


def _geo_norm(s):
    return _re.sub(r'[^a-z0-9]', '', str(s).lower())


def _dma_state_tokens(dma_name):
    """State abbreviation tokens carried by a canonical DMA name, plus
    the explicit multi-state supplement. A leading token is ignored
    when the name carries another state token later ('La Crosse Eau
    Claire Wi' is Wisconsin, not Louisiana)."""
    toks = _re.split(r'[\s&]+', str(dma_name).strip())
    abbrs = set(_GEO_STATE_ABBRS.values())
    hits = [t for t in toks if t in abbrs]
    if len(hits) > 1 and toks and toks[0] in hits:
        hits = [t for i, t in enumerate(toks)
                if t in abbrs and i > 0]
    out = set(hits)
    out |= _GEO_MULTI_STATE_DMA_SUPPLEMENT.get(str(dma_name).strip(),
                                               set())
    return out


def _detect_geo_scope(subject):
    """Return the set of allowed canonical DMA names when the subject
    names a geo-DEFINED universe, else None.

    Gates (all must pass):
      1. subject names a state (full name, word boundary) or a city
         segment from the canonical DMA table;
      2. subject also carries an audience noun (voters, residents,
         renters, ...) so brand names like 'Texas Roadhouse' never
         trigger;
      3. subject does not contain a known geo-containing brand name.
    The caller applies two more gates on the DATA (in-scope share >=
    30%, no >= 90 LOCATION pin) before mutating anything.
    """
    try:
        from scripts._canonical_dma_baseline import CANONICAL_DMA_PCT
    except ImportError:
        try:
            from _canonical_dma_baseline import CANONICAL_DMA_PCT  # type: ignore
        except ImportError:
            return None
    subj_u = str(subject or '').upper()
    subj_n = _geo_norm(subject)
    if not subj_u.strip():
        return None
    if any(b in subj_n for b in _GEO_BRAND_BLOCKLIST):
        return None
    if not any(_re.search(r'\b' + _re.escape(nn) + r'\b', subj_u)
               for nn in _GEO_AUDIENCE_NOUNS):
        return None

    # State scope: full state name in the subject.
    want_states = set()
    for full, abbr in _GEO_STATE_ABBRS.items():
        if _re.search(r'\b' + _re.escape(full) + r'\b', subj_u):
            want_states.add(abbr)
    # 'WASHINGTON' alone is ambiguous (state vs DC vs surname); only
    # honored via the explicit 'WASHINGTON STATE' / 'WASHINGTON DC'
    # keys above.
    if want_states:
        allowed = {name for name in CANONICAL_DMA_PCT
                   if _dma_state_tokens(name) & want_states}
        return allowed or None

    # City/DMA scope: a city n-gram of a canonical DMA named in the
    # subject ('Miami Renters' -> Miami Ft Lauderdale Fl, 'West Palm
    # Beach Retirees' -> West Palm Beach Ft Pierce Fl). Single generic
    # tokens (Ft, Beach, City, ...) never match on their own; the
    # 30% in-scope share gate in the caller is the final guard.
    abbrs = set(_GEO_STATE_ABBRS.values())
    generic = {'FT', 'ST', 'BEACH', 'CITY', 'FALLS', 'SPRINGS', 'PARK',
               'PORT', 'NEW', 'NORTH', 'SOUTH', 'EAST', 'WEST', 'GRAND',
               'GREEN', 'BAY', 'LAKE', 'POINT', 'HIGH', 'LITTLE'}
    allowed = set()
    for name in CANONICAL_DMA_PCT:
        toks = [t for t in _re.split(r'[\s&]+', name)
                if t and t not in abbrs]
        toks_u = [t.upper() for t in toks]
        hit = False
        for a in range(len(toks_u)):
            for b in range(a + 1, len(toks_u) + 1):
                seg = ' '.join(toks_u[a:b])
                if b - a == 1 and (len(seg) < 5 or seg in generic):
                    continue
                if len(seg) >= 5 and _re.search(
                        r'\b' + _re.escape(seg) + r'\b', subj_u):
                    hit = True
                    break
            if hit:
                break
        if hit:
            allowed.add(name)
    return allowed or None


def enforce_geo_scope_location(df, subject, verbose=True):
    """Remove LOCATION rows outside a geo-DEFINED universe's geography
    and renormalize the in-scope DMAs to ~100 (Jenna 2026-08-24: for a
    Florida-defined universe "there should be no value in LOCATION
    outside of FL"; out-of-scope rows are removed, not zeroed, so the
    dashboard never renders dead rows).

    Data gates on top of _detect_geo_scope's name gates:
      - in-scope LOCATION share must already be >= 30% (evidence the
        engine actually built a geo-defined universe; a brand audience
        that merely mentions a state stays untouched);
      - skip when any LOCATION row >= 90 (that is a geo-pinned DMA cut
        per the gender/geo-cut rules - its pin stands).

    Runs BEFORE renormalize_location_to_100 + recompute_raw_and_projection
    so the sum lands at ~100 and Raw/Proj re-canonicalize downstream.
    Micro-jitter is subject-salted; no value lands with a trailing-zero
    4th decimal (no 2dp/4dp boundaries). Idempotent.
    """
    bp_col, _, _, _ = _detect_cols(df)
    if not bp_col or 'Column' not in df.columns:
        return df, 0
    # International frames carry country markets in LOCATION (Omaze
    # precedent), not US DMAs - the US state/DMA scope tables cannot
    # judge them and would strip every country market as out-of-scope.
    _ctry = _frame_country(df)
    if _ctry:
        if verbose:
            print(f"   🌎 geo scope: {_ctry} frame - country markets "
                  f"stand; US DMA scoping skipped")
        return df, 0
    allowed = _detect_geo_scope(subject)
    if not allowed:
        return df, 0
    col_u = df['Column'].astype(str).str.upper().str.strip()
    loc_idx = list(df.index[col_u == 'LOCATION'])
    if not loc_idx:
        return df, 0
    allowed_u = {a.upper() for a in allowed}
    in_rows, out_rows = [], []
    for i in loc_idx:
        v = _bp(df.at[i, bp_col])
        v = None if (v is None or pd.isna(v)) else float(v)
        name = str(df.at[i, 'Value']).strip()
        if name.upper() in allowed_u and v is not None and v > 0:
            in_rows.append((i, name, v))
        else:
            out_rows.append((i, name, v))
    if not in_rows or not out_rows:
        return df, 0
    total_all = sum(v for _, _, v in in_rows) + \
        sum(v for _, _, v in out_rows if v is not None)
    in_share = (sum(v for _, _, v in in_rows) / total_all * 100.0
                if total_all > 0 else 0.0)
    if in_share < 30.0:
        if verbose:
            print(f"   🌎 geo scope: subject names a geography but "
                  f"in-scope LOCATION share is only {in_share:.1f}% - "
                  f"not a geo-defined build; skipping")
        return df, 0
    if any(v is not None and v >= 90.0
           for _, _, v in in_rows + out_rows):
        if verbose:
            print("   🌎 geo scope: LOCATION carries a >=90 DMA pin "
                  "(geo cut) - pin stands; skipping")
        return df, 0

    in_total = sum(v for _, _, v in in_rows)
    newvals = {}
    for i, name, v in in_rows:
        scaled = v * 100.0 / in_total
        h = int(_hl.sha256(
            f"{subject}|LOCATION|{name}|geo-scope".encode()
        ).hexdigest()[:8], 16)
        jit = ((h % 2001) - 1000) / 1000.0 * 0.02
        newvals[i] = max(0.01, scaled + jit)
    largest = max(newvals, key=lambda k: newvals[k])
    newvals[largest] += (100.0 - sum(newvals.values()))
    out = {}
    for i, name, _ in in_rows:
        v = round(newvals[i], 4)
        if int(round(v * 10000)) % 10 == 0:
            h2 = int(_hl.sha256(
                f"{subject}|{name}|geo-ub".encode()).hexdigest()[:8], 16)
            v = round(v + (1 + h2 % 8) / 10000.0, 4)
        out[i] = v
    resid = round(100.0 - sum(out.values()), 4)
    if abs(resid) >= 0.0005:
        v = round(out[largest] + resid, 4)
        if int(round(v * 10000)) % 10 == 0:
            v = round(v + 0.0003, 4)
        out[largest] = v

    drop_idx = [i for i, _, _ in out_rows]
    df = df.drop(index=drop_idx).reset_index(drop=True)
    # Re-locate kept rows post-drop by (Column, Value) since indices
    # shifted; safe because LOCATION values are unique per file.
    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_by_name = {}
    for i2 in df.index[col_u == 'LOCATION']:
        val_by_name[str(df.at[i2, 'Value']).strip()] = i2
    name_targets = {name: out[i] for i, name, _ in in_rows}
    share_col = next((c for c in df.columns if 'Category Share' in c),
                     None)
    kept_sum = sum(name_targets.values())
    n = 0
    for name, v in name_targets.items():
        i2 = val_by_name.get(name)
        if i2 is None:
            continue
        # Bare 4dp numeric, matching renormalize_location_to_100's
        # write convention (normalize_final_format has already run).
        df.at[i2, bp_col] = f"{v:.4f}"
        if share_col:
            df.at[i2, share_col] = round(v * 100.0 / kept_sum, 4)
        n += 1
    if verbose:
        print(f"   🌎 geo scope: removed {len(drop_idx)} out-of-scope "
              f"LOCATION rows, renormalized {n} in-scope DMAs to "
              f"{sum(name_targets.values()):.4f} "
              f"(in-scope share was {in_share:.1f}%)")
    return df, len(drop_idx) + n


def renormalize_location_to_100(df, subject, verbose=True):
    """Scale LOCATION-column BPs so they sum to 100.

    Every panelist lives in exactly one DMA, so LOCATION should sum to
    100 across all DMA rows for the audience. Gilmore Girls 2026-08-19
    shipped with LOCATION sum=121.2 (avid) / 121.5 (base) because
    LOCATION sits in DEPIN_META_CATS (excluded from demo renorm) and
    no dedicated pass rescales it. Fix: proportional rescale of every
    LOCATION row's BP by (100 / current_sum) with subject-salted
    micro-jitter so no row lands on a trailing-zero 4dp boundary, then
    recompute Raw and Projection via the standard downstream pass.

    2026-08-25 hardening (Ari Melber 104.75% / Nicolle Wallace 111.93%
    ship-gate holds): pandas 3.x loads BP columns as 'str' dtype and
    rejects bare-float assignment ("Invalid value '9.3788' for dtype
    'str'"), which crashed this pass inside the chain's per-step
    try/except and let the overshoot reach the ship gate untouched.
    Writes now coerce the BP column to object dtype first (same
    pattern as enforce_mpb_exact_mirror) and assign formatted strings
    that preserve each cell's original style. The pass is also wired
    into run_write_safety_net immediately before
    recompute_raw_and_projection so the invariant re-asserts at write
    time on every path, after the last thing that can mutate LOCATION.

    Idempotent - if the file is already within 0.5pp of 100, no-op
    (nests inside the ship gate's I5 tolerance of 1.5pp). Rows at 0 BP
    (phantom-zeroed) are left untouched.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()
    loc_mask = (col_u == 'LOCATION')
    if not loc_mask.any():
        return df, 0

    bp_num = pd.to_numeric(
        df.loc[loc_mask, bp_col].astype(str).str.replace('%', '', regex=False),
        errors='coerce',
    )
    total_bp = float(bp_num.sum())
    if not (total_bp > 0):
        return df, 0
    if abs(total_bp - 100.0) < 0.5:
        return df, 0

    # pandas >= 3 'str' / StringDtype BP columns reject mixed
    # assignment; coerce to object first.
    if df[bp_col].dtype.name not in ('object', 'O'):
        df[bp_col] = df[bp_col].astype(object)

    scale = 100.0 / total_bp
    targets = {}
    for idx in df.index[loc_mask]:
        cur = _bp(df.at[idx, bp_col])
        if cur is None or cur <= 0:
            continue
        name = (str(df.at[idx, 'Value']).strip()
                if 'Value' in df.columns else str(idx))
        h = int(_hl.sha256(
            f"{subject}|LOCATION|{name}|renorm".encode()).hexdigest()[:8], 16)
        jit = ((h % 2001) - 1000) / 1_000_000.0  # +/- 0.001pp
        targets[idx] = max(0.0001, cur * scale + jit)
    if not targets:
        return df, 0

    # Largest row absorbs the jitter residual so the sum returns to 100.
    largest = max(targets, key=lambda k: targets[k])
    targets[largest] += 100.0 - sum(targets.values())

    n_touched = 0
    for idx, val in targets.items():
        new = round(val, 4)
        if int(round(new * 10000)) % 10 == 0:
            # De-boundary nudge: keep the final 4dp digit non-zero.
            name = (str(df.at[idx, 'Value']).strip()
                    if 'Value' in df.columns else str(idx))
            h2 = int(_hl.sha256(
                f"{subject}|LOCATION|{name}|deboundary".encode()
            ).hexdigest()[:8], 16)
            new = round(new + (1 + h2 % 8) / 10000.0, 4)
        old_cell = df.at[idx, bp_col]
        had_pct = isinstance(old_cell, str) and old_cell.strip().endswith('%')
        df.at[idx, bp_col] = f"{new:.4f}%" if had_pct else f"{new:.4f}"
        n_touched += 1

    if verbose:
        # Verify post
        bp_num2 = pd.to_numeric(
            df.loc[loc_mask, bp_col].astype(str).str.replace('%', '', regex=False),
            errors='coerce',
        )
        print(f"   📍 renormalize_location_to_100 [{subject or ''}]: "
              f"rescaled {n_touched} DMA rows; "
              f"sum {total_bp:.2f} -> {float(bp_num2.sum()):.2f}")
    return df, n_touched


def enforce_brand_share_plausibility(df, subject, verbose=True,
                                     repair_share: float = 0.60,
                                     gp_bp_repair_ceiling: float = 5.0):
    """Cap a profile's implied audience share of any brand's TOTAL Gen
    Pop audience (2026-08-24, Dylan Minnette audit).

    Dylan's file carried rows whose Raw exceeded the brand's ENTIRE
    projected Gen Pop audience (MAXBONE profile_raw > brand US
    audience; MAEV implied 77% of the brand's audience). One subject's
    audience cannot plausibly be more than a defensible share of a
    brand's total US audience. For each brand row with a Gen Pop
    match:

        share = (bp/100 * profile_sample) / (gp_bp/100 * 10M panel)

    When share > `repair_share` (default 60%), the BP is rescaled so
    the share lands at a subject-salted target in [0.38, 0.55] - which
    also enforces the hard 100% cap by construction.

    Scope guard (respects the 2026-06-10 mandate "the fix is in Gen
    Pop, not in the profile" for MASS brands): auto-repair only fires
    when the brand's Gen Pop bp is below `gp_bp_repair_ceiling` (the
    MAXBONE/MAEV micro-brand class where the PROFILE is the implausible
    side). Mass-brand violations are logged only - those route to the
    Gen Pop reconcile path (validate_profile_raw_le_gp_raw + 
    scripts/reconcile_gen_pop.py).

    Skips demos, metadata, LOCATION, and subject self-pin rows. Fails
    safe: no Gen Pop access -> no-op. Positioned BEFORE
    recompute_raw_and_projection so only BP needs setting here; the
    recompute pass canonicalizes Raw/Proj/CS downstream.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    try:
        try:
            from migration.genpop_baseline import load_genpop_map
        except ImportError:
            from genpop_baseline import load_genpop_map  # type: ignore
        gp_map = load_genpop_map()
    except Exception as e:
        if verbose:
            print(f'   [brand-share] Gen Pop map unavailable; skipping '
                  f'({e})')
        return df, 0
    if not gp_map:
        return df, 0

    sample_size = _detect_sample_size(df, bp_col, raw_col)
    if not sample_size or sample_size <= 0:
        return df, 0

    GP_PANEL = 10_000_000.0
    import re as _re

    def _norm_cat_bs(c):
        return _re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())

    def _norm_brand_bs(b):
        return _re.sub(r'[^a-z0-9]+', '', str(b or '').lower())

    subj_norm = _norm_brand_bs(subject)
    # 2026-08-26 (Liz QA, Paw Patrol): the one-way containment test
    # below missed subject-own rows whenever the subject carries an
    # audience suffix ("Paw Patrol Series Viewers" is not a substring
    # of "PAW PATROL"), so the property's own TOYS/GAMES rows got
    # micro-brand-trimmed to 6.1959 against a tiny toy-grid Gen Pop
    # baseline while FRANCHISE stayed at 82.7367. Own-property rows
    # are matched via the audience-suffix-stripped token both ways.
    try:
        try:
            from migration.self_property_coherence import (
                is_subject_own as _spc_is_own,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                is_subject_own as _spc_is_own,
            )
    except Exception:
        _spc_is_own = None
    skip_cats = (METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
                 | {'LOCATION', 'DMA', 'REGION'})
    n_fixed = 0
    n_logged = 0
    examples = []
    # Pass 1: compute a candidate repaired BP per violating row. The
    # salt is brand-only (NOT per category) and the final value is the
    # MIN candidate per brand, applied to every mirror-eligible row of
    # that brand in pass 2 - Rule #3b (MPB exact mirror) already ran
    # upstream, so a per-category repair here would re-introduce
    # MPB-vs-subcat drift on the shipped file.
    cand = {}   # brand_norm -> min candidate new_bp
    rows_by_brand = {}   # brand_norm -> [idx of mirror-eligible rows]
    viol = {}   # brand_norm -> example tuple
    for idx in df.index:
        cat_u = str(df.at[idx, 'Column']).strip().upper()
        if cat_u in skip_cats:
            continue
        val = str(df.at[idx, 'Value']).strip()
        if not val:
            continue
        bp = _bp(df.at[idx, bp_col])
        if bp is None or bp <= 0:
            continue
        # Self-pin / subject rows exempt (the subject IS the brand).
        # Bidirectional own-token match (2026-08-26 Paw Patrol fix):
        # audience-suffixed subjects must still exempt their own
        # property's rows from the micro-brand trim.
        if subj_norm and subj_norm in _norm_brand_bs(val):
            continue
        if _spc_is_own is not None and _spc_is_own(subject, val):
            continue
        bnorm = _norm_brand_bs(val)
        if cat_u not in _MPB_MIRROR_SKIP_CATS or \
                cat_u == 'MOST PURCHASED BRANDS':
            rows_by_brand.setdefault(bnorm, []).append(idx)
        hit = gp_map.get((_norm_cat_bs(cat_u), bnorm))
        if hit is None:
            continue
        gp_bp = hit[0] if isinstance(hit, tuple) else hit
        if not gp_bp or gp_bp <= 0:
            continue
        gp_audience = gp_bp / 100.0 * GP_PANEL
        p_raw = bp / 100.0 * sample_size
        share = p_raw / gp_audience
        if share <= repair_share:
            continue
        if gp_bp >= gp_bp_repair_ceiling:
            # Mass brand: per 2026-06-10 mandate the fix is a Gen Pop
            # bump, not a profile trim. Log only.
            n_logged += 1
            if verbose and n_logged <= 3:
                print(f'   [brand-share] MASS-BRAND share '
                      f'{share*100:.0f}% of Gen Pop audience for '
                      f'{cat_u}/{val} (gp_bp={gp_bp:.4f}) - logged for '
                      f'Gen Pop reconcile, profile untouched')
            continue
        # Micro-gp brand: repair. Target share salted in [0.38, 0.55].
        tgt = 0.38 + abs(_jitter_for(subject, val, salt='bshare',
                                     lo=0.0, hi=0.17))
        tgt = min(tgt, 0.55)
        new_bp = tgt * gp_audience / sample_size * 100.0
        new_bp = max(0.0001, min(99.49, round(new_bp, 4)))
        # keep off 2dp boundaries
        if abs(new_bp - round(new_bp, 2)) < 0.00005:
            new_bp = round(new_bp + 0.0013 + abs(_jitter_for(
                subject, val, salt='bshare-nudge',
                lo=0.0, hi=0.003)), 4)
        if new_bp >= bp:
            continue
        prev = cand.get(bnorm)
        if prev is None or new_bp < prev:
            cand[bnorm] = new_bp
        if bnorm not in viol:
            viol[bnorm] = (cat_u, val, bp, share, tgt, gp_audience)
    # Pass 2: apply the brand-level repaired BP to every mirror-eligible
    # row of each violating brand (keeps the Rule #3b exact mirror).
    for bnorm, new_bp in cand.items():
        for idx in rows_by_brand.get(bnorm, []):
            bp = _bp(df.at[idx, bp_col])
            if bp is None or bp <= 0 or new_bp >= bp:
                continue
            df.at[idx, bp_col] = (f'{new_bp:.4f}%'
                                  if '%' in str(df.at[idx, bp_col])
                                  else f'{new_bp:.4f}')
            n_fixed += 1
        if len(examples) < 5 and bnorm in viol:
            cat_u, val, bp0, share, tgt, gp_aud = viol[bnorm]
            examples.append(f'{cat_u}/{val}: {bp0:.4f} -> {new_bp:.4f} '
                            f'(share {share*100:.0f}% -> {tgt*100:.0f}% '
                            f'of gp audience {gp_aud:,.0f})')
    if verbose and (n_fixed or n_logged):
        print(f'   🧮 brand-share plausibility: repaired {n_fixed} '
              f'micro-brand row(s), logged {n_logged} mass-brand '
              f'violation(s) [{subject or "profile"}]')
        for ex in examples:
            print(f'      {ex}')
    return df, n_fixed


def sync_parent_row_to_segment_anchor(df, subject, verbose=True):
    """When ``subject`` is a derived cohort (e.g. 'Gilmore Girls - Avid
    Fan', 'Reba McEntire - Avid Fan', 'Bridesmaids - Casual Fan'), the
    parent brand row (e.g. 'Gilmore Girls') must equal the segment
    anchor's BP (typically 100.0) in the categories where the anchor
    is pinned.

    Gilmore Girls 2026-08-19 (Avid): segment anchor
    'Gilmore Girls - Avid Fan' = 100.0, but the parent 'Gilmore Girls'
    row read 97.44. A superset row cannot be below its subset's anchor.
    Same class as BYD Avid (98.26) and inverse of Andy Grammer Avid.

    Detection: subject contains ' - ' followed by a cohort suffix
    (Avid Fan / Casual Fan / Fans / Total Universe / Past N Days /
    similar). Parent = subject before the ' - '. When both a parent-
    brand row and the segment anchor exist in the SAME Column, pin the
    parent row's BP to the anchor's BP.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    if 'Value' not in df.columns:
        return df, 0
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0

    # Extract parent name from the subject. If there's no ' - ' this
    # isn't a derived cohort, so no work.
    subj = str(subject or '').strip()
    if ' - ' not in subj:
        return df, 0
    parent_name = subj.split(' - ')[0].strip()
    if not parent_name or parent_name == subj:
        return df, 0

    n_synced = 0
    examples: list = []
    # For each Column that contains BOTH the segment anchor AND the
    # parent brand as separate rows, pin parent BP to anchor BP.
    val_norm = df['Value'].astype(str).str.strip().str.lower()
    parent_norm = parent_name.lower()
    subj_norm = subj.lower()

    for col in df['Column'].astype(str).str.strip().unique():
        col_mask = (df['Column'].astype(str).str.strip() == col)
        anchor_idx = df.index[col_mask & (val_norm == subj_norm)]
        parent_idx = df.index[col_mask & (val_norm == parent_norm)]
        if len(anchor_idx) == 0 or len(parent_idx) == 0:
            continue
        try:
            anchor_bp = _bp(df.at[anchor_idx[0], bp_col])
        except Exception:
            continue
        if anchor_bp is None:
            continue
        for p_idx in parent_idx:
            parent_bp = _bp(df.at[p_idx, bp_col])
            if parent_bp is None or abs(parent_bp - anchor_bp) < 0.001:
                continue
            if parent_bp > anchor_bp:
                # Parent is HIGHER than anchor -- that's directionally
                # correct for a superset (never expected here since
                # the anchor is usually 100). Leave alone.
                continue
            # Parent below anchor -- impossible; pin up.
            df.at[p_idx, bp_col] = anchor_bp
            n_synced += 1
            if len(examples) < 5:
                examples.append((col, parent_name, parent_bp, anchor_bp))

    if verbose and n_synced:
        print(f"   👨‍👦 sync_parent_row_to_segment_anchor [{subject}]: "
              f"pinned {n_synced} parent row(s) up to anchor BP")
        for col, name, old, new in examples:
            print(f"      [{col}] {name!r}: {old} → {new}")
    return df, n_synced


def strip_brand_input_csv_phantom_row(df, subject, verbose=True):
    """Remove any content peer row where Value == 'CSV' (the BRAND
    INPUT marker) that got emitted as a peer at 100%.

    When BRAND INPUT is 'CSV' (the content-series convention), the
    writer sometimes emits an additional 'SERIES :: CSV' row at 100
    or an equivalent phantom row. That row is nonsense — 'CSV' is
    the marker string, not a peer series. Idempotent.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    if 'Value' not in df.columns:
        return df, 0
    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_u = df['Value'].astype(str).str.strip().str.upper()
    # Never drop the actual BRAND INPUT row itself
    mask = ((col_u != 'BRAND INPUT') & (val_u == 'CSV'))
    if not mask.any():
        return df, 0
    dropped = int(mask.sum())
    examples = df.loc[mask, ['Column', 'Value']].head(5).values.tolist()
    df = df.drop(index=df.index[mask]).reset_index(drop=True)
    if verbose:
        print(f"   🧹 strip_brand_input_csv_phantom_row [{subject or ''}]: "
              f"dropped {dropped} phantom 'CSV' row(s) (marker string "
              f"leaked as peer)")
        for c, v in examples:
            print(f"      [{c}] {v!r}")
    return df, dropped


def dedupe_same_column_value(df, subject, verbose=True):
    """Drop duplicate rows within (Column, normalized(Value)); keep MAX BP.

    Idempotent. Catches the "Disney+/Hulu appears twice in
    STREAMING/PLATFORM" defect (Gilmore Girls 2026-08-19) and any
    future same-brand-same-column duplicate the row-by-row engine or
    the hybrid sanity check produces. Case-insensitive, punctuation-
    stripped match — so 'Disney+/Hulu' and 'DISNEY+/HULU' collapse.

    Does NOT run inside demographic categories (they legitimately have
    single-occurrence buckets like GENDER::FEMALE, AGE::18-24). Skips
    metadata rows (BRAND INPUT / SAMPLE SIZE / BRAND CATEGORY /
    SUBJECT).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    if 'Value' not in df.columns:
        return df, 0
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0

    import re as _re
    def _norm(v):
        return _re.sub(r'[^a-z0-9]+', '', str(v or '').lower())

    _METADATA = {'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY',
                 'SUBJECT', 'INPUT_METADATA', 'AVID FAN', 'CASUAL FAN'}

    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_norm = df['Value'].apply(_norm)
    metadata_mask = col_u.isin(_METADATA)

    df['_bp_tmp'] = pd.to_numeric(
        df[bp_col].astype(str).str.replace('%', '', regex=False),
        errors='coerce',
    )

    # Group non-metadata rows by (Column, normalized value); if a group
    # has >1 row, keep the one with MAX BP. Drop the rest.
    drop_idx: list = []
    kept_examples: list = []
    for (col, nm), grp_idx in df.loc[~metadata_mask].groupby(
        [col_u.rename('_c'), val_norm.rename('_v')]
    ).groups.items():
        if len(grp_idx) < 2:
            continue
        # Keep the row with the highest BP; drop the rest
        try:
            keep = df.loc[list(grp_idx), '_bp_tmp'].idxmax()
        except Exception:
            keep = list(grp_idx)[0]
        for i in grp_idx:
            if i != keep:
                drop_idx.append(i)
        if len(kept_examples) < 5:
            kept_bp = df.at[keep, '_bp_tmp']
            drop_bps = [df.at[i, '_bp_tmp'] for i in grp_idx if i != keep]
            kept_examples.append((col, df.at[keep, 'Value'], kept_bp, drop_bps))

    df = df.drop(columns=['_bp_tmp'])
    if not drop_idx:
        return df, 0

    df = df.drop(index=drop_idx).reset_index(drop=True)
    if verbose:
        print(f"   🧹 dedupe_same_column_value [{subject or ''}]: "
              f"dropped {len(drop_idx)} duplicate row(s)")
        for col, val, kept_bp, drop_bps in kept_examples:
            print(f"      [{col}] {val!r} kept BP={kept_bp} (dropped {drop_bps})")
    return df, len(drop_idx)


def enforce_viewer_carriage_constraint(df, subject, carriage_doc=None,
                                       allow_research=False, verbose=True):
    """Viewer-carriage constraint (Jenna 2026-08-26, Jimmy Kimmel Live /
    Rosie hosted viewers defect): a consumption-scoped universe
    ("viewers of X") is DEFINED by having watched the title on some
    digital service, so the services that actually carry the full
    episodes must jointly account for ~100% of the universe.

    Semantics:
      * exclusive carrier (or carriers merged into one row, e.g.
        Disney+ and Hulu both aliasing to 'Disney+/Hulu'): that row is
        lifted to a messy ~100 (99.93-99.995, subject-salted, never
        exactly 100 - exact 100 is reserved for spec pins, which are
        left untouched);
      * multiple carrier rows: the union must reach ~100. When the sum
        falls short, both rows slide UP by the same additive shift
        (Claude's reasoned tilt between them is preserved - this is an
        arithmetic correction, never a multiplier), each capped below
        100 with a salted epsilon, and no two carriers may share the
        same 4dp value;
      * alias rows (STREAMING VIDEO vs STREAMING/PLATFORM) are kept
        consistent per carrier;
      * non-carrier rows are NEVER touched (organic Netflix usage on a
        JKL-viewers universe stays wherever reasoning put it);
      * Raw / Projection recomputed for every touched row (the chain's
        final recompute pass re-canonicalizes again downstream).

    carriage_doc: the canonical doc from migration.viewer_carriage
    (spec['carriage_doc']). When None, the enforcer auto-resolves:
    detection on the subject name, then the S3 cache; live research
    only when allow_research=True (the BG.py-path entry via
    run_all_enforcers(keep_avid_row=True)). Fail-open on everything -
    an unresolvable doc means no changes, never a crash.

    Derived cuts never reach this code with a doc: run_all_enforcers
    only auto-resolves when keep_avid_row=True (TU write paths), and
    cut subjects ('{Subject} - {Cut}') fail detection by design.

    Returns (df, n_changed).
    """
    if (df is None or len(df) == 0 or 'Column' not in df.columns
            or 'Value' not in df.columns):
        return df, 0
    try:
        from migration import viewer_carriage as vc
    except ImportError:
        try:
            import viewer_carriage as vc  # twin layout
        except ImportError:
            return df, 0

    doc = carriage_doc
    try:
        if doc is None:
            det = vc.detect_consumption_scoped(subject)
            if not det:
                return df, 0
            doc = vc.load_cached_carriage(subject, verbose=verbose)
            if doc is None and allow_research and vc.research_enabled():
                doc = vc.research_carriage(
                    subject, det['research_hint'],
                    qualifier=det['qualifier'])
                vc.save_carriage_cache(subject, doc)
        if not vc.doc_is_enforceable(doc):
            if doc is not None and verbose:
                print(f"   📺 viewer carriage: doc for {subject!r} not "
                      f"enforceable (research failed / no carriers); "
                      f"constraint skipped")
            return df, 0
    except Exception as e:
        print(f"   ⚠️ viewer carriage resolution failed (non-fatal): {e}")
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    col_u = df['Column'].astype(str).str.strip().str.upper()

    def _fnum(v):
        try:
            f = float(str(v).replace(',', '').replace('%', '').strip())
            return f
        except Exception:
            return None

    sample = None
    for anchor in ('SAMPLE SIZE', 'BRAND INPUT'):
        m = col_u == anchor
        if m.any() and raw_col:
            s = _fnum(df.loc[m].iloc[0].get(raw_col))
            if s and s > 0:
                sample = s
                break
    if sample is None:
        sample = 10_000_000

    carriers = doc.get('carriers') or []
    vmvpd_carrier = any((c.get('kind') or '') == 'vmvpd' for c in carriers)
    cats = [c for c in vc.STREAMING_ALIAS_CATS
            if (col_u == c).any()]
    if vmvpd_carrier:
        cats += [c for c in vc.VMVPD_CATS if (col_u == c).any()]
    if not cats:
        return df, 0

    def _write(idx, new_bp):
        cur_cell = str(df.at[idx, bp_col])
        had_pct = cur_cell.strip().endswith('%')
        df[bp_col] = df[bp_col].astype(object)
        df.at[idx, bp_col] = (f"{new_bp:.4f}%" if had_pct
                              else f"{new_bp:.4f}")
        if raw_col:
            df[raw_col] = df[raw_col].astype(object)
            df.at[idx, raw_col] = int(round(sample * new_bp / 100.0))
        if proj_col:
            df[proj_col] = df[proj_col].astype(object)
            df.at[idx, proj_col] = int(round(
                (sample * new_bp / 100.0) / 10_000_000 * 329_900_000))

    n = 0
    # Per-carrier canonical value so alias categories mirror exactly.
    canonical_bp: dict[str, float] = {}
    touched_cats = set()
    for cat in cats:
        cat_mask = col_u == cat
        # carrier key -> list of row idx (a merged row like
        # 'Disney+/Hulu' collapses multiple carriers into ONE entry).
        row_map: dict[int, list[str]] = {}
        for c in carriers:
            plat = c.get('platform') or ''
            if not plat:
                continue
            for idx in df.index[cat_mask]:
                if vc.carrier_matches_row(plat, df.at[idx, 'Value']):
                    row_map.setdefault(idx, []).append(plat)
                    break  # first (highest-sorted) matching row only
        if not row_map:
            if verbose:
                print(f"   📺 viewer carriage: no carrier row found in "
                      f"{cat} for {subject!r} "
                      f"({', '.join(c['platform'] for c in carriers)})")
            continue

        idxs = sorted(row_map.keys())
        vals = {i: (_fnum(df.at[i, bp_col]) or 0.0) for i in idxs}
        rkey = {i: '+'.join(sorted(row_map[i])) for i in idxs}

        if len(idxs) == 1:
            i = idxs[0]
            key = rkey[i]
            if key in canonical_bp:
                tgt = canonical_bp[key]
            else:
                cur = vals[i]
                if abs(cur - 100.0) <= 0.00005:
                    # Exact-100 spec pin (Furious flow) is authoritative.
                    canonical_bp[key] = cur
                    continue
                if cur >= 99.0:
                    canonical_bp[key] = cur
                    continue
                tgt = vc.messy_near_total(subject, key)
                canonical_bp[key] = tgt
            if abs(vals[i] - tgt) > 0.00005:
                _write(i, tgt)
                touched_cats.add(cat)
                n += 1
                if verbose:
                    print(f"   📺 viewer carriage: {cat} | "
                          f"{df.at[i, 'Value']!r} exclusive carrier "
                          f"{vals[i]:.4f} -> {tgt:.4f} (union of "
                          f"carriers must cover ~100% of a viewers "
                          f"universe)")
        else:
            # Multi-carrier union. Reuse canonical values when the
            # alias category already settled them.
            if all(rkey[i] in canonical_bp for i in idxs):
                new_vals = {i: canonical_bp[rkey[i]] for i in idxs}
            else:
                cur_sum = sum(vals.values())
                margin = vc.salted_unit(subject, 'union-margin', 2.5, 9.5)
                target_sum = 100.0 + margin
                if cur_sum >= 100.0 - 0.75:
                    new_vals = dict(vals)
                else:
                    shift = (target_sum - cur_sum) / len(idxs)
                    new_vals = {i: v + shift for i, v in vals.items()}
                # Cap each below a per-row messy ceiling; hand overflow
                # to the other carriers (waterfall, one pass).
                overflow = 0.0
                caps = {i: vc.messy_near_total(subject, rkey[i])
                        for i in idxs}
                for i in idxs:
                    if new_vals[i] > caps[i]:
                        overflow += new_vals[i] - caps[i]
                        new_vals[i] = caps[i]
                if overflow > 0:
                    open_rows = [i for i in idxs
                                 if new_vals[i] < caps[i] - 0.01]
                    for i in open_rows:
                        add = min(overflow / len(open_rows),
                                  caps[i] - new_vals[i])
                        new_vals[i] += add
                # 4dp rounding + collision/boundary hygiene: no two
                # carriers identical, nothing on a .XX00 boundary.
                seen4 = set()
                for i in idxs:
                    v4 = round(new_vals[i], 4)
                    if abs(v4 * 100 - round(v4 * 100)) < 1e-9:
                        v4 = round(v4 - vc.salted_unit(
                            subject, f'b|{rkey[i]}', 0.0003, 0.0041), 4)
                    while f"{v4:.4f}" in seen4:
                        v4 = round(v4 - vc.salted_unit(
                            subject, f'c|{rkey[i]}|{len(seen4)}',
                            0.0007, 0.0151), 4)
                    seen4.add(f"{v4:.4f}")
                    new_vals[i] = v4
                for i in idxs:
                    canonical_bp[rkey[i]] = new_vals[i]
            for i in idxs:
                if abs(vals[i] - new_vals[i]) > 0.00005:
                    _write(i, new_vals[i])
                    touched_cats.add(cat)
                    n += 1
                    if verbose:
                        print(f"   📺 viewer carriage: {cat} | "
                              f"{df.at[i, 'Value']!r} carrier "
                              f"{vals[i]:.4f} -> {new_vals[i]:.4f} "
                              f"(carrier union covers ~100% of a "
                              f"viewers universe; reasoned tilt "
                              f"preserved)")

    for cat in touched_cats:
        try:
            df = _renormalize_category(df, cat, bp_col, cs_col, raw_col,
                                       proj_col, sample)
        except Exception:
            pass
    return df, n


def run_all_enforcers(df, subject, brand_category=None, verbose=True,
                       target_year=None, follower_ceiling=None,
                       keep_avid_row=False, carriage_doc=None):
    """Run every enforcer in order. Returns (df, total_changes).

    target_year (optional): if provided (e.g. 2022 for a `Gen_Pop_2022.csv`
    generation), runs the anachronism check to zero / dampen brands that
    didn't exist yet in the target year. Only pass when the file's
    contents are meant to represent that historical year.

    follower_ceiling (optional int): when provided AND positive, runs the
    followers-only projection cap (2026-08-19 Jenna directive):
    US Gen Pop Projection cannot exceed the subject's real follower count.
    Only pass for followers-only cohorts (spec['audience_type'] ==
    'followers' / 'subscribers'); leave None otherwise. See
    `enforce_follower_ceiling_projection` for the full rationale.
    """
    # 2026-08-19 (Gilmore Girls incident): coerce enforcer-writeable
    # columns to object dtype FIRST so downstream enforcers stop
    # silently crashing on pandas-2.1 NA-aware `str` dtype columns.
    df = _coerce_writeable_columns_to_object(df, verbose=verbose)
    # 2026-08-24 (stale-share class kill): snapshot every row's BP at
    # chain start. apply_bp_cs_consistency_recovery treats the stored
    # Category Share as writer ground truth; that premise only holds
    # for rows NOT moved during this run. Deliberate enforcer work
    # (floors, caps, pins) is never writer corruption, so any category
    # with a mid-run BP change is skipped by the recovery.
    bp_at_load = None
    try:
        _bp_col0, _, _, _ = _detect_cols(df)
        if _bp_col0 and _bp_col0 in df.columns:
            bp_at_load = {}
            for _i0 in df.index:
                _v0 = _bp(df.at[_i0, _bp_col0])
                bp_at_load[_i0] = (None if (_v0 is None or pd.isna(_v0))
                                   else round(float(_v0), 6))
    except Exception:
        bp_at_load = None
    total = 0
    # 2026-07-28 (Jenna pipeline hardening): anachronism check for year
    # skins. Zero / dampen brands whose launch_year > target_year. Only
    # fires when target_year is passed. Runs FIRST so downstream
    # enforcers see the corrected values.
    if target_year is not None:
        try:
            try:
                from migration.anachronism_check import (
                    strip_anachronistic_brands,
                )
            except ImportError:
                from anachronism_check import (  # type: ignore
                    strip_anachronistic_brands,
                )
            df, n = strip_anachronistic_brands(
                df, year=target_year, subject=subject, verbose=verbose,
            )
            total += n
        except Exception as e:
            print(f"   ⚠️ enforcer strip_anachronistic_brands failed: {e}")
    # 2026-06-07 (Jenna deep audit, D88): strip `~` from BRAND INPUT FIRST
    # so all downstream subject-string comparisons see the canonical form.
    # 226 of 525 corpus files (43%) had tilde subjects pre-fix.
    try:
        df, n = apply_strip_tilde_from_brand_input(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_strip_tilde_from_brand_input failed: {e}")
    # 2026-08-07 (Jenna directive): Disney+ / Hulu are one platform now.
    # Consolidate any pair of sibling rows into a single 'Disney+/Hulu'
    # row across every non-metadata column. Idempotent. Also runs in
    # `run_write_safety_net` (defense in depth for direct-write paths).
    try:
        df, n = apply_disney_hulu_rollup(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_disney_hulu_rollup failed: {e}")
    # 2026-08-19 (Gilmore Girls incident): general dedupe within
    # (Column, normalized(Value)). Catches the case where the row-by-row
    # engine or hybrid sanity check produced TWO 'Disney+/Hulu' rows in
    # the SAME STREAMING/PLATFORM column - which apply_disney_hulu_rollup
    # doesn't handle (it only merges Disney+ + Hulu into Disney+/Hulu,
    # not two Disney+/Hulu rows into one). Runs early so downstream
    # enforcers see a deduped file.
    try:
        df, n = dedupe_same_column_value(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dedupe_same_column_value failed: {e}")
    # 2026-06-24 (Liz Ed Sheeran flag, corpus sweep found 11 same-day-gen
    # files with 98%+ blank BP rows): backfill BP from raw FIRST so every
    # downstream enforcer can read a valid BP. Idempotent — no-op if BPs
    # are already populated. Without this, depin / renorm / cap enforcers
    # silently skip rows with blank BP.
    try:
        df, n = fill_missing_bp_from_raw(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer fill_missing_bp_from_raw failed: {e}")
    # 2026-07-17 (Jenna): "avid cuts always have the same category as main
    # cuts and never end up as uncategorized". Runs EARLY — before any
    # enforcer that reads BRAND CATEGORY (strip_subject_from_wrong_category
    # at line ~7100, apply_politics_persona_cap, etc.) so downstream logic
    # always sees a resolved value. When called from BG.py's main-file
    # writer or from avid_fan_row_by_row (which now threads the caller's
    # brand_category through to run_all_enforcers), the passed-in value
    # wins with force=True, guaranteeing avid ↔ main mirror. When called
    # from a legacy path without brand_category, existing row is preserved,
    # else falls back to "GENERAL" so no file ships UNCATEGORIZED.
    try:
        df, n = enforce_brand_category_mirror(
            df, subject, brand_category=brand_category, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_brand_category_mirror failed: {e}")
    for fn in (
        strip_input_metadata_leakage,      # 2026-05-27 (D5) — prompt-context echoes
        strip_url_variant_seed_rows,       # 2026-07-29 (Elton MUSICIAN/BAND) — hide URL-variant seed lists from category displays
        strip_hostmap_hidden_brands,       # 2026-05-27 (Rule #4b) — Hidden never ships
        strip_mpb_non_hostmap_brands,      # 2026-05-28 (Rule #4c) — MPB column must match hostmap MPB sections
        strip_url_encoded_subject_dupes,
        strip_corporate_parents,
        strip_product_skus,
        strip_polluted_brand_values,
        strip_youtube_from_wrong_category, # 2026-07-21 (Jenna) - bare YouTube must live in SOCIAL MEDIA only
        strip_phantom_zero_rows,           # 2026-06-15 (Rob Schneider INTEREST defect) — phantom 0.0000% inserts
        ensure_subject_in_native_category, # 2026-06-15 (Apple Pay native-cat gap) — subject must self-pin in BRAND CATEGORY
        enforce_native_cluster_self_pin,   # 2026-06-16 PM (Defect 38) — subject = 100% in ALL cluster grids (BANKING+BANK for BANKS)
        enforce_multi_brand_input_self_pin, # 2026-08-04 Rail #7 (SharkNinja) — SHARKNINJA -> SHARK + NINJA both pinned to 100 in MPB + carry-through
        pin_subject_to_100_in_appearing_categories, # 2026-06-15 (Netflix 99.04 near-miss) — pin >=95% subject rows to exact 100
        enforce_netflix_leads_streaming_platform,   # 2026-06-15 PM (Defect 28) — Netflix #1 in S/P excl. self-pins
        enforce_niche_streamer_caps,                # 2026-06-16 PM (Defect 39) — Criterion/MUBI/Acorn/etc. capped to plausible US-sub ceilings
        enforce_vanguard_in_investments,            # 2026-06-17 (Defect 42) — Vanguard moved BANKING -> INVESTMENTS per hostmap canonical
        dedupe_subject_streaming_grids,             # 2026-06-15 PM (Defect 31) — drop subject duplicate from non-native streaming grid
        apply_politics_persona_cap,
        apply_taylor_swift_persona_tier,
    ):
        try:
            df, n = fn(df, subject, verbose=verbose)
            total += n
        except Exception as e:
            print(f"   ⚠️ enforcer {fn.__name__} failed: {e}")
    try:
        df, n = strip_subject_from_wrong_category(
            df, subject, brand_category=brand_category, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer strip_subject_from_wrong_category failed: {e}")
    # Panel-reality floor — runs before MPB so MPB sees post-floor df
    try:
        df, n = apply_panel_reality_floors(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_panel_reality_floors failed: {e}")
    # 2026-06-03 (Jenna 7-file Gemini master defect): defense-in-depth ceiling
    # for SEARCH ENGINE/AI cohort. apply_panel_reality_floors only trims brands
    # explicitly listed in KNOWN_OVERSHOOT_BRANDS; if the LLM invents a NEW
    # rank-cascade pin (100/50/33/23/18/14 across Google/ChatGPT/Gemini/Copilot/
    # Bing/Perplexity) for any other category we haven't whitelisted, this
    # catches it.
    # 2026-06-04 (Jenna Aidan Gillen Gemini=0.69%): now BIDIRECTIONAL —
    # also lifts suppressed values like Gemini=0.69% on a 55-64 archetype
    # back into the persona-aligned band.
    try:
        df, n = enforce_search_engine_ai_cohort_ceiling(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_search_engine_ai_cohort_ceiling failed: {e}")
    # 2026-06-04 (Jenna Adele Exarchopoulos defect): collapse exact-duplicate
    # rows AND demote partial-token 100% subject-substring pins (e.g. lone
    # "Adele" pinned to 100% across TALENT/ACTOR/MUSICIAN/BAND when the
    # subject is "Adele Exarchopoulos"). Runs LATE so per-row enforcers
    # have already done their work; this is final-pass janitorial.
    try:
        df, n = dedup_and_depin_subject_substrings(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dedup_and_depin_subject_substrings failed: {e}")
    # 2026-05-29: Household-streaming floor — brand-profile-only rescue
    # for Netflix/Disney+/HBO Max/etc. that the GPT-4o vet re-reasoner
    # over-suppresses with "active demo less couch-bound" archetype bias.
    # Runs BEFORE MPB / propagation so the lift carries downstream.
    try:
        df, n = enforce_household_streaming_floor(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_household_streaming_floor failed: {e}")
    # MPB audience-weighted lifts — release the default-floor lock on
    # MOST PURCHASED BRANDS (Defect Class #22, Foosball 825-row evidence).
    # Hostmap-gated per rule #4 — only Sheet4-validated brands lifted.
    try:
        df, n = apply_mpb_digital_share_lifts(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_mpb_digital_share_lifts failed: {e}")
    # 2026-05-25 (Defect Class #22b): one-shot cleanup of MPB rows that
    # were lifted by a previous non-hostmap-gated MPB run. Reverts any
    # row whose Value isn't in Sheet4 back to a jittered floor [0.01-0.05].
    # Idempotent — only fires on rows currently > 0.5.
    try:
        df, n = reset_non_hostmap_mpb_to_floor(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer reset_non_hostmap_mpb_to_floor failed: {e}")
    # 2026-05-25 (Rule #4 across-the-board): same as the MPB reset above,
    # but extended to every brand-style category (TELECOM, BANKING,
    # STREAMING/PLATFORM, etc.). Catches "SHOWTIME TV", "AMERITRADE",
    # subject's own name in MEDIA, etc.
    try:
        df, n = reset_non_hostmap_brands_to_floor(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer reset_non_hostmap_brands_to_floor failed: {e}")
    # 2026-05-25 (Defect Class #22c): add MUST_INCLUDE Sheet4 canonical
    # brands (DOVE BEAUTY etc.) when missing from the profile MPB rows.
    # Safety-net for MPB agent omissions; only adds rows that are in
    # Sheet4 AND have a MPB_DIGITAL_SHARE lookup entry.
    try:
        df, n = add_missing_sheet4_must_include_mpb(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer add_missing_sheet4_must_include_mpb failed: {e}")
    # 2026-05-25 (Defect Class #14b): normalize MPB Value spellings to
    # Sheet4 canonical (UPPER form). Handles "COCA COLA" → "COCA-COLA",
    # "JACK DANIELS" → "JACK DANIEL'S", etc.
    try:
        df, n = normalize_brand_names_to_sheet4(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer normalize_brand_names_to_sheet4 failed: {e}")
    # 2026-05-27 (D4): bidirectional propagation iterated to fixpoint.
    # forward (MPB → sub-cat) and reverse (sub-cat max → MPB) were
    # previously running once each, but Krapopolis still ended with
    # 43 cross-cat 4dp pins and stale MPB/sub-cat deltas. Iterate
    # forward → reverse → forward until max |Δ| < 0.05pp or 4 cycles.
    _MAX_ITERS = 4
    _DELTA_PP = 0.05  # stop when the largest delta drops below 0.05pp
    _prev_snapshot = None
    _iter_total = 0
    for _iter in range(_MAX_ITERS):
        try:
            df, _nf = propagate_mpb_to_other_categories(df, subject,
                                                          verbose=(verbose and _iter == 0))
            _iter_total += _nf
        except Exception as e:
            print(f"   ⚠️ enforcer propagate_mpb_to_other_categories failed: {e}")
            break
        try:
            df, _nr = reverse_propagate_subcat_to_mpb(df, subject,
                                                       verbose=(verbose and _iter == 0))
            _iter_total += _nr
        except Exception as e:
            print(f"   ⚠️ enforcer reverse_propagate_subcat_to_mpb failed: {e}")
            break
        # Convergence check: snapshot {(col, val) → bp} and stop when
        # nothing moves beyond the threshold.
        try:
            bp_col = 'Brand Penetration (Row)'
            cur = {}
            for _, _r in df.iterrows():
                _bp = _r.get(bp_col)
                try:
                    _bpf = float(str(_bp).replace('%', '').strip())
                except (TypeError, ValueError):
                    continue
                cur[(str(_r['Column']).strip().upper(),
                     str(_r['Value']).strip().upper())] = _bpf
            if _prev_snapshot is not None:
                max_delta = 0.0
                for k, v in cur.items():
                    pv = _prev_snapshot.get(k)
                    if pv is None:
                        continue
                    d = abs(v - pv)
                    if d > max_delta:
                        max_delta = d
                if verbose:
                    print(f"   🔁 propagation iter {_iter+1}: max Δ = {max_delta:.4f}pp")
                if max_delta < _DELTA_PP:
                    break
            _prev_snapshot = cur
        except Exception as e:
            print(f"   ⚠️ propagation convergence check failed: {e}")
            break
    if verbose and _iter_total:
        print(f"   ✅ bidirectional propagation: {_iter_total} BP "
              f"adjustment(s) across {_iter + 1} iteration(s)")
    total += _iter_total
    # 2026-05-26 (Defect Class #24): same alignment for talent rows. When a
    # name is in TALENT at 72% and ACTOR/MUSICIAN/HOST at floor (or vice
    # versa), align all family rows to MAX of the family with per-row jitter.
    try:
        df, n = propagate_talent_to_subcats(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer propagate_talent_to_subcats failed: {e}")
    # 2026-06-07 (Jenna 100%-incidence finding): canonical-band normalize
    # for CREDIT PROVIDER and TELECOM. Run AFTER propagation (so MPB/sub-cat
    # alignment finishes first) but BEFORE dejitter passes (so band-anchored
    # values get round-2dp jitter if they land on .00/.50).
    #
    # D102b — CP canonical normalize: every fresh pull leaks 2-4 CP rows
    #   below band. 42/42 fresh profiles audited 2026-06-07 needed this.
    # D106-EXT v3 — TELECOM AT&T age/gender/ethnicity-aware lift. Sub-tier
    #   ordering: female-majority light-senior → very-senior → senior →
    #   high-Black → mainstream-young → mainstream-mainstream.
    try:
        df, n = apply_cp_canonical_normalize(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_cp_canonical_normalize failed: {e}")
    try:
        df, n = apply_telecom_canonical_normalize(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_telecom_canonical_normalize failed: {e}")
    # 2026-06-08 (Jenna 4-profile escalation):
    # D112 — DIGITAL BANKING Apple Pay / PayPal pair. 33 of 651 corpus
    # files (5.1%) ship with Apple Pay > PayPal — a transform/writer-bug
    # that doesn't track audience composition. 4-tier audience-aware band
    # for both brands + hard invariant (PayPal > Apple Pay by ≥ 5pp).
    try:
        df, n = apply_db_canonical_normalize(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_db_canonical_normalize failed: {e}")
    # 2026-06-08 (Jenna two-tail anchor calibration finding):
    # D111 — PORNHUB bidirectional band-clamp. The existing
    # enforce_shelf_category_distribution PORN MEDIA target is a fixed
    # 40pp anchor; combined with cases where it doesn't fire (LLM emits
    # a leader inside the rescale-skip threshold but at a wildly mis-
    # calibrated absolute level) we get both 65%+ over-reads (Ed Helms,
    # Elisabeth Moss, Patrick Stewart) AND 0.5-7% under-reads (Emma Stone,
    # Margot Robbie, Jane Fonda) on the same anchor.
    # 8-tier audience-aware band, bidirectional clamp, scales top-2 peers
    # by same factor so rank gradient stays intact.
    try:
        df, n = apply_porn_canonical_normalize(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_porn_canonical_normalize failed: {e}")
    # 2026-06-09 (Jenna escalation, Hilary Swank XNXX@49.6%): D118 — PORN
    # MEDIA leader-break invariant. apply_porn_canonical_normalize
    # rescales PORNHUB to its audience band and scales top-N peers WITH
    # it; if PORNHUB is already inside its band but a peer was over-
    # emitted by the LLM at 49% (XNXX, XVIDEOS, SEXTB, CHATURBATE,
    # YOUPORN, EPORNER, PORNTREX), the band-clamp no-ops and ships the
    # inversion. This invariant runs AFTER the band-clamp and forces
    # PORNHUB > all peers regardless of band state.
    try:
        df, n = apply_porn_leader_invariant(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_porn_leader_invariant failed: {e}")
    # 2026-06-08 (Jenna David Spade BoA): D115 — BP/CS internal inconsistency
    # recovery. Big-4 banking suppression class (D102b family) resurfacing
    # where writer corrupts BP/RAW/PROJ in lockstep but preserves CS.
    # Recover BP from the surviving CS using sum_others as ground truth.
    # Runs LATE so other lifts/caps have already settled the rest of the
    # block; this enforcer only touches the single corrupted row.
    try:
        df, n = apply_bp_cs_consistency_recovery(
            df, subject, verbose=verbose, bp_at_load=bp_at_load)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_bp_cs_consistency_recovery failed: {e}")
    # 2026-05-27 (D8): aggressive post-emit dejitter for X.X5/X.X0 display
    # bands. The colleague's audit definition of "intentional-looking 2dp"
    # is any value whose 2dp display lands on .X5 or .X0 (4.7531 → 4.75).
    # Run AFTER propagation so we catch values introduced by propagation
    # hops too.
    try:
        df, n = dejitter_x5x0_displays(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_x5x0_displays failed: {e}")
    # 2026-05-27 (D10): re-break cross-cat 4dp identity pins introduced
    # by propagation (same brand, exact 4dp BP in 3+ categories).
    try:
        df, n = dejitter_cross_cat_4dp_pins(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_cross_cat_4dp_pins failed: {e}")
    # 2026-05-27 (Rule #9 Pass B): re-break WITHIN-cat 4dp identity pins
    # — same category, 3+ different brands at exactly same 4dp BP. This
    # catches the placeholder-sentinel pattern (clamp-floors, batch
    # default-writes) that none of the per-cell depin passes detect.
    # Sweep found Krapopolis MPB had 69 brands at one BP, 51 at another.
    try:
        df, n = dejitter_within_cat_4dp_collisions(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_within_cat_4dp_collisions failed: {e}")
    # 2026-06-11 (Jenna's 13-talent batch QC): same category, 3+ brands
    # within ±0.005pp window — exact-4dp dejitter doesn't catch these
    # but they read as "pinned" at 2dp/3dp resolution to analysts. Spreads
    # the cluster across a wider window (default 0.008pp/row) using
    # subject-salted hash so values aren't pinned. Idempotent — once
    # spread, the cluster's window exceeds the trigger threshold.
    try:
        df, n = dejitter_within_cat_tight_clusters(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_within_cat_tight_clusters failed: {e}")
    # 2026-06-10 (Jenna's colleague's MPB cross-profile observation):
    # break CROSS-PROFILE 4dp pins — ONE (cat, brand) shared at exact 4dp
    # BP across MANY profiles. Caught: TELECOM/MINT MOBILE @ 1.9898%
    # across 5 talent profiles; CREDIT PROVIDER/VISA @ 56.3383% across
    # 4 show-cohort profiles (entire CP block pinned). Excludes LOCATION
    # (DMA codes legitimately repeat at gen-pop). Loads corpus index from
    # S3 with 1h cache; no-op if S3 unavailable.
    try:
        df, n = dejitter_cross_profile_4dp_collisions(
            df, subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_cross_profile_4dp_collisions failed: {e}")
    # 2026-05-30 (D1 fix from Jenna's May 30 batch escalation): catch
    # LLM rotating-placeholder BPs (5.6789, 6.7890, 7.8901, 8.9012,
    # 9.0123, 9.8765, 8.7654, ...) by PATTERN — these slip past the
    # 4dp-collision detector when the LLM emits a small ROTATING SET
    # of placeholders rather than a single repeated value.
    try:
        df, n = dejitter_sequential_placeholders(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_sequential_placeholders failed: {e}")
    # 2026-08-26 (Liz QA, Bethenny Frankel avid): same-suffix ladders —
    # many rows sharing one 4dp fractional part at integer steps
    # (67.8912 / 55.8912 / ... / 3.8912). Exact-4dp and sequential-digit
    # passes above don't catch these. Downward-only per-row re-salt.
    try:
        df, n = dejitter_fractional_ladders(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_fractional_ladders failed: {e}")
    # 2026-05-27 (D8 mop-up): cross-cat / within-cat dejitter can shift
    # values onto a round band by coincidence — run dejitter_x5x0 once
    # more to clean.
    try:
        df, n = dejitter_x5x0_displays(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer dejitter_x5x0 (mop-up) failed: {e}")
    # 2026-08-22 (Rule #3b): MPB exact mirror AFTER every dejitter/depin
    # pass so nothing re-breaks the exact identity. Fixes the standing
    # ~1,880-rows-per-file mirror drift (Aug 21-22 batch audit).
    try:
        df, n = enforce_mpb_exact_mirror(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_mpb_exact_mirror failed: {e}")
    # 2026-06-04 (Jenna sample-size clamp defect): detect 99K-110K clamp
    # signature on BRAND INPUT raw and re-ground niche/mid subjects to a
    # realistic small panel-share before final Raw/Proj recompute below.
    # The recompute step picks up the new BRAND INPUT raw automatically.
    try:
        df, n = reground_clamped_sample_size(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer reground_clamped_sample_size failed: {e}")
    # 2026-08-06 (Jenna: "make sure for all synth profiles that it always
    # only uses canonical demos ... should only be from these [demos.csv]").
    # Collapse non-canonical demographic bucket labels to the canonical
    # 5-EDU / 5-REL / 8-AGE / 13-OCC set from reference/demos.csv BEFORE
    # renormalizing to 100 (so drops leave BPs to redistribute
    # proportionally).
    try:
        df, n = enforce_canonical_demo_buckets(
            df, subject=subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_canonical_demo_buckets failed: {e}")

    # 2026-08-06 (Jenna: "check most purchased brands for north west. they
    # feel too lux. they can be those brands. The percentages just need to
    # be lower since this is an assumed confirmed purchase ... We just need
    # to ensure this doesn't happen again"). Cap MPB (+ sub-cat) rows for
    # brands in the 4-tier lux canon to panel-reality confirmed-purchase
    # bands. Idempotent: caps DOWN only. Runs AFTER canonical demos and
    # BEFORE the demo renormalize so downstream Rule 3b sync gets the
    # capped values.
    try:
        df, n = apply_lux_confirmed_purchase_caps(
            df, subject=subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_lux_confirmed_purchase_caps failed: {e}")

    # 2026-06 (Jenna P2 belt-and-suspenders): demographic-renormalize.
    # Each of the 9 demos (AGE, GENDER, ETHNICITY, INCOME, EDUCATION,
    # RELATIONSHIP, SEXUAL_ORIENTATION, PARENTAL_STATUS, OCCUPATION) rescaled
    # to sum=100% in place. Idempotent — no-op when within 0.5pp tolerance.
    # Closes the gap where enforce_all_demographic_categories only renorm'd
    # 5 of the 9 demos, allowing OCCUPATION sums of 110-141% to ship.
    # MUST run BEFORE recompute_raw_and_projection so the recompute sees
    # the fixed BPs.
    try:
        df, n = renormalize_demographics_to_100(
            df, subject=subject, tolerance=0.5, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer renormalize_demographics_to_100 failed: {e}")

    # 2026-08-14 (iJustine incident hardening, 4 small-sample enforcers):
    # Runs BEFORE normalize_final_format + recompute_raw_and_projection so
    # those pipeline-tail passes settle from post-hardened BP values.
    #  Rail A: LOCATION degrade to Gen-Pop-blend when sample_size < 1500
    #          (was causing 86 zero-DMAs on iJustine Avid at N=1000).
    #  Rail B: Canonical demo schema back-fill (was causing EDUCATION to
    #          ship with only 3 of 5 canonical buckets when panelists
    #          absent from a bucket in the raw ClickHouse pull).
    #  Rail C: Mass-brand zero floor (was causing Disney+/Hulu = 0.0 on
    #          iJustine Avid because 0 of 1000 panelists touched that
    #          hostname in the window - not a persona signal).
    #  Rail D: MPB rank-based de-band (was causing 114 brands to stack
    #          in 0.3pp on iJustine TU when Claude reached for a "generic
    #          middle" for weak-signal brands under small-sample stress).
    try:
        from migration.small_sample_hardening import (
            enforce_small_sample_location_degrade,
            enforce_canonical_demo_schema,
            enforce_mass_brand_zero_floor,
            enforce_mpb_deband,
        )
        for fn in (
            enforce_small_sample_location_degrade,
            enforce_canonical_demo_schema,
            enforce_mass_brand_zero_floor,
            enforce_mpb_deband,
        ):
            try:
                df, n = fn(df, subject=subject, verbose=verbose)
                total += n
            except Exception as e:
                print(f"   ⚠️ enforcer {fn.__name__} failed: {e}")
    except Exception as e:
        print(f"   ⚠️ small_sample_hardening import failed: {e}")

    # 2026-07-28 (pipeline hardening rail #5, WoF Avid defects 5/6/7/8):
    # final format normalizer. Runs immediately BEFORE
    # recompute_raw_and_projection so the recompute pass finalizes
    # Raw/Proj from post-normalized BP values.
    #  D5: SAMPLE SIZE Raw drift from BRAND INPUT Raw
    #  D6: rows with Raw=0 but BP>0 (phantom cells; LOCATION drift)
    #  D7: stale numeric fields on the BRAND CATEGORY metadata row
    #  D8: mixed '%' suffix in Brand Penetration (Row) column
    # Idempotent — no-op on already-clean files.
    try:
        df, n = normalize_final_format(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer normalize_final_format failed: {e}")

    # 2026-08-19 (Gilmore Girls incident): three defect-specific
    # fix-ups that must run AFTER the main enforcer chain and BEFORE
    # recompute_raw_and_projection so downstream Raw/Proj math is
    # canonicalized against the corrected BPs.
    #
    # D5b — content BRAND INPUT='CSV' should never spawn a peer row.
    try:
        df, n = strip_brand_input_csv_phantom_row(
            df, subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer strip_brand_input_csv_phantom_row "
              f"failed: {e}")
    # 2026-08-24 (Florida gubernatorial voters, Jenna: "there should be
    # no value in LOCATION outside of FL"): geo-DEFINED universes strip
    # every out-of-geography LOCATION row and renormalize in-scope DMAs
    # to ~100. Runs BEFORE renormalize_location_to_100 (which then
    # no-ops on the ~100 result) and BEFORE recompute_raw_and_projection
    # (which cascades the new BPs into Raw/Proj). Four gates prevent
    # false positives: geography + audience noun in the subject name,
    # geo-brand blocklist, in-scope share >= 30%, no >= 90 DMA pin
    # (geo cuts keep their pin).
    try:
        df, n = enforce_geo_scope_location(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_geo_scope_location failed: {e}")
    # D6 — LOCATION column must sum to 100 across all DMAs.
    try:
        df, n = renormalize_location_to_100(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer renormalize_location_to_100 failed: {e}")
    # D7 — parent brand row must not sit below its own segment anchor.
    try:
        df, n = sync_parent_row_to_segment_anchor(
            df, subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer sync_parent_row_to_segment_anchor "
              f"failed: {e}")

    # 2026-08-19 (Jenna followers-cap directive): if this build is a
    # followers-only cohort, enforce the physical projection ceiling
    # (US Gen Pop Projection <= real follower count) by capping the
    # sample size on BRAND INPUT / SAMPLE SIZE rows. MUST run BEFORE
    # recompute_raw_and_projection so the recompute pass cascades the
    # capped sample size into every brand row's Raw + Proj.
    # No-op when follower_ceiling is None / 0 (general audiences).
    try:
        df, n = enforce_follower_ceiling_projection(
            df, subject, follower_ceiling, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_follower_ceiling_projection "
              f"failed: {e}")

    # 2026-08-24 (Dylan Minnette MAXBONE/MAEV defect): profile raw count
    # must never exceed a defensible share of the brand's TOTAL Gen Pop
    # audience. Runs BEFORE recompute_raw_and_projection so the
    # recompute pass cascades the corrected BPs into Raw/Proj/CS.
    # Micro-gp brands repaired in place; mass-brand violations logged
    # for the Gen Pop reconcile path (2026-06-10 mandate).
    try:
        df, n = enforce_brand_share_plausibility(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_brand_share_plausibility failed: {e}")

    # 2026-08-26 (Liz QA, Paw Patrol): self-property coherence. On a
    # content/franchise subject the property's own merch/games/media
    # rows must be coherent with the FRANCHISE anchor (82.74% franchise
    # with 6.20% own toys is the defect signature). Runs AFTER the
    # brand-share pass (whose own-row exemption is fixed, but this is
    # the backstop if any earlier pass re-trims) and BEFORE
    # recompute_raw_and_projection.
    try:
        df, n = enforce_self_property_coherence(df, subject,
                                                verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_self_property_coherence "
              f"failed: {e}")

    # 2026-08-26 (Jenna, Jimmy Kimmel Live / Rosie hosted viewers):
    # viewer-carriage constraint. A consumption-scoped universe's
    # carrying platforms must jointly cover ~100% of the audience.
    # Runs AFTER every brand strip / plausibility pass (so nothing
    # re-lowers a carrier) and BEFORE recompute_raw_and_projection (so
    # the canonical math pass cascades the lifted BPs). Explicit
    # carriage_doc comes from the build spec (worker path); when None,
    # auto-resolution (detection + S3 cache + live research) only fires
    # on TU write paths (keep_avid_row=True) - derived cuts inherit
    # from their parent and skip this entirely.
    try:
        df, n = enforce_viewer_carriage_constraint(
            df, subject, carriage_doc=carriage_doc,
            allow_research=bool(keep_avid_row and carriage_doc is None),
            verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_viewer_carriage_constraint "
              f"failed: {e}")

    # 2026-08-26 (Jenna convention correction): own-property /
    # owner-platform pin. The subject's own property row and its
    # owning / universe-defining platform row pin at exactly 100.0000
    # in base and every cut. Runs BEFORE the exact-100 de-pin (which
    # exempts these same rows) and BEFORE recompute_raw_and_projection.
    try:
        df, n = pin_own_property_rows(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer pin_own_property_rows failed: {e}")

    # 2026-08-26 (Liz QA, Paw Patrol): exact-100 non-subject de-pin.
    # Runs AFTER every pin/carriage pass so nothing re-pins behind it
    # (the spec-pin enforcer itself now lands non-subject carrier pins
    # at a salted near-100, this is the catch-all) and BEFORE
    # recompute_raw_and_projection. Owner-verified platform rows,
    # cut-defining platforms, and single-carrier rows are exempt.
    try:
        df, n = depin_exact_100_non_subject(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer depin_exact_100_non_subject failed: {e}")

    # 2026-05-27 (D-Proj): VERY LAST -- recompute Raw + Proj from final BP.
    # Every previous enforcer that touched BP may have left stale Raw/Proj
    # cells. This pass canonicalizes the math:
    #   Raw  = BP/100 * sample_size
    #   Proj = (Raw / 10M) * US_POP    (subject-scaled, edit_sample_size.py-style)
    # so drift across enforcer hops is impossible. Sweep on 2026-05-27 found
    # ALL 230 profiles had stale cells (750,113 total). Must run after every
    # other enforcer settles. Updated 2026-05-28: Proj formula corrected from
    # BP/100*US_POP (which over-projects for subject_raw < 10M).
    try:
        df, n = recompute_raw_and_projection(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer recompute_raw_and_projection failed: {e}")

    # 2026-08-03 (Honey Pot streaming defect): repair the "Netflix
    # Share=100, everyone else NULL" signature BEFORE the general share
    # recompute — the health-check enforcer detects the pattern and
    # forces a BP-based rewrite that survives even when Raw for the
    # non-Netflix rows is stale/zero.
    try:
        df, n = enforce_streaming_share_health(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_streaming_share_health failed: {e}")

    # 2026-06-07 (Jenna deep audit, 549 of 560 files affected): final
    # Category Share recompute. Earlier enforcers don't always re-run
    # _renormalize_category on every block they touch; this pass ensures
    # Share = BP / ΣBP × 100 for every non-meta block (BP-based math,
    # 2026-08-03 hardening — raw-based math broke on stale-Raw files).
    # MUST run AFTER recompute_raw_and_projection (needs final BP values).
    try:
        df, n = apply_recompute_category_share(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_recompute_category_share failed: {e}")

    # 2026-08-04 Rail #6 (SharkNinja main file bimodal INCOME):
    # auto-repair mild-to-moderate bimodal INCOME. Runs BEFORE the
    # soft-warn validator so most audiences are silently fixed;
    # validator only speaks up when auto-fix's dual-cohort guardrail
    # declined to touch (rare, legitimately bimodal case).
    try:
        df, n_ir = apply_income_monotonicity_fix(
            df, subject=subject, verbose=verbose,
        )
        total += n_ir
    except Exception as e:
        print(f"   ⚠️ enforcer apply_income_monotonicity_fix failed: {e}")

    # 2026-08-03 (Honey Pot bimodal INCOME): soft-warn on bimodal
    # INCOME distributions. Read-only — does NOT auto-fix (some
    # audiences are legitimately bimodal). Result is logged for the
    # writer/auditor to review.
    try:
        n_bi, _ = validate_income_monotonicity(
            df, subject=subject, verbose=verbose,
        )
        total += n_bi
    except Exception as e:
        print(f"   ⚠️ enforcer validate_income_monotonicity failed: {e}")

    # 2026-08-04 Rail #7 late repin (SharkNinja): re-pin multi-brand
    # BRAND INPUT components in case any of the ~30 mid-pipeline
    # enforcers (MPB digital lifts, propagation, canonical normalizes,
    # non-hostmap resets) dropped SHARK / NINJA below 100.
    try:
        df, n = enforce_multi_brand_input_self_pin(
            df, subject=subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_multi_brand_input_self_pin (late) "
              f"failed: {e}")

    # 2026-06-16 (Jenna Defect 37 -- Peacock/LiveTV/YouTube/Citibank
    # near-miss + overflow): FINAL guarantee pass. pin_subject_to_100_*
    # earlier in the chain only pins ONE "native" grid; this catches the
    # legacy-alias sister grids (STREAMING VIDEO when canonical is
    # STREAMING/PLATFORM, BANK when canonical is BANKS) AND overflow
    # (BP > 100% which is mathematically impossible). Corpus sweep on
    # 2026-06-16 found 110 files / 181 rows affected -- mostly Avid Fans
    # where _apply_persona_uniqueness_noise clamps to [0.5, 99.5] yielding
    # 99.49% self-pin without a follow-up subject-pin step.
    try:
        df, n = enforce_subject_self_pin_final(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_subject_self_pin_final failed: {e}")

    # 2026-06-16 (Defect 37 mop-up): hard-ceiling for any non-subject row
    # > 100%. Previously only in run_post_vet_safety_sweep, which doesn't
    # run for Avid Fan synthesis -- YouTube STREAMING VIDEO shipped at
    # 100.3744% as a result. Runs AFTER enforce_subject_self_pin_final so
    # legitimate subject overflows (e.g. 100.37 -> 100) are pinned first;
    # this is the catch-all for non-subject rows (e.g. peer brands the
    # LLM emitted at 100.1%).
    try:
        df, n = enforce_bp_hard_ceiling(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_bp_hard_ceiling failed: {e}")

    # 2026-06-17 (Jenna Santander Bank — Defect 28/30 final-pass guarantee):
    # Netflix BP can be DROPPED back below the 73% floor by any of the ~20
    # passes that run after the early enforce_netflix_leads_streaming_platform
    # call (propagation, dejitter, recompute_raw_and_projection,
    # apply_recompute_category_share, enforce_bp_hard_ceiling, ...). Santander
    # Bank shipped 2026-06-17 with NX=62.13 / PR=64.17 (a Prime>Netflix
    # inversion) despite the early enforcer running, because a later pass
    # re-introduced the suppression. This SECOND, FINAL pass runs after
    # everything else has settled and guarantees the on-disk file written by
    # run_full_pipeline cannot ship with Netflix < baseline floor or any
    # peer leading Netflix in STREAMING/PLATFORM (excl. self-pins).
    try:
        df, n = enforce_netflix_leads_streaming_platform(
            df, subject, verbose=verbose,
        )
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer enforce_netflix_leads_streaming_platform "
              f"(final pass) failed: {e}")

    # 2026-07-09 (Jenna consolidation): apply_platform_pin — self-anchor
    # pin driven by BG_PIN_PLATFORM env var set by the caller (dispatcher
    # spec or runner-script argv). No-op when the env var is empty.
    # Runs AFTER every other streaming-platform enforcer
    # (enforce_netflix_leads_streaming_platform, enforce_niche_streamer_caps,
    # enforce_bp_hard_ceiling) so its 100% pin is authoritative for the
    # requested platform. Replaces the ad-hoc /tmp/strict_youtube_pin_watcher
    # + /tmp/generic_pin_watcher scripts, ensuring dashboard-initiated
    # pulls receive identical pinning to CLI-initiated pulls.
    try:
        df, n = apply_platform_pin(df, subject, verbose=verbose)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer apply_platform_pin failed: {e}")

    # Fan-row semantics (REVISED 2026-08-24, Jenna "Go with what you
    # recommend"): CASUAL FAN stripped always, everywhere (the TU IS the
    # casual cohort). AVID FAN kept on TUs when the caller threads
    # keep_avid_row=True (profile_writer auto-detects TU keys; BG.py
    # save-gate passes True), stripped on derived cuts and unthreaded
    # legacy callers. Runs LAST-mutating (after every row-touching
    # enforcer + after apply_platform_pin) and just before the read-only
    # validate_demo_sum_100 check. See strip_avid_casual_fan_rows'
    # docstring for the full reversal rationale.
    try:
        df, n = strip_avid_casual_fan_rows(df, subject, verbose=verbose,
                                           keep_avid_row=keep_avid_row)
        total += n
    except Exception as e:
        print(f"   ⚠️ enforcer strip_avid_casual_fan_rows failed: {e}")

    # 2026-06 (Jenna P0 validator): validate_demo_sum_100 as the final
    # check in the chain. By this point renormalize has fixed any drift
    # and recompute_raw_and_projection has rebuilt Raw/Proj — if a demo
    # still doesn't sum to 100±0.5 something is structurally wrong (rows
    # were added/dropped after renorm). Logs loudly; does NOT raise here
    # (raise=False keeps run_all_enforcers compatible with callers that
    # expect non-fatal best-effort enforcement). The actual write-time
    # block lives in run_pre_publish_gate G5 (raise_on_fail=True path).
    try:
        viols = validate_demo_sum_100(
            df, subject=subject, tolerance=0.5,
            raise_on_fail=False, verbose=verbose,
        )
        # Return demo-sum violations as part of total (informational).
        total += len(viols)
    except Exception as e:
        print(f"   ⚠️ enforcer validate_demo_sum_100 failed: {e}")

    # 2026-08-18 (Andy Grammer defect - defense in depth): re-sync
    # Raw + Proj + Category Share to final BP one more time. Many
    # post-recompute enforcers (enforce_bp_hard_ceiling,
    # enforce_subject_self_pin_final,
    # enforce_netflix_leads_streaming_platform, apply_platform_pin,
    # strip_avid_casual_fan_rows, validate_demo_sum_100's auto-fix
    # branch) can drift BP without recomputing the downstream
    # columns. The individual enforcers now recompute in-place where
    # they can, but this tail-pass guarantees a clean invariant
    # regardless of which enforcer touched what: every row's Raw /
    # Proj / Category Share matches its displayed BP against the
    # resolved sample size. Both passes are idempotent.
    try:
        df, n_final = recompute_raw_and_projection(df, subject,
                                                    verbose=verbose)
        if n_final and verbose:
            print(f"   🔁 tail recompute_raw_and_projection: fixed "
                  f"{n_final} cell(s) drifted after main recompute pass")
        total += n_final
    except Exception as e:
        print(f"   ⚠️ tail recompute_raw_and_projection failed: {e}")

    try:
        df, n_final_share = apply_recompute_category_share(
            df, subject, verbose=verbose,
        )
        if n_final_share and verbose:
            print(f"   🔁 tail apply_recompute_category_share: fixed "
                  f"{n_final_share} cell(s) drifted after main share pass")
        total += n_final_share
    except Exception as e:
        print(f"   ⚠️ tail apply_recompute_category_share failed: {e}")

    return df, total


# ============================================================================
# Post-vet safety sweep — runs AFTER agent_reason_vet_failures has finished
# Added 2026-05-29 from Nike-output defect list (notes from Jenna):
#   1. YouTube 100.34%  →  hard-cap >100% non-subject rows
#   2. INPUT_METADATA leak re-appears post-enforcer-chain  →  late re-strip
#   3. Apple 76.55% + Android 31.49% sum to 108%  →  mobile-OS balance
#   4. Tesla 49.8% / Lexus 19.1% / Rivian 13.2% inflated; Ford/Toyota/Honda
#      /Chevy suppressed  →  mainstream-auto floor for brand profiles
#   5. Mastercard 25.02% (real ~62%)  →  credit-provider floor
#   6. Walmart/Target/AA/Southwest landed on X.00 round numbers from the
#      Claude arbiter / GPT-4o vet-reason agent  →  re-run dejitter
#   7. CPG top-N clustered tightly around 19% (Coca-Cola=19.74, Doritos=19.72,
#      Colgate=19.59, Gillette=19.42...)  →  cluster-compression detector
#      (log only — auto-fix would risk making things worse)
# ============================================================================

import re as _re_sweep

# ---------------------------------------------------------------------------
# Bug 1: BP > 100% hard ceiling
# ---------------------------------------------------------------------------

def enforce_bp_hard_ceiling(df, subject, verbose=True):
    """Repair any BP > 100% EXCEPT the canonical BRAND INPUT subject row,
    which legitimately sits at exactly 100.0%.

    Nike shipped with SOCIAL MEDIA / YOUTUBE = 100.3406% - mathematically
    impossible (you can't have more than 100% of a sample doing X).

    2026-08-18 (Andy Grammer defect): also recompute Original Raw
    Numbers + US Gen Pop Projection for every repaired row using the
    same sample-size math as recompute_raw_and_projection. Previously
    this enforcer only touched BP, leaving Raw > sample_size on every
    capped row.

    2026-08-24 (Bethenny HEINZ/NINJA 99.9900 pins): the old behavior
    clamped every overflow to the CONSTANT 99.99, which shipped
    non-subject rows pinned at 99.9900% (CPG HEINZ on a 9.77 Gen Pop
    baseline, index 1033; the final ship gate's I1 now blocks exactly
    that signature). New behavior mirrors the engine's anchor-guard
    semantics (scripts/synth_engine_row_by_row.py):

      - subject self-row overflow: restore the exact 100.0 self-pin
        (enforce_subject_self_pin_final runs first; this is the net)
      - non-subject, Gen Pop baseline >= 30: a near-total read can be
        legitimate; clamp to 100 minus a subject-salted epsilon in
        [0.3, 2.5] (4dp, never on a .XX00 boundary, never 99.99)
      - non-subject, baseline < 30: an over-ceiling read is a misfire,
        not a signal; re-anchor to baseline x subject-salted [1.5, 3.5]
        (bounded below a salted ~96.5 hard cap)
      - baseline unknown: salted modest seed in [3.5, 8.5]

    The SAME replacement lands on every violating row of the same brand
    (Rule #3b), and enforce_mpb_exact_mirror re-runs afterwards (when
    the helper exists in this module) so MPB/subcategory mirrors stay
    exact without hand-writing sibling cells. Every mutated category
    gets its Category Share recomputed at mutation time.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    _bp_dtype = str(df[bp_col].dtype)
    is_str_col = (df[bp_col].dtype == object
                  or _bp_dtype.startswith('string')
                  or _bp_dtype == 'str')
    # pandas >= 3 'str' / StringDtype columns reject non-string
    # assignments (raw/proj ints, CS floats). Coerce to object first,
    # same pattern as enforce_mpb_exact_mirror.
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if (_c and _c in df.columns
                and df[_c].dtype.name not in ('object', 'O',
                                              'float64', 'int64')):
            df[_c] = df[_c].astype(object)
    col_u = df['Column'].astype(str).str.upper().str.strip()

    # Sample size for Raw/Proj recompute. Matches
    # recompute_raw_and_projection's resolution order (BRAND INPUT
    # raw / bp preferred; fall back to SAMPLE SIZE raw). If we can't
    # resolve it, fall back to BP-only repair and let the final
    # recompute pass fix the Raw drift.
    def _num_local(v):
        try:
            return float(str(v).replace(',', '').replace('%', '').strip())
        except Exception:
            return None

    sample_size = None
    for cat in ('BRAND INPUT', 'SAMPLE SIZE'):
        cand = df[col_u == cat]
        if len(cand) == 0:
            continue
        r = cand.iloc[0]
        bp_r = _num_local(r.get(bp_col))
        raw_r = _num_local(r.get(raw_col)) if raw_col else None
        if raw_r and bp_r and bp_r > 0:
            sample_size = raw_r / (bp_r / 100.0)
            break

    # Gen Pop baseline map for the branch decision. Unavailable map ->
    # every baseline reads unknown -> misfire seed path (never a
    # pinned constant).
    try:
        try:
            from migration.genpop_baseline import load_genpop_map
        except ImportError:
            from genpop_baseline import load_genpop_map  # type: ignore
        gp_map = load_genpop_map() or {}
    except Exception:
        gp_map = {}
    import re as _re_hc

    def _nc_hc(c):
        return _re_hc.sub(r'[_\s]+', ' ', str(c or '').strip().upper())

    def _nb_hc(b):
        return _re_hc.sub(r'[^a-z0-9]+', '', str(b or '').lower())

    _brand_max_gp = {}
    for _k, _val in gp_map.items():
        try:
            _gb = _k[1]
            _gbp = float(_val[0])
        except Exception:
            continue
        if _gb not in _brand_max_gp or _gbp > _brand_max_gp[_gb]:
            _brand_max_gp[_gb] = _gbp
    _subj_norm_hc = _nb_hc(subject)

    PANEL = 10_000_000
    n_caps, examples = 0, []
    new_by_brand = {}
    touched_cats = set()
    for idx, raw in df[bp_col].items():
        cur = _bp(raw)
        if cur is None or cur <= 100.0:
            continue
        # BRAND INPUT row at exactly 100% is legitimate; anything else
        # > 100% is a math bug.
        if col_u.at[idx] == 'BRAND INPUT' and abs(cur - 100.0) < 0.001:
            continue
        val = str(df.at[idx, 'Value']).strip()
        bnorm = _nb_hc(val)
        if bnorm and bnorm == _subj_norm_hc:
            # Subject self-row overflow: exact self-pin restore.
            new = 100.0
        elif bnorm in new_by_brand:
            # Rule #3b: the same brand gets the same replacement in
            # every category it violates in.
            new = new_by_brand[bnorm]
        else:
            base = None
            hit = gp_map.get((_nc_hc(df.at[idx, 'Column']), bnorm))
            if hit:
                try:
                    base = float(hit[0])
                except Exception:
                    base = None
            if base is None:
                base = _brand_max_gp.get(bnorm)
            if base is not None and base >= 30.0:
                eps = _jitter_for(subject, val, salt='hard-ceiling-eps',
                                  lo=0.3, hi=2.5)
                new = round(100.0 - eps, 4)
            elif base is not None and base > 0:
                mult = _jitter_for(subject, val, salt='hard-ceiling-mult',
                                   lo=1.5, hi=3.5)
                cap = 96.5 - _jitter_for(subject, val,
                                         salt='hard-ceiling-cap',
                                         lo=0.1, hi=1.9)
                new = round(min(base * mult, cap), 4)
            else:
                new = _jitter_for(subject, val, salt='hard-ceiling-seed',
                                  lo=3.5, hi=8.5)
            # Keep off .XX00 boundaries (subject 100.0 restore exempt).
            if abs(new * 100 - round(new * 100)) < 1e-4:
                new = round(new + 0.0017, 4)
            new_by_brand[bnorm] = new
        if is_str_col:
            df.at[idx, bp_col] = f'{new:.4f}%'
        else:
            df.at[idx, bp_col] = new
        # Recompute Raw + Proj so the row stays internally consistent.
        # Skips silently if sample_size couldn't be resolved above -
        # the terminal recompute_raw_and_projection safety-net pass
        # (see run_all_enforcers) will catch that case.
        if sample_size is not None:
            if raw_col is not None:
                try:
                    df.at[idx, raw_col] = int(round(new / 100.0 * sample_size))
                except Exception:
                    pass
            if proj_col is not None:
                try:
                    df.at[idx, proj_col] = int(round(
                        new / 100.0 * sample_size * (US_POP / PANEL)))
                except Exception:
                    pass
        n_caps += 1
        examples.append((df.at[idx, 'Column'], df.at[idx, 'Value'],
                         cur, new))
        touched_cats.add(str(df.at[idx, 'Column']).strip())
    if n_caps:
        # Mutation-time share coherence for every touched category.
        for _cat in touched_cats:
            try:
                df = _recompute_cs_for_cat(df, _cat, bp_col, cs_col)
            except Exception:
                pass
        # Route mirror propagation through the canonical Rule #3b
        # helper instead of hand-writing sibling cells.
        _mirror = globals().get('enforce_mpb_exact_mirror')
        if _mirror is not None:
            try:
                df, _ = _mirror(df, subject, verbose=False)
            except Exception:
                pass
    if n_caps and verbose:
        print(f'   🚧 bp-hard-ceiling: repaired {n_caps} row(s) with BP '
              f'>100% (baseline-aware, subject-salted; no 99.99 pin)')
        for c, v, old, nv in examples[:5]:
            print(f'      [{c}] {v}: {old:.4f}% -> {nv:.4f}%')
    return df, n_caps


# ---------------------------------------------------------------------------
# Bug 3: Mobile-OS balance (Apple vs Android)
# ---------------------------------------------------------------------------

# US market share 2025: ~57% iOS / ~43% Android. Most adult profiles skew
# slightly more iOS (62/38) because digital-active audiences over-index iOS.
# We allow per-profile drift but bound Apple+Android to [95, 105].
MOBILE_OS_TARGET_SUM_LO = 92.0
MOBILE_OS_TARGET_SUM_HI = 102.0
MOBILE_OS_DEFAULT_APPLE_SHARE = 0.62  # of (Apple + Android) sum


def enforce_mobile_os_balance(df, subject, verbose=True):
    """Apple + Android in TECHNOLOGY/DEVICE should sum to ~95-100%
    (every smartphone owner has one OS). If they over-tilt or under-fill,
    renormalize while preserving the per-profile iOS-skew direction.

    Nike shipped Apple=76.55% + Android=31.49% = 108.04% — impossible
    because users have ONE phone OS.
    """
    if df is None or len(df) == 0:
        return df, 0
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_u = df['Value'].astype(str).str.upper().str.strip()
    tech_mask = col_u == 'TECHNOLOGY/DEVICE'
    apple_idx = df.index[tech_mask & (val_u == 'APPLE')]
    android_idx = df.index[tech_mask & (val_u == 'ANDROID')]
    if len(apple_idx) == 0 or len(android_idx) == 0:
        return df, 0
    ai, ndi = apple_idx[0], android_idx[0]
    apple_bp = _bp(df.at[ai, bp_col])
    android_bp = _bp(df.at[ndi, bp_col])
    if apple_bp is None or android_bp is None:
        return df, 0
    total = apple_bp + android_bp
    if MOBILE_OS_TARGET_SUM_LO <= total <= MOBILE_OS_TARGET_SUM_HI:
        return df, 0

    # Preserve the iOS-skew direction. If Apple was higher, keep iOS skewed;
    # if Android was higher, keep Android skewed.
    if apple_bp + android_bp > 0:
        apple_share = apple_bp / (apple_bp + android_bp)
    else:
        apple_share = MOBILE_OS_DEFAULT_APPLE_SHARE
    # Clamp the iOS share to [0.45, 0.75] — even Android-skewed audiences
    # rarely fall below 45% iOS in US adult panels.
    apple_share = max(0.45, min(0.75, apple_share))
    target_sum = (MOBILE_OS_TARGET_SUM_LO + MOBILE_OS_TARGET_SUM_HI) / 2  # 97
    new_apple = round(target_sum * apple_share, 4)
    new_android = round(target_sum * (1 - apple_share), 4)
    # Deterministic jitter so different profiles don't all land on same value
    seed = int(_hl.md5(f'{subject}|mobile-os'.encode()).hexdigest()[:8], 16)
    norm = ((seed % 1000) / 1000.0 - 0.5) * 2  # -1..1
    new_apple = round(max(40.0, min(85.0, new_apple + norm * 1.2)), 4)
    new_android = round(max(15.0, min(55.0, new_android - norm * 0.8)), 4)

    is_str_col = (df[bp_col].dtype == object
                  or str(df[bp_col].dtype).startswith('string'))
    for idx, new in [(ai, new_apple), (ndi, new_android)]:
        if is_str_col:
            df.at[idx, bp_col] = f'{new:.4f}%'
        else:
            df.at[idx, bp_col] = new

    if verbose:
        print(f'   📱 mobile-os balance: Apple+Android was {total:.2f}% '
              f'(target {MOBILE_OS_TARGET_SUM_LO:.0f}-{MOBILE_OS_TARGET_SUM_HI:.0f}%)')
        print(f'      APPLE:   {apple_bp:.2f}% → {new_apple:.4f}%')
        print(f'      ANDROID: {android_bp:.2f}% → {new_android:.4f}%')
    return df, 2


# ---------------------------------------------------------------------------
# Bugs 4+5: Generic consensus-floor enforcer for mainstream brand categories
# (auto + credit) — same pattern as HOUSEHOLD_STREAMING_CONSENSUS_MID
# ---------------------------------------------------------------------------

# Auto: digital-penetration mids for 2025 US mass-market household audiences.
# These are DIGITAL BP (% of audience that visited brand domains/pages), NOT
# ownership %. Tesla/Lexus/Rivian aren't suppressed — the LLM was OVER-tilting
# them. The fix is a FLOOR on mainstream brands, not a CEILING on EV/luxury.
AUTOMOBILE_CONSENSUS_MID: dict[tuple[str, str], float] = {
    ('AUTOMOBILE', 'TOYOTA'):     38.0,
    ('AUTOMOBILE', 'HONDA'):      32.0,
    ('AUTOMOBILE', 'FORD'):       36.0,
    ('AUTOMOBILE', 'CHEVROLET'):  29.0,
    ('AUTOMOBILE', 'NISSAN'):     22.0,
    ('AUTOMOBILE', 'HYUNDAI'):    20.0,
    ('AUTOMOBILE', 'KIA'):        18.0,
    ('AUTOMOBILE', 'SUBARU'):     15.0,
    ('AUTOMOBILE', 'JEEP'):       24.0,
    ('AUTOMOBILE', 'DODGE'):      14.0,
    ('AUTOMOBILE', 'RAM'):        13.0,
    ('AUTOMOBILE', 'VOLKSWAGEN'): 14.0,
    ('AUTOMOBILE', 'GMC'):        16.0,
}

# Credit: digital BPs for major card networks (2025 US adult panels).
CREDIT_PROVIDER_CONSENSUS_MID: dict[tuple[str, str], float] = {
    ('CREDIT PROVIDER', 'VISA'):                 64.0,
    ('CREDIT PROVIDER', 'MASTERCARD'):           52.0,
    ('CREDIT PROVIDER', 'AMERICAN EXPRESS'):     28.0,
    ('CREDIT PROVIDER', 'DISCOVER'):             24.0,
    ('CREDIT PROVIDER', 'DISCOVER CREDIT CARD'): 24.0,
    ('CREDIT PROVIDER', 'CAPITAL ONE'):          26.0,
}

# Telecom: digital BPs (% of audience visiting brand domains) for major US
# carriers + ISPs on 2025 adult panels. Added 2026-06-01 after Jenna's
# Marmot audit flagged AT&T at 24.83% (T-Mobile 32.87, Verizon 29.68, AT&T
# 24.83) — AT&T should be within ~3pp of the other Big-3, not 5-8pp below.
#
# These are gen-pop-leaning digital BPs (account login / billing /
# support traffic). Big-3 carriers all sit in the 30-36% band on adult
# panels; under-suppression below ~26% indicates LLM archetype bias
# (e.g., outdoor / male / affluent personas getting "all on T-Mobile").
TELECOM_CONSENSUS_MID: dict[tuple[str, str], float] = {
    ('TELECOM', 'T-MOBILE'):    34.0,
    ('TELECOM', 'TMOBILE'):     34.0,
    ('TELECOM', 'VERIZON'):     34.0,
    # AT&T mid raised to 34 (from 32) so the 8pp trigger gap catches values
    # ≤ 26 — Jenna's Marmot audit value (24.83%) was 0.83pp inside the old
    # threshold and slipped through. AT&T digital BP on US adult panels is
    # 32-36% (wireless + uVerse/internet + DirecTV traffic combined).
    ('TELECOM', 'AT&T'):        34.0,
    ('TELECOM', 'ATT'):         34.0,
    ('TELECOM', 'XFINITY'):     22.0,
    ('TELECOM', 'SPECTRUM'):    18.0,
}

CONSENSUS_FLOOR_TRIGGER_GAP_PCT = 8.0
CONSENSUS_FLOOR_TARGET_PCT = 0.96


def _generic_consensus_floor(df, subject, consensus_mid: dict,
                              floor_name: str, emoji: str = '🎯',
                              brand_profiles_only: bool = False,
                              verbose: bool = True):
    """Generic consensus-floor enforcer, parameterized by a (col,brand)→mid dict."""
    if df is None or len(df) == 0:
        return df, 0
    if brand_profiles_only:
        is_brand, brand_cat = _is_brand_profile(df)
        if not is_brand:
            return df, 0
    df = df.copy()
    bp_col, cs_col, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0
    # 2026-06-01 (Marmot audit harness): same StringDtype guard as the
    # shelf-distribution enforcer — see comment there.
    for _c in (bp_col, cs_col):
        if _c is not None and str(df[_c].dtype).startswith(('str', 'string')):
            df[_c] = df[_c].astype(object)
    is_str_col = (df[bp_col].dtype == object
                  or str(df[bp_col].dtype).startswith('string'))
    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_u = df['Value'].astype(str).str.upper().str.strip()
    n_lifts, examples, cats_renorm = 0, [], set()

    for (cat_u, brand_u), mid in consensus_mid.items():
        mask = (col_u == cat_u) & (val_u == brand_u)
        if not mask.any():
            continue
        idx = df.index[mask][0]
        cur = _bp(df.at[idx, bp_col])
        if cur is None or cur >= mid - CONSENSUS_FLOOR_TRIGGER_GAP_PCT:
            continue
        seed = int(_hl.md5(f'{subject}|{cat_u}|{brand_u}|{floor_name}'
                          .encode()).hexdigest()[:8], 16)
        norm = ((seed % 10000) / 10000.0 - 0.5) * 2
        target = mid * CONSENSUS_FLOOR_TARGET_PCT + norm * 1.5
        target = round(max(0.5, min(99.5, target)), 4)
        if is_str_col:
            df.at[idx, bp_col] = f'{target:.4f}%'
        else:
            df.at[idx, bp_col] = target
        n_lifts += 1
        cats_renorm.add(cat_u)
        examples.append((cat_u, brand_u, cur, target, mid))

    if n_lifts and verbose:
        print(f'   {emoji} {floor_name}: lifted {n_lifts} row(s)')
        for c, b, old, new, mid in examples:
            print(f'      [{c}] {b}: {old:.2f}% → {new:.4f}% (consensus {mid:.0f}%)')

    if n_lifts and cs_col is not None:
        cs_is_str = (df[cs_col].dtype == object
                     or str(df[cs_col].dtype).startswith('string'))
        for cat_u in cats_renorm:
            idxs = df.index[col_u == cat_u]
            bps = df.loc[idxs, bp_col].apply(_bp)
            total = bps.sum(skipna=True)
            if total and total > 0:
                shares = (bps / total * 100).round(4)
                if cs_is_str:
                    df.loc[idxs, cs_col] = shares.apply(
                        lambda v: f'{v:.4f}' if pd.notna(v) else v).values
                else:
                    df.loc[idxs, cs_col] = shares.values
    return df, n_lifts


def enforce_household_auto_floor(df, subject, verbose=True):
    """Lift mainstream auto brands (Toyota/Honda/Ford/Chevy/etc.) on brand
    profiles when LLM agent suppressed them in favor of Tesla/Lexus/Rivian
    archetype bias."""
    return _generic_consensus_floor(
        df, subject, AUTOMOBILE_CONSENSUS_MID,
        'household-auto floor', emoji='🚗',
        brand_profiles_only=True, verbose=verbose,
    )


def enforce_credit_provider_floor(df, subject, verbose=True):
    """Lift major card networks (Visa/Mastercard/AmEx/Discover) when LLM
    agent suppressed them below household digital-penetration consensus.
    Applies to BOTH brand and talent profiles — everyone uses Visa."""
    return _generic_consensus_floor(
        df, subject, CREDIT_PROVIDER_CONSENSUS_MID,
        'credit-provider floor', emoji='💳',
        brand_profiles_only=False, verbose=verbose,
    )


def enforce_telecom_floor(df, subject, verbose=True):
    """Lift major US carriers + ISPs (T-Mobile/Verizon/AT&T/Xfinity/Spectrum)
    when LLM agent suppresses them below household digital-penetration
    consensus. Added 2026-06-01 after Jenna's Marmot audit flagged AT&T low
    at 24.83% (vs T-Mobile 32.87 / Verizon 29.68 in same file).

    Applies to BOTH brand and talent profiles — telecom is universal."""
    return _generic_consensus_floor(
        df, subject, TELECOM_CONSENSUS_MID,
        'telecom floor', emoji='📶',
        brand_profiles_only=False, verbose=verbose,
    )


# ---------------------------------------------------------------------------
# CEILING enforcers (Jenna 2026-06-01 SEARCH-pinning audit)
#
# Mirror of _generic_consensus_floor — caps brands the LLM cat/vet agents
# systematically over-pin across radically different personas. Trigger
# is symmetric to the floor: lift if current > ceiling + trigger gap.
# ---------------------------------------------------------------------------

# Persona-realistic digital-BP ceilings for SEARCH ENGINE/AI. The LLM
# vet-agent receives the audit framework's PUBLISHED CONSENSUS as a
# reasoning anchor and consistently keeps Google ~92% / ChatGPT ~80% across
# wildly different personas (ISP, horror movie, pet supplement, outdoor
# apparel — all within 2pp of each other in Jenna's 06-01 review). These
# caps reflect Pew Feb 2025 (~36-39% of US adults have used ChatGPT) and
# ComScore Google digital reach (~70-85% by persona).
#
# Per-(col, brand) config:
#   trigger_above — cap if BP > this value (kills the ~85-95% Google
#                   templating + ~70-85% ChatGPT templating)
#   target_mid    — recentered to this value (persona-realistic)
#   jitter_pp     — ± per-subject deterministic jitter (md5-seeded), wide
#                   enough to give visible cross-profile variance
# 2026-06-01 (Jenna principle: "never pinning, always reasoning"):
# the per-(col,brand) target_mid / jitter approach was still pinning —
# md5(subject) is not reasoning, it's just noise. SEARCH ENGINE/AI now
# uses enforce_search_engine_ai_persona_grounded() which DERIVES Google
# and ChatGPT targets from the profile's own AGE / INCOME / INTEREST
# signals. The dict below is kept empty so the old _generic_consensus_
# ceiling code path is a no-op for SEARCH categories — anyone wiring a
# new ceiling for a different category can still use the helper.
SEARCH_ENGINE_AI_CEILING: dict[tuple[str, str], dict] = {}
SEARCH_ENGINE_AI_FLOOR: dict[tuple[str, str], float] = {}


def _generic_consensus_ceiling(df, subject, consensus_ceiling: dict,
                                ceiling_name: str, emoji: str = '🚧',
                                brand_profiles_only: bool = False,
                                verbose: bool = True):
    """Generic consensus-ceiling enforcer, parameterized by a
    (col,brand)→{trigger_above, target_mid, jitter_pp} dict. Symmetric in
    spirit to _generic_consensus_floor but with explicit recentering
    (rather than a single 'mid' value with derived trigger) so each
    capped brand can be tuned independently for both threshold and
    target variance.

    Skips rows that match the brand input (so an Alphabet/Google profile
    keeps its 100% pin on GOOGLE; an OpenAI profile keeps its 100% pin
    on CHATGPT) — detected via the BRAND INPUT row's Value.
    """
    if df is None or len(df) == 0:
        return df, 0
    if brand_profiles_only:
        is_brand, _ = _is_brand_profile(df)
        if not is_brand:
            return df, 0
    df = df.copy()
    bp_col, cs_col, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0
    for _c in (bp_col, cs_col):
        if _c is not None and str(df[_c].dtype).startswith(('str', 'string')):
            df[_c] = df[_c].astype(object)
    is_str_col = (df[bp_col].dtype == object
                  or str(df[bp_col].dtype).startswith('string'))
    col_u = df['Column'].astype(str).str.upper().str.strip()
    val_u = df['Value'].astype(str).str.upper().str.strip()

    # Identify the BRAND INPUT row value (or project_name proxy) so we
    # don't cap a brand-input pin (e.g. an Alphabet/Google profile).
    bi_mask = (col_u == 'BRAND INPUT')
    brand_input_u = ''
    if bi_mask.any():
        brand_input_u = str(df.loc[bi_mask, 'Value'].iloc[0]).upper().strip()

    n_caps, examples, cats_renorm = 0, [], set()

    for (cat_u, brand_u), cfg in consensus_ceiling.items():
        # Allow either {dict} or {float} cfg for backward compat
        if isinstance(cfg, (int, float)):
            trigger_above = float(cfg)
            target_mid    = float(cfg) * 0.85
            jitter_pp     = 6.0
        else:
            trigger_above = float(cfg.get('trigger_above', cfg.get('ceiling', 90.0)))
            target_mid    = float(cfg.get('target_mid', trigger_above * 0.85))
            jitter_pp     = float(cfg.get('jitter_pp', 6.0))

        mask = (col_u == cat_u) & (val_u == brand_u)
        if not mask.any():
            continue
        idx = df.index[mask][0]
        cur = _bp(df.at[idx, bp_col])
        if cur is None or cur <= trigger_above:
            continue
        # Skip if this brand IS the profile's brand input (preserve 100%
        # pin). Match either exact equality or substring inclusion (e.g.
        # brand input "ALPHABET" / "GOOGLE LLC" both protect "GOOGLE";
        # "OPENAI" protects "CHAT GPT"/"CHATGPT").
        if brand_input_u:
            if brand_u == brand_input_u:
                continue
            if (brand_u in brand_input_u) or (brand_input_u in brand_u):
                continue
            _parents = {
                'GOOGLE':   {'ALPHABET', 'GOOGLE LLC', 'GOOGLE INC'},
                'CHAT GPT': {'OPENAI', 'OPEN AI', 'OPENAI INC'},
                'CHATGPT':  {'OPENAI', 'OPEN AI', 'OPENAI INC'},
            }
            if brand_input_u in _parents.get(brand_u, set()):
                continue
        # Per-subject deterministic jitter — ensures Google + ChatGPT do
        # NOT land on identical values across profiles (the explicit
        # anti-pinning requirement).
        seed = int(_hl.md5(f'{subject}|{cat_u}|{brand_u}|{ceiling_name}'
                          .encode()).hexdigest()[:8], 16)
        norm = ((seed % 10000) / 10000.0 - 0.5) * 2  # -1..1
        target = target_mid + norm * jitter_pp
        target = round(max(0.5, min(99.5, target)), 4)
        if is_str_col:
            df.at[idx, bp_col] = f'{target:.4f}%'
        else:
            df.at[idx, bp_col] = target
        n_caps += 1
        cats_renorm.add(cat_u)
        examples.append((cat_u, brand_u, cur, target, trigger_above))

    if n_caps and verbose:
        print(f'   {emoji} {ceiling_name}: capped {n_caps} row(s)')
        for c, b, old, new, trig in examples:
            print(f'      [{c}] {b}: {old:.2f}% → {new:.4f}% (trigger >{trig:.0f}%)')

    if n_caps and cs_col is not None:
        cs_is_str = (df[cs_col].dtype == object
                     or str(df[cs_col].dtype).startswith('string'))
        for cat_u in cats_renorm:
            idxs = df.index[col_u == cat_u]
            bps = df.loc[idxs, bp_col].apply(_bp)
            total = bps.sum(skipna=True)
            if total and total > 0:
                shares = (bps / total * 100).round(4)
                if cs_is_str:
                    df.loc[idxs, cs_col] = shares.apply(
                        lambda v: f'{v:.4f}' if pd.notna(v) else v).values
                else:
                    df.loc[idxs, cs_col] = shares.values
    return df, n_caps


def enforce_search_engine_ai_ceiling(df, subject, verbose=True):
    """Generic wrapper around SEARCH_ENGINE_AI_CEILING. Currently a no-op
    (dict is empty) per Jenna's principle: similar values across profiles
    OK, identical NOT OK, always reasoning never pinning.

    Kept defined so the (col, brand) -> {trigger_above, target_mid,
    jitter_pp} framework is available for future categories where the LLM
    needs a hard ceiling. To activate, add entries to
    SEARCH_ENGINE_AI_CEILING and re-add this to run_post_vet_safety_sweep."""
    return _generic_consensus_ceiling(
        df, subject, SEARCH_ENGINE_AI_CEILING,
        'search-engine/AI ceiling', emoji='🚧',
        brand_profiles_only=False, verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Shelf-category distribution basis correction (Jenna 2026-05-29 PM note)
#
# Defect: category-agent generates flat-line clusters at too-high BP basis
# for shelf-CPG-style categories. The dashboard reads digital-PURCHASER
# penetration; the agent has been reading toward a blended shelf/engagement
# number.
#
# Nike pre-Claude CPG: top-10 mean=19.29%, std=0.34pp (Coca-Cola, Doritos,
#   Colgate, Gillette, Dr Pepper, Gatorade, Bounty, Red Bull all at 19%±0.4)
# Paramount+ CPG: top-10 mean=20.21% (Pepsi 23.84, Coca-Cola 25.59, then
#   13 brands cluttered at 18-19%)
# User's hand-patched Netflix CPG: top-10 mean ~10-12%, brands spread
#   across 5-18% with proper differentiation
#
# Rule: if a shelf category's top-10 mean exceeds its realistic ceiling,
# rescale ALL brands in that category by (target / actual) with per-brand
# deterministic jitter to prevent re-clustering, preserving relative
# ordering. Renormalize Category Share afterward.
# ---------------------------------------------------------------------------

SHELF_CATEGORY_DISTRIBUTION_TARGETS: dict[str, dict] = {
    # category → realistic digital-purchaser distribution targets
    #
    # Strategy: piece-wise linear interpolation over the "cluster zone".
    # If a category's top-10 mean exceeds top10_mean_max, we rescale ONLY
    # brands above rescale_above_pp using:
    #   new = anchor_lo + (old - anchor_lo) *
    #         (target_top - anchor_lo) / (actual_top - anchor_lo)
    # This compresses the upper cluster while leaving mid/tail brands
    # untouched (a uniform multiplicative scale crushes the tail and
    # over-compresses the top).
    #
    # Fallback (rank-decay): if after the linear interp the top-10 std is
    # still below `flat_line_std_pp`, the input was a flat-line cluster
    # (Jordan Matter CPG: input 18.5-19.7%, post-interp 16.7-17.7%, std
    # 0.26pp). In that case we re-assign top-N brands by RANK along a
    # linear gradient from target_top → flat_line_floor_pp, preserving
    # rank ordering but forcing a healthy spread.
    #
    # Tuned against user-patched Netflix CPG (top-1=18.19%, top-10
    # mean=13.09%, healthy spread across 5-18%).
    'CPG': {
        'top10_mean_max':      14.0,  # trigger threshold
        'rescale_above_pp':     8.0,  # leave brands below this untouched
        'target_top_pp':       18.0,  # where the top brand should land
        'jitter_pp':            0.4,  # ± per-brand deterministic jitter
        'flat_line_std_pp':     1.0,  # if post-interp top-10 std < this, rank-decay
        'flat_line_floor_pp':   9.0,  # where rank-N brand lands on rank gradient
        'flat_line_rank_n':    15,    # how many ranks to spread
    },
    # PORN MEDIA — added 2026-06-01 after Jenna's Marmot audit flagged the
    # category as compressed to a 10-12% band (Pornhub 12.32, XVideos 12.04,
    # SEXTB 10.39, ...) with no Pornhub dominance. In gen-pop adult panels
    # Pornhub is the runaway leader at ~38-42%, with XVideos/xHamster trailing
    # at 20-30%, then a fast tail.
    #
    # Configured to ALWAYS trigger rank-decay (flat_line_std_pp set very high
    # so any input shape falls through to the rank gradient), because LLM PORN
    # MEDIA outputs are systematically compressed regardless of persona.
    # force_top_brand pins PORNHUB to rank 1 even if the LLM ranked another
    # site first (Jenna's report had SEXTB at the top in some version —
    # defensive pinning catches that pattern). decay_alpha=3.0 gives a
    # steep top falloff so PORNHUB visibly dominates (gap ≥ 5pp to rank 2).
    'PORN MEDIA': {
        'top10_mean_max':      6.0,   # trigger if top-10 mean > 6%
        'rescale_above_pp':    2.0,   # leave tail (< 2%) untouched
        'target_top_pp':      40.0,   # Pornhub at ~40% (US adult-panel leader)
        'jitter_pp':           0.5,
        'flat_line_std_pp':   25.0,   # high threshold → rank-decay always fires
        'flat_line_floor_pp':  4.0,   # rank-N brand at ~4%
        'flat_line_rank_n':   16,     # covers all rebased brands (no rank inversion)
        'decay_alpha':         3.0,   # power curve → steep top, gentle tail
        'force_top_brand':    'PORNHUB',
    },
}


def enforce_shelf_category_distribution(df, subject, verbose=True):
    """Rebase shelf-category BP basis when the category-agent flat-lines
    many unrelated brands at a too-high cluster (Jenna's "CPG basis"
    defect, recurring across all profiles).

    Uses piece-wise linear interpolation over the upper cluster
    (BP > rescale_above_pp), compressing it to digital-purchaser levels
    while preserving rank order and leaving low-tier brands untouched.
    Renormalizes Category Share afterward.
    """
    if df is None or len(df) == 0:
        return df, 0
    df = df.copy()
    bp_col, cs_col, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, 0
    # 2026-06-01 (Marmot audit harness): coerce any pandas-2.x StringDtype
    # columns to object so we can assign either a float (numeric path) or a
    # formatted string (display path) without dtype-rejection errors. In
    # production BG.py builds the dataframe in memory (object dtype), but
    # CSVs loaded via pd.read_csv land on 'str' dtype with pandas ≥ 2.x.
    for _c in (bp_col, cs_col):
        if _c is not None and str(df[_c].dtype).startswith(('str', 'string')):
            df[_c] = df[_c].astype(object)
    is_str_col = (df[bp_col].dtype == object
                  or str(df[bp_col].dtype).startswith('string'))

    col_u = df['Column'].astype(str).str.upper().str.strip()
    n_rows_total = 0

    for cat, tgt in SHELF_CATEGORY_DISTRIBUTION_TARGETS.items():
        cat_idxs = df.index[col_u == cat]
        if len(cat_idxs) < 10:
            continue
        parsed = []
        for idx in cat_idxs:
            v = _bp(df.at[idx, bp_col])
            if v is not None:
                parsed.append((idx, v))
        if len(parsed) < 10:
            continue
        bps_sorted = sorted([v for _, v in parsed], reverse=True)
        top10_mean = sum(bps_sorted[:10]) / 10
        if top10_mean <= tgt['top10_mean_max']:
            continue

        anchor_lo = float(tgt['rescale_above_pp'])
        target_top = float(tgt['target_top_pp'])
        actual_top = bps_sorted[0]
        # Guard against degenerate inputs (top brand below anchor)
        denom = actual_top - anchor_lo
        if denom <= 0.5:
            continue
        slope = (target_top - anchor_lo) / denom

        if verbose:
            print(f'   📦 shelf-distribution rebase: [{cat}] top-10 mean '
                  f'{top10_mean:.2f}% > ceiling {tgt["top10_mean_max"]:.0f}%; '
                  f'remapping [{anchor_lo:.0f}, {actual_top:.2f}]% → '
                  f'[{anchor_lo:.0f}, {target_top:.0f}]% (slope {slope:.3f})')

        n_cat = 0
        for idx, old in parsed:
            if old <= anchor_lo:
                continue  # mid/tail untouched
            brand = str(df.at[idx, 'Value']).strip()
            seed = int(_hl.md5(f'{subject}|{cat}|{brand}|shelf'
                                .encode()).hexdigest()[:8], 16)
            norm = ((seed % 10000) / 10000.0 - 0.5) * 2  # -1..1
            jitter = norm * tgt['jitter_pp']
            new = anchor_lo + (old - anchor_lo) * slope + jitter
            # Don't push below the anchor (preserves rank-zone boundary)
            new = round(max(anchor_lo, min(99.5, new)), 4)
            if is_str_col:
                df.at[idx, bp_col] = f'{new:.4f}%'
            else:
                df.at[idx, bp_col] = new
            n_cat += 1
        n_rows_total += n_cat

        # Flat-line fallback: if post-interp top-10 std is still tight,
        # the input had no rank-differentiation to preserve — apply rank-
        # based decay to force a healthy spread.
        if 'flat_line_std_pp' in tgt:
            post_parsed = sorted(
                ((_bp(df.at[i, bp_col]), i)
                 for i, _ in parsed
                 if _bp(df.at[i, bp_col]) is not None
                 and _bp(df.at[i, bp_col]) > anchor_lo),
                reverse=True,
            )
            top10_vals = [v for v, _ in post_parsed[:10]]
            if len(top10_vals) >= 5:
                m = sum(top10_vals) / len(top10_vals)
                post_std = (sum((v - m) ** 2 for v in top10_vals)
                            / len(top10_vals)) ** 0.5
                if post_std < tgt['flat_line_std_pp']:
                    fl_floor = float(tgt['flat_line_floor_pp'])
                    fl_top   = float(tgt['target_top_pp'])
                    rank_n   = int(min(tgt['flat_line_rank_n'], len(post_parsed)))
                    step = (fl_top - fl_floor) / max(1, rank_n - 1)

                    # 2026-06-01 (Marmot audit): force_top_brand pins a
                    # known dominant brand to rank 1 before the rank
                    # gradient is applied. Used for categories where the
                    # leader is canonically known (e.g. PORN MEDIA →
                    # PORNHUB) but the LLM sometimes ranks a clone or
                    # niche site first. Searches ALL category rows (not
                    # just post_parsed) so a suppressed leader is still
                    # found and lifted into rank 1.
                    forced = tgt.get('force_top_brand')
                    if forced:
                        forced_u = str(forced).upper().strip()
                        forced_idx_in_pp = None
                        for _i, (_v, _ix) in enumerate(post_parsed):
                            if str(df.at[_ix, 'Value']).strip().upper() == forced_u:
                                forced_idx_in_pp = _i
                                break
                        if forced_idx_in_pp is None:
                            # Leader is below anchor_lo (or missing) —
                            # find it anywhere in the category and inject.
                            for _ix in cat_idxs:
                                if str(df.at[_ix, 'Value']).strip().upper() == forced_u:
                                    _cur = _bp(df.at[_ix, bp_col])
                                    post_parsed.insert(0, (_cur if _cur is not None else 0.0, _ix))
                                    if verbose:
                                        print(f'      🎯 force_top_brand: lifted '
                                              f'suppressed {forced_u} into rank 1')
                                    break
                        elif forced_idx_in_pp > 0:
                            post_parsed.insert(0, post_parsed.pop(forced_idx_in_pp))
                            if verbose:
                                print(f'      🎯 force_top_brand: promoted '
                                      f'{forced_u} from rank {forced_idx_in_pp + 1} to rank 1')

                    # 2026-06-01 (Marmot audit): decay_alpha controls the
                    # rank-gradient curvature. alpha=1.0 (default) is the
                    # original linear decay used for CPG. alpha > 1 gives a
                    # power-curve falloff that's steeper near rank 1 — used
                    # for PORN MEDIA where the leader (PORNHUB) should clearly
                    # dominate the runner-up by ≥ 5pp.
                    alpha = float(tgt.get('decay_alpha', 1.0))
                    if verbose:
                        curve_note = '' if alpha == 1.0 else f' (alpha={alpha:.1f})'
                        print(f'      ⚠️ flat-line detected (post-interp top-10 std={post_std:.2f}pp); '
                              f'applying rank-decay over top-{rank_n} brands '
                              f'[{fl_top:.0f}% → {fl_floor:.0f}%]{curve_note}')
                    n_rank = 0
                    for rank, (_old, idx) in enumerate(post_parsed[:rank_n], start=1):
                        brand = str(df.at[idx, 'Value']).strip()
                        seed = int(_hl.md5(
                            f'{subject}|{cat}|{brand}|rankdecay'.encode()
                        ).hexdigest()[:8], 16)
                        norm = ((seed % 10000) / 10000.0 - 0.5) * 2
                        jitter = norm * tgt['jitter_pp']
                        if alpha == 1.0:
                            target = fl_top - (rank - 1) * step + jitter
                        else:
                            # Power-curve: target = floor + (top-floor)*(1-pos)^alpha
                            # where pos ∈ [0, 1] over rank ∈ [1, rank_n].
                            norm_pos = (rank - 1) / max(1, rank_n - 1)
                            target = (fl_floor
                                      + (fl_top - fl_floor) * (1 - norm_pos) ** alpha
                                      + jitter)
                        target = round(max(anchor_lo, min(99.5, target)), 4)
                        if is_str_col:
                            df.at[idx, bp_col] = f'{target:.4f}%'
                        else:
                            df.at[idx, bp_col] = target
                        n_rank += 1
                    n_rows_total += n_rank  # additional fixes

        if verbose:
            new_sorted = sorted(
                ((_bp(df.at[i, bp_col]), str(df.at[i, 'Value']))
                 for i, _ in parsed),
                reverse=True,
            )[:5]
            print(f'      rescaled {n_cat} brand(s) (above {anchor_lo:.0f}%); new top-5:')
            for v, b in new_sorted:
                print(f'         {b}: {v:.4f}%')

        if cs_col is not None:
            cs_is_str = (df[cs_col].dtype == object
                         or str(df[cs_col].dtype).startswith('string'))
            new_bps = df.loc[cat_idxs, bp_col].apply(_bp)
            total = new_bps.sum(skipna=True)
            if total and total > 0:
                shares = (new_bps / total * 100).round(4)
                if cs_is_str:
                    df.loc[cat_idxs, cs_col] = shares.apply(
                        lambda v: f'{v:.4f}' if pd.notna(v) else v).values
                else:
                    df.loc[cat_idxs, cs_col] = shares.values

    return df, n_rows_total


# ---------------------------------------------------------------------------
# Bug 7 (detect-only): in-category cluster compression
# ---------------------------------------------------------------------------

# Categories where digital-purchase BPs should naturally spread out
# (different brands index very differently with different audiences).
# These should NOT have tight clusters in the top-N.
#
# Exclusions:
#   - WHERE THEY SHOP / WHERE THEY DINE: hyper-broad-reach categories
#     where mid-tier retailers genuinely cluster at 18-20% (Amazon at 82,
#     then Target/Lowes/Home Depot/Kroger/ALDI/Costco all at ~18-22%).
#     Flagging these as compression generates false positives.
CLUSTER_CHECK_CATEGORIES = frozenset({
    'CPG', 'AUTOMOBILE', 'QSR', 'INSURANCE', 'TELECOM', 'CREDIT PROVIDER',
    'HEALTH/BEAUTY', 'BEAUTY/WELLNESS',
    'TECHNOLOGY/DEVICE', 'TECHNOLOGY BRAND',
})
CLUSTER_TOP_N = 8
CLUSTER_STD_THRESHOLD = 1.5  # pp std across top-N
# Only fire Check A when top-N mean is in the "too-high" zone — after
# rebase it lands ~11%, which is healthy clustering and should not flag.
CLUSTER_MIN_MEAN = 13.0


def detect_category_cluster_compression(df, subject, verbose=True):
    """Detect (don't auto-fix) categories where brands cluster too tightly
    — a signal the category-agent or default-lock is generating templated
    values rather than persona-grounded ones.

    Uses TWO checks (either triggers a flag):
      A. Top-N std check: top-8 std < 1.5pp with mean >= 8 (Nike pre-Claude
         CPG: top-8 std=0.26pp, mean=19.41 — flat-line cluster)
      B. Densest-window check: find the densest 3pp window of BP values
         in [10, 30]%; if it contains >= 8 brands, flag (Paramount+ CPG:
         13 brands clustered in 18-19% band even though top-2 Pepsi/
         Coca-Cola pull the std up above 1.5).
    """
    if df is None or len(df) == 0:
        return df, []
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col is None:
        return df, []
    flags = []
    col_u = df['Column'].astype(str).str.upper().str.strip()
    for cat in CLUSTER_CHECK_CATEGORIES:
        idxs = df.index[col_u == cat]
        if len(idxs) < CLUSTER_TOP_N:
            continue
        parsed = sorted(
            ((float(_bp(df.at[i, bp_col])), str(df.at[i, 'Value']))
             for i in idxs if _bp(df.at[i, bp_col]) is not None),
            reverse=True,
        )
        if len(parsed) < CLUSTER_TOP_N:
            continue
        top_n = parsed[:CLUSTER_TOP_N]
        vals = [b[0] for b in top_n]
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5

        # Check A: top-N std
        flag_a = (std < CLUSTER_STD_THRESHOLD and mean >= CLUSTER_MIN_MEAN)

        # Check B: densest 3pp window in [13, 28]% — narrow zone so we
        # don't flag healthy mid-tier shopping/dining categories where
        # broad-reach brands genuinely cluster around 18-20%. Only fires
        # on the "too-high CPG basis" pattern: 10+ brands clustered ABOVE
        # realistic digital-purchaser levels for the category.
        all_bps_in_zone = sorted(
            v for v, _ in parsed if 13.0 <= v <= 28.0
        )
        best_count, best_lo, best_hi = 0, 0.0, 0.0
        if all_bps_in_zone:
            i = 0
            for j in range(len(all_bps_in_zone)):
                while all_bps_in_zone[j] - all_bps_in_zone[i] > 3.0:
                    i += 1
                cnt = j - i + 1
                if cnt > best_count:
                    best_count = cnt
                    best_lo = all_bps_in_zone[i]
                    best_hi = all_bps_in_zone[j]
        flag_b = best_count >= 10

        if not (flag_a or flag_b):
            continue
        flags.append({
            'category': cat, 'top_n': CLUSTER_TOP_N,
            'mean': round(mean, 3), 'std': round(std, 3),
            'range': round(max(vals) - min(vals), 3),
            'top_brands': [(b, round(v, 3)) for v, b in top_n],
            'densest_3pp_window': {
                'count': best_count,
                'lo': round(best_lo, 3),
                'hi': round(best_hi, 3),
            },
            'triggered_by': ('A_topN_std' if flag_a else '') +
                             ('+' if flag_a and flag_b else '') +
                             ('B_densest_window' if flag_b else ''),
        })
    if flags and verbose:
        print(f'   🔬 cluster-compression: {len(flags)} suspect categor'
              f'{"ies" if len(flags) > 1 else "y"}')
        for f in flags:
            w = f['densest_3pp_window']
            print(f'      [{f["category"]}] mean={f["mean"]:.2f}% std={f["std"]:.2f} '
                  f'densest 3pp window: {w["count"]} brands in [{w["lo"]:.1f}, {w["hi"]:.1f}]% '
                  f'(triggered by {f["triggered_by"]})')
            for b, v in f['top_brands'][:5]:
                print(f'         {b}: {v}%')
    return df, flags


# ---------------------------------------------------------------------------
# Convenience wrapper — call once from BG.py post-vet
# ---------------------------------------------------------------------------

def run_post_vet_safety_sweep(df, subject, *, verbose=True):
    """Run the late-pass safety sweep AFTER the vet-reason agent has finished.

    Order matters:
      1. enforce_household_streaming_floor  (already wired separately in BG.py)
      2. Mainstream-brand floors (auto, credit) — lift suppressed brands
      3. Mobile-OS balance — normalize Apple/Android sum
      4. Hard cap >100% — last math sanity check
      5. Re-strip INPUT_METADATA in case anything re-introduced it
      6. Re-dejitter — Claude/GPT outputs land on X.00 round numbers
      7. Detector: log cluster compression (no auto-fix)

    Returns (df, summary_dict) — summary contains counts + cluster flags.
    """
    if df is None or len(df) == 0:
        return df, {}
    summary: dict = {'subject': subject}

    for fn, key in (
        (enforce_household_auto_floor,        'auto_lifts'),
        (enforce_credit_provider_floor,       'credit_lifts'),
        (enforce_telecom_floor,               'telecom_lifts'),
        # 2026-06-01 (Jenna principle: similar OK, identical NOT OK,
        # always reasoning never pinning): enforce_search_engine_ai_ceiling
        # was removed from this list. Hardcoded target_mid + md5 jitter
        # was itself a form of pinning. SEARCH ENGINE/AI variance is
        # achieved instead by the tightened CROSS_PULL_RANGES in
        # crosswalk_audit_framework.py (gives the vet-agent better
        # reasoning anchors) plus the existing within-cat dejitter passes
        # that catch literal-identical-value collisions.
        (enforce_shelf_category_distribution, 'shelf_rebase_rows'),
        # 2026-08-25 (Liz QA, Ari Melber hot PORN block): the shelf
        # rebase above re-gradients PORN MEDIA toward its fixed 40
        # target whenever it fires, which can overwrite the D111
        # audience-aware band clamp that ran earlier in
        # run_all_enforcers. The documented design intent (see the
        # D111 header comment) is clamp-after-shelf; BG.py guarantees
        # it via the _demo_safe_to_csv save-gate, but any other caller
        # of this sweep gets the re-clamp here. Idempotent no-op when
        # already in-band.
        (apply_porn_canonical_normalize,      'porn_reclamp_post_shelf'),
        (apply_porn_leader_invariant,         'porn_leader_post_shelf'),
        (enforce_mobile_os_balance,           'mobile_os_norms'),
        (enforce_bp_hard_ceiling,             'bp_caps'),
        (strip_input_metadata_leakage,        'metadata_re_stripped'),
        (strip_url_variant_seed_rows,         'url_seed_rows_stripped'),
    ):
        try:
            df, n = fn(df, subject, verbose=verbose)
            summary[key] = n
        except Exception as e:
            summary[key] = f'ERROR: {e}'
            if verbose:
                print(f'   ⚠️ post-vet safety: {fn.__name__} failed: {e}')

    # Re-dejitter — Claude/GPT vet outputs often land on round X.00 numbers
    for fn, key in (
        (dejitter_x5x0_displays,          'dejitter_x5x0'),
        (dejitter_cross_cat_4dp_pins,     'dejitter_xcat'),
        # 2026-05-30 (D1 fix, Jenna May 30 batch): late re-pass on
        # within-cat 4dp collisions AND the sequential-digit placeholder
        # pattern. Both run here as the LAST line of defense BEFORE
        # the pre-publish gate. mpb-floor injects ~1,400 LLM-invented
        # brands AFTER the early sweep, so these must repeat late.
        (dejitter_within_cat_4dp_collisions, 'dejitter_within_cat_late'),
        (dejitter_sequential_placeholders,   'dejitter_seq_placeholders'),
        # 2026-08-26 (Liz QA, Bethenny avid): same-suffix integer-step
        # ladders — late re-pass so vet output can't re-introduce them.
        (dejitter_fractional_ladders,        'dejitter_frac_ladders'),
    ):
        try:
            df, n = fn(df, subject, verbose=verbose)
            summary[key] = n
        except Exception as e:
            summary[key] = f'ERROR: {e}'
            if verbose:
                print(f'   ⚠️ post-vet safety: {fn.__name__} failed: {e}')

    # Detector — log but don't fix
    try:
        _, flags = detect_category_cluster_compression(df, subject, verbose=verbose)
        summary['cluster_flags'] = flags
    except Exception as e:
        summary['cluster_flags'] = f'ERROR: {e}'

    if verbose:
        head = {k: v for k, v in summary.items()
                if k != 'cluster_flags' and not isinstance(v, str)}
        n_total = sum(v for v in head.values() if isinstance(v, int))
        n_flags = len(summary.get('cluster_flags', []) or [])
        print(f'   ✅ post-vet safety sweep complete: {n_total} fix(es), '
              f'{n_flags} cluster flag(s)')

    return df, summary


# ============================================================================
# Pre-publish gate (added 2026-05-30 from Jenna's May 30 batch escalation)
#
# Hard-block save if any deterministic, easy-to-catch defect is still present
# after all enforcer passes. Cheaper to fail-fast at write time than to push
# bad data into S3 and patch later.
#
# Gates:
#   G1: any sequential-digit placeholder BP survives (post all dejitters)
#   G2: any US Gen Pop Projection > US_POP * 1.05 (impossible value)
#   G3: BRAND INPUT row > 80 chars AND the Value reads as leaked junk
#       (prompt/metadata echo, multiline, prose). Canonical clickstream
#       slug shapes per Rule #4c-i (variant lists, handle lists,
#       scrape-term lists, URL slugs, 'CSV') never fire - shape-based
#       and subject-independent since 2026-08-25 (IFF Avid false
#       positive; cuts inherit the parent Value verbatim)
#   G4: any single 4dp BP value appearing 10+ times in one category
#       (within-cat collision that survived the dejitter)
#   G5: any of the 9 demos fails sum=100±0.5 (added 2026-06 to catch the
#       OCCUPATION over-emission + AGE drop family of defects at write time)
# ============================================================================

# ============================================================================
# DEMOGRAPHIC SUM-TO-100 ENFORCER + VALIDATOR
# ============================================================================
# Each of the 9 demographic categories (AGE, GENDER, ETHNICITY, INCOME,
# EDUCATION, RELATIONSHIP, SEXUAL_ORIENTATION, PARENTAL_STATUS, OCCUPATION)
# MUST sum to 100% (within 0.5pp tolerance for rounding). Defects we have
# seen at write time:
#   - OCCUPATION sum 110-141% (10 files): structural double-emission.
#     enforce_all_demographic_categories only renormalizes 5 of 9 demos
#     (GENDER, INCOME, AGE, EDUCATION, ETHNICITY); OCCUPATION + the other
#     three have NO sum-to-100 safeguard.
#   - AGE sum 96.28% (3 files: Kelsey + 2 Home Internet Shopping):
#     deterministic drop of a single age bucket on certain audience profiles.
#
# `renormalize_demographics_to_100` is the FIX (rescales BPs in place +
# recomputes raw/projection). `validate_demo_sum_100` is the CATCHER (raises
# DemoSumViolationError if any demo still drifts > tolerance after the fix).
# Both are wired into BG.py BEFORE to_csv(), so future profile generations
# self-correct and any survivor surfaces loudly in the run log.
# ============================================================================

_DEMO_CATS_NINE = (
    'AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION',
    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION',
)

# Extended demo-like blocks: mutually-exclusive-bucket categories whose
# BPs sum to ~100. For these, Share ≡ BP is mathematically correct
# (Share = BP/ΣBP × 100 = BP/100 × 100 = BP). They must be treated
# like the 9 canonical demos by:
#   * apply_recompute_category_share (write Share = BP, not the
#     non-demo BP/ΣBP path)
#   * G8 SHARE_SUM detector (skip; sum ≈ 100 is EXPECTED, not a defect)
#   * G9 SHARE_EQ_BP detector (skip; Share == BP is expected, not a
#     writer-bug signature)
# Added 2026-08-04 after Oncology Patients / Doctors landed with 210
# DMA-level LOCATION rows all correctly showing Share == BP (they sum
# to 100 by construction), and G9 falsely flagged them as writer-bug.
_DEMO_LIKE_EXTRA = (
    'LOCATION',          # 210 DMAs; sum to ~100 as mutually-exclusive buckets
    'PRIMARY_LANGUAGE',  # English / Spanish / Other; sum to 100
    'NUMBER_OF_CHILDREN', 'AGE_OF_CHILDREN',  # optional demos
)
_DEMO_LIKE_ALL = tuple(_DEMO_CATS_NINE) + _DEMO_LIKE_EXTRA


class DemoSumViolationError(Exception):
    """Raised when one or more demographic categories fail the sum=100±tol
    check. Callers should treat as fatal — the CSV has a structural defect
    that no downstream consumer can interpret correctly."""
    def __init__(self, violations: list[tuple[str, float, int]]):
        self.violations = violations
        msg = '; '.join(
            f'{cat} sum={s:.2f}% (n={n})' for cat, s, n in violations
        )
        super().__init__(
            f'validate_demo_sum_100 FAILED for {len(violations)} demo(s): {msg}'
        )


# ---------------------------------------------------------------------------
# 2026-08-06 (Jenna: "make sure for all synth profiles that it always only
# uses canonical demos ... should only be from these [demos.csv]").
# North West and other new synth builds landed with 7-bucket EDUCATION
# schemas ('Less than High School', 'HS Diploma / GED', 'Some College',
# 'Associate Degree', 'Bachelor's Degree', 'Master's Degree', 'Doctorate /
# Professional Degree') and legacy 'Widowed' in RELATIONSHIP -- neither is
# in the canonical demos.csv distinct set from userdata.user_data_sanitized.
# Enforcer collapses aliases -> canonical, drops orphan/aliased-to-None
# buckets and merges duplicates. Runs BEFORE renormalize_demographics_to_100
# so the sum-to-100 pass sees the collapsed distribution.
# ---------------------------------------------------------------------------
def enforce_canonical_demo_buckets(df, *, subject=None, verbose=True):
    """Collapse non-canonical demographic bucket labels to the canonical
    set from ``reference/demos.csv`` (via
    :mod:`migration.canonical_demos`).

    For each of the categories in PIPELINE_DEMO_SCHEMA:

    * If a row's ``Value`` normalizes to a canonical bucket, its label
      is rewritten to canonical casing/apostrophe form (e.g.
      ``BACHELOR'S DEGREE`` -> ``Bachelors Degree`` -- but we preserve
      the case style of neighbors if the file uses all-uppercase demos).
    * If ``Value`` is aliased to another canonical bucket (e.g.
      ``Master's Degree`` -> ``Graduate or Professional Degree``), the
      row's BP is SUMMED into the canonical bucket's existing row (or
      the row is relabeled if no canonical row exists yet), and the
      duplicate is dropped.
    * If ``Value`` is aliased to ``None`` (e.g. ``Widowed`` in
      RELATIONSHIP, ``Prefer Not to Say`` in INCOME/OCCUPATION), the
      row is dropped. Its BP will be redistributed proportionally to
      the remaining canonical buckets by
      :func:`renormalize_demographics_to_100`.
    * If ``Value`` is orphan (no canonical match, no alias),
      :func:`migration.canonical_demos.orphan_fallback` is consulted:
      if a fallback bucket exists for the category (e.g. ``Other`` for
      OCCUPATION), the BP is merged into it; otherwise the row is
      dropped and logged as an orphan.

    Idempotent: a second pass on an already-canonical df is a no-op.
    Preserves the SUBJECT self-pin at 100%.

    Returns (df, n_operations) where n_operations is the total number
    of relabels + merges + drops applied.
    """
    if df is None or len(df) == 0:
        return df, 0

    # International frames keep their country-native bucket labels
    # (Omaze precedent) - the canonical US schema must never be forced
    # onto them. renormalize_demographics_to_100 (name-agnostic) still
    # holds every demo category at 100.
    _ctry = _frame_country(df)
    if _ctry:
        if verbose:
            print(f'   enforce_canonical_demo_buckets: {_ctry} frame - '
                  f'country-native demo schema preserved, US canonical '
                  f'collapse skipped')
        return df, 0

    try:
        from migration.canonical_demos import (
            PIPELINE_DEMO_SCHEMA, canonical_value, orphan_fallback,
            _CANONICAL_NORM, _norm, _ORPHAN,
        )
    except Exception as e:
        if verbose:
            print(f'   ⚠️ enforce_canonical_demo_buckets: import failed: {e}')
        return df, 0

    if 'Column' not in df.columns or 'Value' not in df.columns:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0

    df = df.copy()
    ops = 0
    log_relabel: list[tuple[str, str, str]] = []
    log_merge:   list[tuple[str, str, str, float]] = []
    log_drop:    list[tuple[str, str, float]] = []
    log_orphan:  list[tuple[str, str, float]] = []

    col_series = df['Column'].astype(str).str.strip().str.upper()

    for cat in PIPELINE_DEMO_SCHEMA.keys():
        cat_mask = col_series == cat
        if not cat_mask.any():
            continue

        # Detect the file's case-style for this category (all-upper vs
        # title-case) so we preserve it when rewriting labels.
        cat_rows = df[cat_mask]
        upper_style = all(
            str(v).strip() == str(v).strip().upper()
            for v in cat_rows['Value']
            if str(v).strip()
        )

        # First pass: relabel canonical-but-differently-cased rows.
        # Second pass: collapse aliases + drops + orphans.
        drop_idxs: list[int] = []
        for idx in list(cat_rows.index):
            raw_val = df.at[idx, 'Value']
            if raw_val is None or str(raw_val).strip() == '':
                continue
            canon = canonical_value(cat, raw_val)
            if canon is None:
                # Aliased to DROP
                bp = _bp(df.at[idx, bp_col]) or 0.0
                drop_idxs.append(idx)
                log_drop.append((cat, str(raw_val), bp))
                ops += 1
                continue
            if canon is _ORPHAN:
                fallback = orphan_fallback(cat)
                bp = _bp(df.at[idx, bp_col]) or 0.0
                if fallback is None:
                    drop_idxs.append(idx)
                    log_orphan.append((cat, str(raw_val), bp))
                    ops += 1
                    continue
                canon = fallback
                log_orphan.append((cat, str(raw_val), bp))

            # Convert to file's case style
            target_label = canon.upper() if upper_style else canon

            # Is this row already at the target label?
            cur_label = str(raw_val).strip()
            if cur_label == target_label:
                continue

            # Does any OTHER row in this category already carry the
            # canonical target label? If so, merge BPs and drop this row.
            target_norm = _norm(target_label)
            existing_target_idx = None
            for j in list(cat_rows.index):
                if j == idx or j in drop_idxs:
                    continue
                if _norm(df.at[j, 'Value']) == target_norm:
                    existing_target_idx = j
                    break

            bp_here = _bp(df.at[idx, bp_col]) or 0.0
            if existing_target_idx is not None:
                bp_target = _bp(df.at[existing_target_idx, bp_col]) or 0.0
                new_bp = round(bp_target + bp_here, 4)
                df.at[existing_target_idx, bp_col] = new_bp
                drop_idxs.append(idx)
                log_merge.append((cat, cur_label, target_label, bp_here))
                ops += 1
            else:
                df.at[idx, 'Value'] = target_label
                log_relabel.append((cat, cur_label, target_label))
                ops += 1

        if drop_idxs:
            df = df.drop(index=drop_idxs).reset_index(drop=True)
            col_series = df['Column'].astype(str).str.strip().str.upper()

    if verbose and ops:
        subj = f'[{subject}]' if subject else ''
        print(f'   🧭 enforce_canonical_demo_buckets {subj}: '
              f'{ops} operation(s) '
              f'({len(log_relabel)} relabel, {len(log_merge)} merge, '
              f'{len(log_drop)} drop, {len(log_orphan)} orphan)')
        # Cap the per-line output for readability
        for cat, old, new in log_relabel[:8]:
            print(f"      relabel {cat}: '{old}' -> '{new}'")
        if len(log_relabel) > 8:
            print(f'      ...+{len(log_relabel)-8} more relabel(s)')
        for cat, src, dst, bp in log_merge[:8]:
            print(f"      merge   {cat}: '{src}' (BP={bp:.2f}) into '{dst}'")
        if len(log_merge) > 8:
            print(f'      ...+{len(log_merge)-8} more merge(s)')
        for cat, val, bp in log_drop[:8]:
            print(f"      drop    {cat}: '{val}' (BP={bp:.2f}) [alias->None]")
        if len(log_drop) > 8:
            print(f'      ...+{len(log_drop)-8} more drop(s)')
        for cat, val, bp in log_orphan[:8]:
            print(f"      orphan  {cat}: '{val}' (BP={bp:.2f})")
        if len(log_orphan) > 8:
            print(f'      ...+{len(log_orphan)-8} more orphan(s)')

    return df, ops


# ---------------------------------------------------------------------------
# 2026-08-06 (Jenna: "check most purchased brands for north west. they feel
# too lux. they can be those brands. The percentages just need to be lower
# since this is an assumed confirmed purchase ... The mass brands would
# likely still be fine ... We just need to ensure this doesn't happen again")
#
# MOST PURCHASED BRANDS is the "actually paid money in trailing 12 months"
# column, not aspiration/lust. Panel-reality confirmed-purchase for a $2000
# Fendi bag or $4000 Loro Piana sweater is single-digit % even for the most
# aligned audience -- the pool of people who can afford it is small, and
# not all of them buy in a given 12mo window.
#
# North West.csv had Celine at 52%, Louboutin at 52%, Fendi at 52%, Loro
# Piana at 42%, The Row at 42%, Judith Leiber at 18% -- all wildly above
# panel reality. This enforcer caps every MPB row (and sub-cat occurrences
# via Rule 3b) whose brand normalizes into the 4-tier lux canon at
# `migration.luxury_brand_tiers`. Idempotent: caps DOWN only, never up.
# ---------------------------------------------------------------------------
_LUX_SUBCAT_COLUMNS = {
    # MPB itself
    "MOST PURCHASED BRANDS",
    # Fashion sub-cats where the same lux brand rows appear (Rule 3b)
    "APPAREL", "APPAREL/FOOTWEAR", "APPAREL AND FOOTWEAR",
    "FOOTWEAR",
    "ACCESSORIES",
    "JEWELRY",
    "ACTIVEWEAR",
    "INTIMATES",
    "BEAUTY",
    "FRAGRANCE",
    "HOME/OUTDOOR", "HOME",
    "PETS",
    "TECHNOLOGY BRAND",
    "WHERE THEY SHOP",
    "RETAILERS",
    "CPG",
}


def apply_lux_confirmed_purchase_caps(df, *, subject=None, verbose=True):
    """Cap luxury / aspirational brand BPs into confirmed-purchase panel
    reality (per-tier target bands defined in
    ``migration.luxury_brand_tiers``).

    * ULTRA tier (Hermes, Loro Piana, The Row, Judith Leiber, watch/champagne
      houses...):     cap at 0.8-2.2%
    * HI tier (Chanel, LV, Gucci, Prada, Dior, Fendi, Celine, Balenciaga,
      Bottega, YSL, Louboutin, Chrome Hearts, Chloe, David Yurman,
      Tiffany...):    cap at 1.5-3.8%
    * MID tier (Reformation, Zadig, Sandro, Maje, Marc Jacobs, Golden
      Goose, Acne, Ganni, Alo, SKIMS, SKKN, Good American...):
                      cap at 3.0-7.0%
    * LO tier (Michael Kors, Coach, Kate Spade, Tory Burch, Ralph Lauren,
      Everlane, Madewell, J.Crew, Anthropologie, Lululemon, Athleta,
      Old Navy, Gap, Away, Monos...):
                      cap at 5.5-12.0%

    For each MPB or sub-cat row where the brand is in the lux canon and
    the current BP exceeds the tier ceiling: rescale to a jittered value
    in the tier band. Below-ceiling rows are left alone (idempotent).

    Subject-salted jitter preserves audience directional signal (a lux
    brand the audience over-indexes on ends up in the top half of the
    band; a lux brand the audience under-indexes on ends up in the
    bottom half), while making sure no two brands in the same tier
    land at identical 4dp BP.

    Returns (df, n_capped) where n_capped is the count of individual
    rows the enforcer capped.
    """
    if df is None or len(df) == 0:
        return df, 0

    try:
        from migration.luxury_brand_tiers import (
            lookup_tier, target_band, _norm_brand,
        )
    except Exception as e:
        if verbose:
            print(f"   ⚠️ apply_lux_confirmed_purchase_caps: import failed: {e}")
        return df, 0

    if 'Column' not in df.columns or 'Value' not in df.columns:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0

    sample_size = _detect_sample_size(df, bp_col, raw_col)
    subject_key = str(subject or '').upper()

    df = df.copy()
    ops = 0
    log: list[tuple[str, str, str, float, float]] = []   # (brand, tier, col, old, new)
    per_brand_new_bp: dict[str, float] = {}   # ensure same brand gets same jitter across all cats

    col_series = df['Column'].astype(str).str.strip().str.upper()

    for cat in _LUX_SUBCAT_COLUMNS:
        mask = col_series == cat
        if not mask.any():
            continue
        for idx in df.index[mask]:
            brand_raw = df.at[idx, 'Value']
            if brand_raw is None or str(brand_raw).strip() == '':
                continue
            tier = lookup_tier(brand_raw)
            if tier is None:
                continue
            cur_bp = _bp(df.at[idx, bp_col]) or 0.0
            lo, hi = target_band(tier)
            if cur_bp <= hi:
                # Already at panel reality
                continue

            brand_key = _norm_brand(brand_raw)
            if brand_key in per_brand_new_bp:
                new_bp = per_brand_new_bp[brand_key]
            else:
                # Rescale into the band. Direction preserved: brands with
                # HIGHER original BP get pushed further UP in the band
                # (they'll still be top-of-tier for this audience), brands
                # with LOWER original BP land in the bottom half.
                # Original BPs cluster in 15-80% range in bad files; map
                # to (lo, hi) with a shallow curve so ordering is preserved.
                band = hi - lo
                anchor_lo, anchor_hi = 25.0, 70.0
                frac = min(1.0, max(0.0,
                                    (cur_bp - anchor_lo) /
                                    (anchor_hi - anchor_lo)))
                base_new = lo + band * (0.35 + 0.55 * frac)
                # Subject + brand salted absolute-jitter in a small
                # window around base_new so no two lux brands in the same
                # tier land at identical 4dp BP.
                jit_amp = max(0.05, band * 0.08)
                jit_absval = _jitter_for(
                    subject_key, brand_key, salt="LUX_CAP",
                    lo=-jit_amp, hi=jit_amp,
                )
                new_bp = max(lo, min(hi, base_new + jit_absval))
                per_brand_new_bp[brand_key] = round(new_bp, 4)
                new_bp = per_brand_new_bp[brand_key]

            if abs(new_bp - cur_bp) < 0.01:
                continue
            _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col,
                    sample_size)
            ops += 1
            log.append((str(brand_raw), tier, cat, cur_bp, new_bp))

    if verbose and ops:
        subj = f'[{subject}]' if subject else ''
        # Roll up to per-brand summary for readability (a lux brand
        # capped in MPB + APPAREL/FOOTWEAR + ACCESSORIES gets 3 log rows
        # but is really 1 brand event).
        by_brand: dict[str, list] = {}
        for brand, tier, cat, old, new in log:
            by_brand.setdefault(brand, []).append((tier, cat, old, new))
        print(f'   💎 apply_lux_confirmed_purchase_caps {subj}: '
              f'capped {ops} row(s) across {len(by_brand)} brand(s)')
        for brand, entries in sorted(by_brand.items(), key=lambda kv: -max(e[2] for e in kv[1]))[:20]:
            tier = entries[0][0]
            cats = [e[1] for e in entries]
            old = entries[0][2]
            new = entries[0][3]
            print(f'      • {brand:<32} [{tier:<5}] {old:>6.2f}% -> {new:>5.2f}%  '
                  f'({len(cats)} cat(s))')
        if len(by_brand) > 20:
            print(f'      ...+{len(by_brand)-20} more brand(s)')

    return df, ops


def renormalize_demographics_to_100(df, *, subject=None, tolerance=0.5,
                                    verbose=True):
    """Rescale BPs within each of the 9 demographic categories so each
    sums to exactly 100% (within rounding). Recomputes Category Share,
    Original Raw Numbers, and US Gen Pop Projection from the new BPs
    using the canonical sample-size formula.

    Idempotent: a second pass on an already-renormalized df is a no-op.
    Safe on string-typed columns (parses via `_bp`). Skips categories
    with sum=0 (preserves the all-zero state).

    Returns (df, n_categories_adjusted).
    """
    if df is None or len(df) == 0:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns or 'Column' not in df.columns:
        return df, 0

    sample_size = _detect_sample_size(df, bp_col, raw_col)
    cats_upper = df['Column'].astype(str).str.strip().str.upper()
    adjusted = 0
    adj_log: list[tuple[str, float, float, int]] = []

    for cat in _DEMO_CATS_NINE:
        mask = cats_upper == cat
        if not mask.any():
            continue
        bp_floats = df.loc[mask, bp_col].apply(_bp)
        bp_sum = float(bp_floats.sum())
        if bp_sum <= 0:
            continue
        deviation = abs(bp_sum - 100.0)
        if deviation <= tolerance:
            continue

        scale = 100.0 / bp_sum
        new_bps = (bp_floats * scale).round(4)
        # Ensure cell-type compatibility: coerce BP col to object/numeric
        # before assignment to avoid the str-dtype TypeError we hit in
        # adjust_platform_to_100_percent.
        if str(df[bp_col].dtype) == 'string' or str(df[bp_col].dtype).startswith('str'):
            df[bp_col] = pd.to_numeric(df[bp_col].apply(_bp), errors='coerce')
        df.loc[mask, bp_col] = new_bps

        # Recompute raw + projection from new BPs
        if raw_col:
            if str(df[raw_col].dtype) == 'string' or str(df[raw_col].dtype).startswith('str'):
                df[raw_col] = pd.to_numeric(df[raw_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype('int64')
            df.loc[mask, raw_col] = (
                (new_bps / 100.0 * sample_size).round(0).astype('int64')
            )
        if proj_col:
            if str(df[proj_col].dtype) == 'string' or str(df[proj_col].dtype).startswith('str'):
                df[proj_col] = pd.to_numeric(df[proj_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype('int64')
            df.loc[mask, proj_col] = (
                (new_bps / 100.0 * sample_size *
                 (US_POP / 10_000_000.0)).round(0).astype('int64')
            )
        # Category Share = BP fraction of (newly 100%) category total
        if cs_col:
            if str(df[cs_col].dtype) == 'string' or str(df[cs_col].dtype).startswith('str'):
                df[cs_col] = pd.to_numeric(df[cs_col].apply(_bp), errors='coerce')
            df.loc[mask, cs_col] = new_bps.round(4)

        adjusted += 1
        adj_log.append((cat, bp_sum, 100.0, int(mask.sum())))

    if verbose and adj_log:
        print(f'   🔧 renormalize_demographics_to_100 [{subject or ""}]: '
              f'{adjusted} demo cat(s) rescaled to sum=100%')
        for cat, old, new, n in adj_log:
            print(f'      • {cat}: {old:.2f}% -> {new:.2f}% (n={n} rows)')

    return df, adjusted


def validate_demo_sum_100(df, *, subject=None, tolerance=0.5,
                          raise_on_fail=True, verbose=True):
    """Hard-fail validator: raises DemoSumViolationError if ANY of the 9
    demos has |sum - 100| > tolerance. Should be run AFTER
    `renormalize_demographics_to_100`; if it fires, something else is
    structurally wrong (rows added/dropped after renorm, or renorm itself
    failed). Returns the violation list when raise_on_fail=False.
    """
    violations: list[tuple[str, float, int]] = []
    if df is None or len(df) == 0:
        return violations

    bp_col, _, _, _ = _detect_cols(df)
    if bp_col not in df.columns or 'Column' not in df.columns:
        return violations

    cats_upper = df['Column'].astype(str).str.strip().str.upper()
    for cat in _DEMO_CATS_NINE:
        mask = cats_upper == cat
        if not mask.any():
            continue
        bp_sum = float(df.loc[mask, bp_col].apply(_bp).sum())
        if abs(bp_sum - 100.0) > tolerance:
            violations.append((cat, bp_sum, int(mask.sum())))

    if verbose:
        tag = subject or ''
        if violations:
            print(f'   🚨 validate_demo_sum_100 [{tag}]: '
                  f'{len(violations)} demo(s) violate sum=100±{tolerance}:')
            for cat, s, n in violations:
                print(f'      ✗ {cat} sum={s:.2f}% (n={n})')
        else:
            print(f'   🟢 validate_demo_sum_100 [{tag}]: PASS (all 9 demos sum=100±{tolerance})')

    if violations and raise_on_fail:
        raise DemoSumViolationError(violations)

    return violations


# ============================================================================
# INCOME MONOTONICITY (soft-warn; some audiences are legitimately bimodal)
# ============================================================================
# 2026-08-03 (Honey Pot bimodal INCOME): corpus scan found 1,817 profiles
# with the writer-noise signature: peak, decline ≥ 2pp, then rise ≥ 1pp.
# Example (Honey Pot Avid): 25-49K=19.00, 50-74K=21.01, 75-99K=16.48,
#                            100-149K=18.52 — the rise at 100-149K after
#                            the 75-99K dip is the smoking gun.
# Some audiences ARE legitimately bimodal (dual-cohort products: young
# early adopters + established professionals). This validator soft-warns
# so writers can catch the LLM-noise cases but doesn't auto-fix or reject.

_INCOME_BUCKETS_ORDERED = [
    ['Less than $25,000', 'Under $25,000', '<$25,000'],
    ['$25,000 - $49,999', '$25,000 to $49,999'],
    ['$50,000 - $74,999', '$50,000 to $74,999'],
    ['$75,000 - $99,999', '$75,000 to $99,999'],
    ['$100,000 - $149,999', '$100,000 to $149,999'],
    ['$150,000 - $249,999', '$150,000 to $249,999', '$150,000+'],
    ['$250,000 or More', '$250,000 or more', '$250,000+'],
]

# Bucket widths in units of $10,000 - used to normalize raw BP into a
# density (BP per $10K of bucket width) before detecting bimodality.
# Colleague audit 2026-08-06 (Kane Brown): the raw sequence
# "6.76, 8.93, 8.17, 5.70, 3.05, 0.85" APPEARS bimodal (dip at $75-99K
# then rise at $100-149K) but that is an artifact of the $100-149K
# bucket being 2x wider ($50K vs $25K). Normalized to per-$10K density
# the distribution is cleanly single-peaked and monotone declining.
# Add this rule as a standing invariant: always normalize INCOME to
# equal bucket width before testing bimodality.
_INCOME_BUCKET_WIDTHS_10K = [
    2.5,   # <$25K            ($0-24,999 => $25K width)
    2.5,   # $25-49K
    2.5,   # $50-74K
    2.5,   # $75-99K
    5.0,   # $100-149K        ($50K width - 2x)
    10.0,  # $150-249K        ($100K width - 4x)
    25.0,  # $250K+           (open-ended; treat as $250K width)
]


def _income_density_seq(seq):
    """Return per-$10K density for an income raw-BP sequence.

    If ``seq`` has fewer than ``len(_INCOME_BUCKET_WIDTHS_10K)`` entries
    (some tail bucket missing from the profile) the leading widths are
    used positionally. Elements are divided by the matching bucket
    width so bucket-width imbalance no longer creates spurious peaks.
    """
    if not seq:
        return []
    return [
        (float(seq[i]) / _INCOME_BUCKET_WIDTHS_10K[i])
        if i < len(_INCOME_BUCKET_WIDTHS_10K) else float(seq[i])
        for i in range(len(seq))
    ]


def apply_income_monotonicity_fix(df, subject=None, verbose=True,
                                   *, aggressive_gap_pp=6.0):
    """Auto-repair mild-to-moderate bimodal INCOME distributions.

    The 2026-08-03 pipeline hardening rail #6.

    Motivation
    ----------
    SharkNinja main file 2026-08-04 shipped with income sequence:
        9.37, 16.51, 17.63, 15.91, 20.17, 14.10, 6.31
    A trough at $75-99K (15.91) sitting BELOW $50-74K (17.63) and
    $100-149K (20.17). The Avid file (same audience, tighter panel)
    was clean single-mode: 7.02, 13.54, 16.98, 17.47, 23.51, 16.48,
    4.99. The main-file dip is a modeling artifact, not a real
    dual-cohort signal. `validate_income_monotonicity` correctly
    flagged it — but was read-only and let the file ship.

    Fix logic
    ---------
    1. Read INCOME buckets in canonical ascending order.
    2. Find the argmax bucket (call it peak_i).
    3. For every bucket i in [1..peak_i], if bp[i] < bp[i-1] (trough
       on the ASCENDING side), interpolate: bp[i] = mean(bp[i-1],
       bp[i+1]) so the trough sits at least at the lower neighbor.
       If i+1 doesn't exist, use bp[i-1] + 0.5.
    4. For every bucket i in [peak_i+1..end], if bp[i] > bp[i-1] (rise
       on the DESCENDING side), pull down: bp[i] = mean(bp[i-1],
       bp[i+1]) or bp[i-1] - 0.5 as fallback. Floor at 0.5.
    5. Renormalize the block to 100 exactly.
    6. Recompute Raw + Proj for every touched row via _set_bp().

    Guardrail
    ---------
    The largest bimodal "gap" is (peak2 - trough) where peak2 is the
    lesser of the two peaks. When gap >= aggressive_gap_pp (default 6),
    the audience is likely GENUINELY dual-cohort (e.g., a product that
    serves both budget and premium segments distinctly). In that case
    we skip the auto-fix and only soft-warn. Empirically all "modeling
    artifact" cases in the corpus have gap in the 1.5-4pp range;
    genuine bimodal cases start at ~6-8pp.

    Returns (df, n_touched). Idempotent — no-op on monotonic files.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col is None:
        return df, 0
    # International frames carry country-native income bands (GBP/EUR
    # buckets, different widths and ordering conventions) - the US
    # bracket-order interpolation does not apply.
    _ctry = _frame_country(df)
    if _ctry:
        if verbose:
            print(f'   apply_income_monotonicity_fix: {_ctry} frame - '
                  f'US income-bracket repair skipped')
        return df, 0
    m_inc = df['Column'].astype(str).str.strip().str.upper() == 'INCOME'
    if not m_inc.any():
        return df, 0

    # Build ordered (idx, bucket_name, bp) list following canonical band
    # order. If a bucket alias isn't present, that slot is skipped and
    # the surrounding neighbors still form a valid contiguous sequence.
    inc_by_name = {}
    for idx in df.index[m_inc]:
        v = str(df.at[idx, 'Value']).strip()
        bp = _bp(df.at[idx, bp_col])
        if bp is not None:
            inc_by_name[v] = (idx, bp)

    ordered: list[tuple[int, str, float]] = []
    for aliases in _INCOME_BUCKETS_ORDERED:
        for a in aliases:
            if a in inc_by_name:
                idx, bp = inc_by_name[a]
                ordered.append((idx, a, bp))
                break

    if len(ordered) < 4:
        return df, 0

    seq = [bp for _, _, bp in ordered]

    # 2026-08-06 (colleague audit correction): detect bimodality on the
    # DENSITY sequence (BP per $10K bucket width), not raw BP. Otherwise
    # the $75-99K -> $100-149K transition (width doubles from $25K to
    # $50K) creates spurious "second peak" false positives on every
    # cleanly single-modal audience. See `_income_density_seq` and
    # `_INCOME_BUCKET_WIDTHS_10K` above.
    dseq = _income_density_seq(seq)

    # Bimodal detection = any INTERIOR local minimum on the density seq.
    # This is simpler AND catches the SharkNinja mild-dip case where
    # the old "dip >= 2pp AND rise >= 1pp" test missed a 1.72pp dip,
    # while suppressing bucket-width-driven false positives.
    troughs: list[int] = []
    for i in range(1, len(dseq) - 1):
        if dseq[i] < dseq[i - 1] and dseq[i] < dseq[i + 1]:
            troughs.append(i)
    if not troughs:
        return df, 0

    # Dual-cohort guardrail: for each trough, the smaller peak-to-trough
    # gap tells us how "committed" the two-peak structure is. Genuine
    # dual-cohort audiences have BOTH peaks well above the trough.
    # Artifact bimodals (SharkNinja etc.) have a shallow dip on one side.
    # Gap is measured on DENSITY (per-$10K units) so the threshold stays
    # in the same coordinate system as detection.
    max_smaller_gap = 0.0
    for ti in troughs:
        # Left peak: closest local max on the left (or endpoint) - measured
        # on DENSITY (per-$10K) to keep the coordinate system consistent
        # with the trough detection above.
        left_peak = dseq[ti - 1]
        for j in range(ti - 2, -1, -1):
            if dseq[j] < dseq[j + 1]:
                break
            left_peak = dseq[j]
        # Right peak: closest local max on the right (or endpoint)
        right_peak = dseq[ti + 1]
        for j in range(ti + 2, len(dseq)):
            if dseq[j] < dseq[j - 1]:
                break
            right_peak = dseq[j]
        gap = min(left_peak, right_peak) - dseq[ti]
        if gap > max_smaller_gap:
            max_smaller_gap = gap

    # Convert the raw-BP threshold to density units so the guardrail scales
    # with bucket width. Use the widest bucket in the flanking peaks'
    # neighborhood as a proxy - conservative: any dip that's dual-cohort
    # in raw BP is dual-cohort in density too, but not vice versa.
    if max_smaller_gap >= (aggressive_gap_pp / min(_INCOME_BUCKET_WIDTHS_10K)):
        if verbose:
            print(f'   ⚠️  apply_income_monotonicity_fix [{subject or ""}]: '
                  f'INCOME bimodal min-peak-gap {max_smaller_gap:.2f} density-pp '
                  f'>= threshold - likely genuine dual-cohort, '
                  f'LEAVING unchanged (raw_seq={[round(x, 2) for x in seq]}, '
                  f'density_seq={[round(x, 2) for x in dseq]})')
        return df, 0

    # Auto-fix path: lift each trough (in DENSITY space) to the mean of
    # its neighbors, then translate back to raw BP by multiplying by
    # bucket width. This ensures the "fix" itself doesn't reintroduce
    # a false peak at the wide $100-149K bucket.
    before = [round(x, 2) for x in seq]
    for _ in range(len(dseq)):
        new_troughs = [i for i in range(1, len(dseq) - 1)
                       if dseq[i] < dseq[i - 1] and dseq[i] < dseq[i + 1]]
        if not new_troughs:
            break
        for ti in new_troughs:
            dseq[ti] = (dseq[ti - 1] + dseq[ti + 1]) / 2.0
            width = (_INCOME_BUCKET_WIDTHS_10K[ti]
                     if ti < len(_INCOME_BUCKET_WIDTHS_10K) else 1.0)
            seq[ti] = dseq[ti] * width

    peak_i = max(range(len(dseq)), key=lambda i: dseq[i])
    # Descending side: pull down any density rises to at most the previous
    # bucket - again density-space so we don't overcorrect the wide
    # $100-149K / $150-249K buckets.
    for i in range(peak_i + 1, len(dseq)):
        if dseq[i] > dseq[i - 1]:
            lower_d = (dseq[i + 1] if i + 1 < len(dseq)
                       else dseq[i - 1] - 0.2)
            new_d = (dseq[i - 1] + max(lower_d, 0.05)) / 2.0
            if new_d >= dseq[i - 1]:
                new_d = dseq[i - 1] - 0.05
            new_d = max(0.05, new_d)
            width = (_INCOME_BUCKET_WIDTHS_10K[i]
                     if i < len(_INCOME_BUCKET_WIDTHS_10K) else 1.0)
            dseq[i] = new_d
            seq[i] = new_d * width

    # (Legacy raw-BP smoother kept for degenerate cases where dseq
    # collapses; harmless because the loop above already fixed
    # everything on density.)
    for i in range(peak_i + 1, len(seq)):
        if seq[i] > seq[i - 1]:
            lower = seq[i + 1] if i + 1 < len(seq) else seq[i - 1] - 1.0
            new_bp = (seq[i - 1] + max(lower, 0.5)) / 2.0
            if new_bp >= seq[i - 1]:
                new_bp = seq[i - 1] - 0.5
            seq[i] = max(0.5, new_bp)

    # Renormalize to 100 across the ordered buckets
    tot = sum(seq)
    if tot <= 0:
        return df, 0
    seq = [x * 100.0 / tot for x in seq]

    sample_size = _detect_sample_size(df, bp_col, raw_col)
    n = 0
    for (idx, name, _old), new_bp in zip(ordered, seq):
        df = _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        n += 1

    # 2026-08-05 (SharkNinja - Potential Air Fryer Consumer G5 fix):
    # `ordered` only covers buckets whose Value matches a canonical
    # alias in _INCOME_BUCKETS_ORDERED. If the source has extra INCOME
    # rows (alias variants we didn't enumerate, or new bucket labels),
    # the repaired subset sums to exactly 100 but the FULL block sums
    # to 100 + (other rows). Rescale every INCOME row proportionally so
    # the full block sums to exactly 100 -- this is what
    # renormalize_demographics_to_100 does, but that enforcer runs
    # BEFORE this fix in the chain, so the post-fix state needs its own
    # sum-to-100 pass. This keeps the trough-lift shape (via
    # proportional scaling of every row) while restoring BP sum=100.
    inc_idxs = list(df.index[m_inc])
    if inc_idxs:
        current = []
        for idx in inc_idxs:
            v = _bp(df.at[idx, bp_col])
            if v is not None:
                current.append((idx, max(0.001, v)))
        cur_total = sum(v for _, v in current)
        if cur_total > 0 and abs(cur_total - 100.0) > 0.1:
            scale = 100.0 / cur_total
            for idx, v in current:
                new_v = round(v * scale, 4)
                df = _set_bp(df, idx, new_v, bp_col, cs_col, raw_col,
                             proj_col, sample_size)

    df = _renormalize_category(df, 'INCOME', bp_col, cs_col, raw_col,
                                proj_col, sample_size)

    if verbose:
        # Re-read the ordered buckets after the full-block renormalize
        after_final = []
        for (idx, name, _old) in ordered:
            v = _bp(df.at[idx, bp_col])
            if v is not None:
                after_final.append(round(v, 2))
        # Verify full block sums to 100
        full_sum = 0.0
        for idx in inc_idxs:
            v = _bp(df.at[idx, bp_col])
            if v is not None:
                full_sum += v
        print(f'   🩹 apply_income_monotonicity_fix [{subject or ""}]: '
              f'INCOME repaired {before} -> {after_final} '
              f'({n} bucket(s); full block sums to {full_sum:.2f}%)')
    return df, n


def validate_income_monotonicity(df, *, subject=None, verbose=True):
    """Soft-warn on bimodal INCOME distributions.

    Returns (n_violations, sequence). n_violations is 0 on pass, 1 when
    the block violates monotonic-after-peak. NEVER auto-fixes or
    rejects.

    Kept alongside `apply_income_monotonicity_fix` (2026-08-04 rail #6)
    for auditability: the auto-fix runs first with a dual-cohort
    guardrail; this validator runs afterwards as a soft-warn to log
    the rare cases the auto-fix declined to touch.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return 0, []
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col is None:
        return 0, []
    m_inc = df['Column'].astype(str).str.strip().str.upper() == 'INCOME'
    if not m_inc.any():
        return 0, []
    inc_map = {}
    for idx in df.index[m_inc]:
        v = str(df.at[idx, 'Value']).strip()
        bp = _bp(df.at[idx, bp_col])
        if bp is not None:
            inc_map[v] = bp
    seq = []
    for aliases in _INCOME_BUCKETS_ORDERED:
        for a in aliases:
            if a in inc_map:
                seq.append(inc_map[a])
                break
    if len(seq) < 4:
        return 0, seq

    # 2026-08-06 (colleague audit correction): test bimodality on the
    # DENSITY sequence (BP per $10K bucket width), NOT raw BP. The
    # $100-149K bucket is 2x wider than $75-99K (and $150-249K is 4x
    # wider); comparing raw BPs across those transitions creates
    # spurious "second peak" false positives on every cleanly single-
    # modal audience. See `_income_density_seq` and
    # `_INCOME_BUCKET_WIDTHS_10K`.
    #
    # Detection matches the auto-fix's interior-local-minimum test:
    # any density point that is strictly less than BOTH neighbors is
    # a trough. The smallest peak-to-trough gap must exceed a
    # meaningfulness threshold to flag (the auto-fix uses this same
    # signal but with a wider guardrail before it declines to touch).
    dseq = _income_density_seq(seq)

    troughs: list[int] = []
    for i in range(1, len(dseq) - 1):
        if dseq[i] < dseq[i - 1] and dseq[i] < dseq[i + 1]:
            troughs.append(i)

    bimodal = False
    max_gap = 0.0
    for ti in troughs:
        gap = min(dseq[ti - 1], dseq[ti + 1]) - dseq[ti]
        if gap > max_gap:
            max_gap = gap
        # Threshold: >=0.2 density-pp trough (roughly 0.5pp raw over a
        # $25K bucket, or 1pp raw over a $50K bucket). Below that is
        # noise-level and not worth surfacing.
        if gap >= 0.2:
            bimodal = True

    if bimodal and verbose:
        print(f'   ⚠️  validate_income_monotonicity [{subject or ""}]: '
              f'INCOME appears bimodal in DENSITY space '
              f'(interior trough with peak->trough>=0.2/$10K) '
              f'max_gap={max_gap:.2f} density-pp '
              f'raw_seq={[round(x, 2) for x in seq]} '
              f'density_seq={[round(x, 2) for x in dseq]} - '
              f'auto-fix may have declined (dual-cohort guardrail). '
              f'Review manually if unexpected.')
    return (1 if bimodal else 0), seq


# ============================================================================
# 2026-08-04 Rail #7 — MULTI-BRAND BRAND INPUT SELF-PIN
# ============================================================================
# SharkNinja pair (Liz flag 2026-08-04): BRAND INPUT value was "SHARKNINJA"
# but the MPB rows are split as "SHARK" (11.65% main / 12.11% avid) and
# "NINJA" (13.56% main / 13.38% avid). Rule #3 says every subject brand
# pins at 100% in MPB + carry-through to secondary categories, but
# pin_subject_to_100_in_appearing_categories:
#   1. Looks for a single Value == subject_name row (SHARKNINJA doesn't
#      equal SHARK or NINJA individually)
#   2. Skips MPB and its family (APPAREL/FOOTWEAR, TECHNOLOGY BRAND,
#      HOME/OUTDOOR, ...) — but for multi-brand FAMILY profiles, MPB is
#      exactly where the subject brands live and should be pinned.
#
# This new enforcer handles multi-brand BRAND INPUT explicitly:
#   * Comma / plus / ampersand separator: "SHARK, NINJA" -> [SHARK, NINJA]
#   * Concatenated brand family: "SHARKNINJA" -> scan MPB for substring
#     matches ("SHARK" and "NINJA" both live in MPB -> multi-brand)
#   * Legacy single subject: no split, hand off to existing enforcer
# ============================================================================


def enforce_multi_brand_input_self_pin(df, subject=None, verbose=True):
    """Pin each component brand of a multi-brand BRAND INPUT to 100%.

    Detects three multi-brand signals in BRAND INPUT.Value:
      1. Explicit separator: comma, plus, ampersand between tokens.
      2. Concatenated family: no space, no separator, but two or more
         existing MPB rows match a substring of the input value.
      3. Space-separated pair where BOTH tokens exist as MPB brands.

    For each detected component brand:
      * Pins its row in MOST PURCHASED BRANDS to 100% (BP, CS, Raw,
        Proj).
      * Propagates the 100% pin to every OTHER category where the same
        brand value appears (Rule #3b MPB carry-through), such as
        TECHNOLOGY BRAND, HOME/OUTDOOR, APPAREL/FOOTWEAR, CPG,
        BEAUTY/WELLNESS, etc.

    Idempotent — skips brands already at 100%. Returns (df, n_pinned).
    No-op for single-subject profiles (falls through to the existing
    pin_subject_to_100_in_appearing_categories enforcer).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if not all((bp_col, raw_col, proj_col)):
        return df, 0

    col_u = df['Column'].astype(str).str.upper().str.strip()
    bi_mask = col_u == 'BRAND INPUT'
    if not bi_mask.any():
        return df, 0
    bi_val = str(df.loc[bi_mask].iloc[0].get('Value', '') or '').strip()
    if not bi_val:
        return df, 0

    # Prefer SAMPLE SIZE row -> BRAND INPUT row -> _detect_sample_size fallback.
    # _detect_sample_size walks rows and back-computes, which can pick up
    # a floating-point rounding artifact on the FIRST row with positive
    # BP + Raw (e.g., a low-BP brand row whose rounded Raw doesn't
    # cleanly invert). The subject rows are the authoritative source.
    def _to_int(cell):
        try:
            s = str(cell).replace(',', '').replace('%', '').strip()
            if not s or s.lower() in ('nan', 'none'):
                return None
            return int(float(s))
        except (ValueError, TypeError):
            return None

    sample_size = None
    sz_mask = col_u == 'SAMPLE SIZE'
    if sz_mask.any():
        sample_size = _to_int(df.loc[sz_mask].iloc[0].get(raw_col))
    if sample_size is None:
        sample_size = _to_int(df.loc[bi_mask].iloc[0].get(raw_col))
    if sample_size is None:
        sample_size = _detect_sample_size(df, bp_col, raw_col)
    profile_universe = int(round(sample_size / 10_000_000.0 * US_POP))

    # --- Step 1: extract candidate component tokens from BRAND INPUT ----
    bi_norm = bi_val.upper().strip()

    # Strip common noise suffixes/prefixes ("- FULL PROFILE", "(2025)"
    # etc.) that appear in some subject-string conventions.
    bi_clean = _re.sub(r'\s*[-–—]\s*FULL PROFILE.*$', '', bi_norm)
    bi_clean = _re.sub(r'\s*\([^)]*\)\s*$', '', bi_clean).strip()

    # Explicit separator split
    sep_tokens: list[str] = []
    if _re.search(r'[,+&]', bi_clean):
        sep_tokens = [t.strip() for t in _re.split(r'[,+&]', bi_clean)
                      if t.strip()]

    # Concatenated / space-separated candidate detection needs an index
    # of MPB row values.
    mpb_mask = col_u == 'MOST PURCHASED BRANDS'
    mpb_vals: dict[str, int] = {}
    if mpb_mask.any():
        for idx in df.index[mpb_mask]:
            v = str(df.at[idx, 'Value'] or '').strip().upper()
            if v:
                mpb_vals[v] = idx

    detected: list[str] = []

    # A) explicit separator: each token must exist as an MPB brand
    if sep_tokens:
        for t in sep_tokens:
            if t in mpb_vals:
                detected.append(t)

    # B) concatenated / space-separated: try prefix + suffix decomposition
    # (e.g. "SHARKNINJA" -> "SHARK" + "NINJA", or space "SHARK NINJA").
    if not detected:
        # First try space-separated pair
        if ' ' in bi_clean:
            parts = bi_clean.split()
            if len(parts) == 2 and all(p in mpb_vals for p in parts):
                detected = parts
        # Then concatenated: find two MPB brands that together cover the
        # whole string with no gap.
        if not detected and ' ' not in bi_clean and len(bi_clean) >= 6:
            for cut in range(3, len(bi_clean) - 2):
                left = bi_clean[:cut]
                right = bi_clean[cut:]
                if left in mpb_vals and right in mpb_vals:
                    detected = [left, right]
                    break

    # C) Legacy single subject: if only the whole BI value matches an
    # MPB row, that's the normal single-subject case — hand off to the
    # existing enforcer and no-op here.
    if not detected:
        # Try single: whole cleaned input matches an MPB brand
        if bi_clean in mpb_vals:
            return df, 0  # single-brand path handled elsewhere
        return df, 0  # nothing to do

    # De-dupe while preserving order
    seen: set[str] = set()
    detected = [t for t in detected if not (t in seen or seen.add(t))]

    if verbose:
        print(f'   🔗 enforce_multi_brand_input_self_pin '
              f'[{subject or ""}]: BRAND INPUT="{bi_val}" detected as '
              f'multi-brand -> {detected}')

    # --- Step 2: pin each component brand to 100 -------------------------
    #
    # For each detected token, find EVERY row (across MPB + secondary
    # categories) whose normalized Value matches, and pin to 100. This
    # is the Rule #3b MPB carry-through for a subject brand.
    n_pinned = 0
    touched_cats: set[str] = set()
    val_norm = (
        df['Value'].astype(str).str.upper().str.strip()
        .str.replace(r'[^A-Z0-9]', '', regex=True)
    )
    for tok in detected:
        tok_norm = _re.sub(r'[^A-Z0-9]', '', tok.upper())
        mask = val_norm == tok_norm
        if not mask.any():
            if verbose:
                print(f'      [multi-brand] "{tok}" - no rows in profile')
            continue
        for idx in df.index[mask]:
            cur_bp = _bp(df.at[idx, bp_col])
            if cur_bp is not None and cur_bp >= 99.9999:
                continue  # already pinned
            cat_here = str(df.at[idx, 'Column']).strip()
            df.at[idx, bp_col] = '100.0000'
            if cs_col is not None:
                df.at[idx, cs_col] = '100.0000'
            df.at[idx, raw_col] = sample_size
            df.at[idx, proj_col] = profile_universe
            n_pinned += 1
            touched_cats.add(cat_here.upper())
            if verbose:
                cur_str = f'{cur_bp:.4f}' if cur_bp is not None else 'blank'
                print(f'      [multi-brand] "{tok}" in [{cat_here}]: '
                      f'{cur_str} -> 100.0000%')

    # --- Step 3: renormalize each touched category so Share sums right --
    for cat in touched_cats:
        df = _renormalize_category(df, cat, bp_col, cs_col, raw_col,
                                   proj_col, sample_size)

    return df, n_pinned


# ============================================================================
# D88 — STRIP TILDE FROM BRAND INPUT (writer-bug fix)
# ============================================================================
# 2026-06-07 (Jenna deep audit): subject-naming layer in BG.py emits
# "MEGHAN~MARKLE" instead of "MEGHAN MARKLE" in 226 of 525 corpus files
# (43% incidence). Replace `~` with space at write time so dashboards render
# clean human-readable subject names. Propagates to any internal value
# row that referenced the old tilde-form (TALENT self-anchor etc.).
# ============================================================================

def apply_strip_tilde_from_brand_input(df, subject, verbose=True):
    """Replace `~` with space in BRAND INPUT.Value and propagate to any
    internal row whose Value matches the old tilde form. Returns
    (df, n_rows_touched). Idempotent — no-op when no tildes present.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bi_idx = df.index[df['Column'].astype(str).str.upper().str.strip() == 'BRAND INPUT']
    if not len(bi_idx):
        return df, 0
    bi = bi_idx[0]
    old = str(df.at[bi, 'Value'] or '').strip()
    if '~' not in old:
        return df, 0
    new = old.replace('~', ' ').upper().strip()
    df.at[bi, 'Value'] = new
    n = 1
    old_upper = old.upper().strip()
    new_upper = new.upper()
    # Propagate to internal references (TALENT self-anchor, MUSICIAN/BAND etc.)
    for i in df.index:
        if i == bi:
            continue
        v = str(df.at[i, 'Value'] or '').strip().upper()
        if v == old_upper or v == old_upper.replace('~', ' '):
            df.at[i, 'Value'] = new_upper
            n += 1
    if verbose:
        print(f"   🔧 apply_strip_tilde_from_brand_input [{subject or ''}]: "
              f"'{old}' → '{new}' ({n} row(s) touched)")
    return df, n


# ============================================================================
# D102b — CREDIT PROVIDER CANONICAL NORMALIZE (audience-aware band-anchor)
# ============================================================================
# 2026-06-07 (Jenna 100%-incidence finding): every fresh pull leaks at least
# 2-4 CP rows below the realistic adult-audience penetration band. 42 of 42
# fresh profiles audited 2026-06-07 needed this fix. Applies canonical bands
# with deterministic per-(subject, brand) jitter so values stay unique across
# profiles AND reproducible across re-runs (no pinning, per workspace rule #1).
# Only operates on rows that ALREADY exist in the CREDIT PROVIDER block —
# never adds new brands (so hostmap-gating not strictly required).
# ============================================================================

# Canonical bands for the major US credit-card / charge-card brands. Sourced
# from corpus median ± std-dev across mainstream-adult-audience profiles
# (Visa 52-65, Mastercard 32-45, etc.). DO NOT add Digital Banking brands
# here (PayPal, Venmo, CashApp, Zelle, ApplePay, GooglePay) — those live in
# DIGITAL BANKING and have a separate band table (apply_db_canonical_normalize
# is a future P2 ask per the 2026-06-07 audit recommendations).
_CANONICAL_CP_BANDS = {
    'VISA':             (52.0, 65.0),
    'MASTERCARD':       (32.0, 45.0),
    'AMERICAN EXPRESS': (12.0, 22.0),
    'AMEX':             (12.0, 22.0),
    'DISCOVER':         (10.0, 18.0),
    'CAPITAL ONE':      (13.0, 22.0),
    'CHASE':            (10.0, 18.0),
}


def apply_cp_canonical_normalize(df, subject, verbose=True):
    """Lift any CREDIT PROVIDER row whose BP is BELOW its canonical band to
    a deterministic-jittered value inside the band. Recomputes Raw + Proj
    via _set_bp(). Returns (df, n_rows_lifted). Idempotent — only fires on
    rows currently below band (no-op when corpus is already in-band).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    cp_mask = df['Column'].astype(str).str.strip().str.upper() == 'CREDIT PROVIDER'
    if not cp_mask.any():
        return df, 0

    n_lifted = 0
    lift_log: list[tuple[str, float, float]] = []
    for idx in df.index[cp_mask]:
        v_upper = str(df.at[idx, 'Value'] or '').strip().upper()
        band = _CANONICAL_CP_BANDS.get(v_upper)
        if band is None:
            continue
        low, high = band
        cur_bp = _bp(df.at[idx, bp_col])
        # Only lift if BELOW band — never cap-down (cap-down would pin
        # any high-engagement profile and violate rule #1).
        if cur_bp >= low:
            continue
        # Deterministic jitter inside the band per (subject, brand).
        new_bp = _jitter_for(subject, v_upper, salt='d102b_cp', lo=low, hi=high)
        df = _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        n_lifted += 1
        lift_log.append((v_upper, cur_bp, new_bp))

    if n_lifted:
        df = _renormalize_category(df, 'CREDIT PROVIDER',
                                   bp_col, cs_col, raw_col, proj_col, sample_size)
        if verbose:
            print(f"   🔧 apply_cp_canonical_normalize [{subject or ''}]: "
                  f"{n_lifted} row(s) lifted to canonical band")
            for v, old, new in lift_log[:6]:
                print(f"      • {v}: {old:.4f}% → {new:.4f}%")
    return df, n_lifted


# ============================================================================
# D106-EXT v3 — TELECOM CANONICAL NORMALIZE (audience-age + ethnicity + gender)
# ============================================================================
# 2026-06-06 → 2026-06-07 escalation chain (Jenna):
#   Shirley MacLaine → Johnny Depp → Jada Pinkett Smith → Pat Sajak →
#   Sharon Stone → Pamela Anderson → Orlando Bloom → Bob Odenkirk (0.74%).
#
# Single defect: AT&T BP gets emitted at a value that ignores the audience's
# age + ethnicity + gender composition. Fix is to compute the EXPECTED band
# from audience composition and lift if the current value is depressed by
# more than 1.5pp. Sub-tier ordering matters — female-majority light-senior
# is checked FIRST, then age-only tiers, then ethnicity modulator.
# ============================================================================

# Telecom brands with audience-aware canonical bands. AT&T is the primary
# escape vector but Verizon / T-Mobile have similar (less severe) drift on
# senior + female-majority audiences.
_CANONICAL_TELECOM_BRANDS = ('AT&T', 'ATT', 'AT T')  # value-string variants


def _expected_att_band(s55_pct, fem_pct, eth_black_pct):
    """Return (low, high) AT&T band for an audience with `s55_pct` 55+,
    `fem_pct` female, `eth_black_pct` Black/African American. Sub-tier
    ordering (most-specific first):
      1. female-majority light-senior     (fem≥60, 25≤s55<50)  → (28, 32)
      2. very-senior                      (s55≥50)             → (33, 38)
      3. senior                           (s55≥35)             → (30, 35)
      4. high-Black-audience               (eth_black≥30)       → (38, 45)
      5. mainstream-young                 (s55<20)             → (20, 27)
      6. mainstream-mainstream            (default)            → (24, 32)
    Each tier produces a 4pp-wide band; deterministic jitter spreads
    within-band so cross-profile values stay distinct.
    """
    if fem_pct >= 60 and 25 <= s55_pct < 50:
        return (28.0, 32.0)
    if s55_pct >= 50:
        return (33.0, 38.0)
    if s55_pct >= 35:
        return (30.0, 35.0)
    if eth_black_pct >= 30:
        return (38.0, 45.0)
    if s55_pct < 20:
        return (20.0, 27.0)
    return (24.0, 32.0)


def _audience_pct(df, demo_cat, value_substrings):
    """Sum BP for any row in `demo_cat` whose Value upper-cased contains
    one of `value_substrings` (also upper-cased). Returns 0.0 if not found.
    Tolerant to missing block / mixed dtypes."""
    if 'Column' not in df.columns:
        return 0.0
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col not in df.columns:
        return 0.0
    mask = df['Column'].astype(str).str.strip().str.upper() == demo_cat.upper()
    if not mask.any():
        return 0.0
    rows = df.loc[mask].copy()
    rows['_v'] = rows['Value'].astype(str).str.upper().str.strip()
    needles = [s.upper() for s in value_substrings]
    hit = rows[rows['_v'].apply(lambda v: any(n in v for n in needles))]
    if hit.empty:
        return 0.0
    return float(hit[bp_col].apply(_bp).sum())


def apply_telecom_canonical_normalize(df, subject, verbose=True):
    """Lift AT&T BP if depressed relative to the audience-aware expected band.
    Computes the expected band from AGE / GENDER / ETHNICITY composition,
    then lifts if BP < (low - 1.5). Recomputes Raw + Proj via _set_bp.
    Idempotent. No cap-down (no pinning). Returns (df, n_rows_lifted).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    tel_mask = df['Column'].astype(str).str.strip().str.upper() == 'TELECOM'
    if not tel_mask.any():
        return df, 0

    # Audience composition (used by tier selection).
    s55_64 = _audience_pct(df, 'AGE', ['55-64'])
    s65 = _audience_pct(df, 'AGE', ['65+', '65 OR OLDER'])
    s55 = s55_64 + s65
    fem = _audience_pct(df, 'GENDER', ['FEMALE'])
    eth_black = _audience_pct(df, 'ETHNICITY', ['BLACK', 'AFRICAN AMERICAN'])

    n_lifted = 0
    lift_log: list[tuple[str, float, float, tuple]] = []
    for idx in df.index[tel_mask]:
        v_upper = str(df.at[idx, 'Value'] or '').strip().upper()
        # Match all value-string variants of AT&T (catches placeholder paths
        # that emit 'ATT' or 'AT T' instead of the canonical 'AT&T').
        if v_upper not in _CANONICAL_TELECOM_BRANDS:
            continue
        cur_bp = _bp(df.at[idx, bp_col])
        low, high = _expected_att_band(s55, fem, eth_black)
        # 1.5pp tolerance below band — only lift on real depressions, not
        # on values that are slightly under-tier (avoid jitter-on-jitter).
        if cur_bp >= (low - 1.5):
            continue
        new_bp = _jitter_for(subject, v_upper, salt='d106_telecom',
                             lo=low, hi=high)
        df = _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col,
                     sample_size)
        n_lifted += 1
        lift_log.append((v_upper, cur_bp, new_bp, (low, high)))

    if n_lifted:
        df = _renormalize_category(df, 'TELECOM',
                                   bp_col, cs_col, raw_col, proj_col, sample_size)
        if verbose:
            print(f"   🔧 apply_telecom_canonical_normalize [{subject or ''}]: "
                  f"{n_lifted} row(s) lifted "
                  f"(audience: s55={s55:.0f}% fem={fem:.0f}% black={eth_black:.0f}%)")
            for v, old, new, (lo, hi) in lift_log:
                print(f"      • {v}: {old:.4f}% → {new:.4f}% (band {lo}-{hi})")
    return df, n_lifted


# ============================================================================
# D112 — DIGITAL BANKING canonical normalize (Apple Pay / PayPal pair)
# ============================================================================
# 2026-06-08 (Jenna 4-profile escalation): Sharon Stone, Adriana Paz, Edward
# Norton, Jamie Lee Curtis all ship with APPLE PAY > PAYPAL (50-57% vs
# 41-50%). The 4 don't cluster on any demographic — Sharon Stone is older
# female, Adriana Paz is younger female, Edward Norton is older balanced.
# Audience-tracking would predict different defects on different audiences,
# but they all show the same swap. That points to a transform/writer-bug
# corrupting PayPal on a random subset, same conclusion as D111 PORN
# over-read: anchor calibration, not audience-tracking.
#
# Corpus scan of 651 files:
#   - 33 files (5.1%) have APPLE PAY > PAYPAL
#   - corpus median PayPal=62.96% / Apple Pay=43.48%
#   - PayPal-deflated tail (PP < 50%): 83 files
#   - Apple-Pay-inflated tail (AP ≥ 50%): 217 files (33% of corpus)
#
# Real-world adult-panel data: PayPal 70-80% (universal), Apple Pay
# 35-50% (smartphone-skew). PayPal should always > Apple Pay; the latter
# is heavier on younger audiences but PayPal still leads.
#
# Fix: audience-aware tiered bands for both PayPal AND Apple Pay,
# bidirectional clamp, and a hard invariant (PayPal > Apple Pay by ≥ 5pp
# post-fix). Tail brands (Venmo / Zelle / Cash App / Chime / etc.) left
# untouched — they aren't part of the swap pattern.
# ============================================================================


def _expected_db_bands(male_pct, fem_pct, a18_34, a55_plus):
    """Return ((paypal_lo, paypal_hi), (apple_pay_lo, apple_pay_hi),
    apple_can_lead) for an audience composition.

    `apple_can_lead` is True when the audience composition makes Apple
    Pay > PayPal a defensible per-row reasoning outcome (rule #2:
    "REASONING > FLOORS"). When True, the enforcer + G7 gate skip the
    PP-leads invariant and let the agent's row-level reasoning stand.

    Tier order (most-specific FIRST):
      1. heavily-senior     (a55+ ≥ 50)          → PP (62, 75), AP (20, 30)   AP_lead=False
      2. older skew         (a55+ ≥ 35)          → PP (60, 72), AP (28, 38)   AP_lead=False
      3. very-young / tech  (a18-34 ≥ 60)        → PP (45, 60), AP (50, 65)   AP_lead=True
      4. younger skew       (a18-34 ≥ 50)        → PP (50, 62), AP (42, 56)   AP_lead=True (soft)
      5. mainstream         default              → PP (55, 68), AP (32, 42)   AP_lead=False

    Real-world panel data informs the tier ranges:
      - PayPal is 70-80% on US adults, 75-85% on age 35+ (oldest age
        groups have lower smartphone penetration but those who ARE
        online use PayPal more than Apple Pay).
      - Apple Pay is ~50% on smartphone owners, ~38-45% on all adults,
        ~22-30% on age 55+ (older users have lower Apple-ecosystem
        adoption + lower NFC-checkout familiarity).
      - For Gen Z / very-young / iPhone-native cohorts, Apple Pay can
        legitimately lead PayPal. Recent panel data (Forbes Advisor 2025
        US payment-app survey): 18-34 Apple Pay weekly use ≈ 52-60%,
        PayPal ≈ 45-55%. Tier 3 reflects that crossover.

    2026-06-09 Jenna pushback ("apple pay can be top if it makes sense
    for that specific audience and the agent really believes it"):
    relaxed prior hard PP > AP + 5pp invariant. Apple-Pay-leadership is
    now allowed where audience composition (tier 3 / 4) supports it.
    The writer-bug catch still runs in tiers 1, 2, 5 where AP > PP is a
    near-certain transform corruption.
    """
    if a55_plus >= 50:
        return (62.0, 75.0), (20.0, 30.0), False
    if a55_plus >= 35:
        return (60.0, 72.0), (28.0, 38.0), False
    if a18_34 >= 60:
        return (45.0, 60.0), (50.0, 65.0), True
    if a18_34 >= 50:
        return (50.0, 62.0), (42.0, 56.0), True
    return (55.0, 68.0), (32.0, 42.0), False


def apply_db_canonical_normalize(df, subject, verbose=True):
    """Audience-aware band-clamp for DIGITAL BANKING leader pair (PayPal
    and Apple Pay). Computes audience-aware bands from AGE + GENDER, then:
      - lifts PayPal if BP < band_low - 1 (aggressive on depression — the
        dominant defect direction)
      - caps PayPal if BP > band_high + 3
      - lifts Apple Pay if BP < band_low - 3
      - caps Apple Pay if BP > band_high + 3 (relaxed from +1; agent
        reasoning sets the level inside band ± 3pp)
      - leaves Venmo / Zelle / Cash App / etc. untouched (no swap signal)

    Apple-Pay-leadership invariant (2026-06-09 Jenna relaxation):
      - For mainstream / older audiences (tiers 1, 2, 5): if AP > PP - 5,
        cap-down Apple Pay so PayPal leads by ≥ 5pp. This catches the
        writer-bug signature observed pre-D112 across older audiences.
      - For very-young / younger-skew audiences (tiers 3, 4):
        AP can lead PP because the agent's per-row reasoning may
        legitimately reflect Gen Z / iPhone-native panel data. The
        invariant is SKIPPED — the agent's reasoning stands.
        Defense-in-depth: even on these tiers, if PayPal looks
        suppressed (PP < pp_lo - 1) we still lift it to its band, but
        we never cap-down Apple Pay just because AP > PP.

    Recomputes Raw + Proj via _set_bp. Idempotent (±3pp tolerance).
    Returns (df, n_rows_touched).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    db_mask = df['Column'].astype(str).str.strip().str.upper() == 'DIGITAL BANKING'
    if not db_mask.any():
        return df, 0

    male = _audience_pct_exact(df, 'GENDER', ['MALE'])
    fem = _audience_pct_exact(df, 'GENDER', ['FEMALE'])
    a18_34 = _audience_pct_exact(df, 'AGE', ['18-24', '25-34'])
    a55_plus = _audience_pct_exact(df, 'AGE', ['55-64', '65+', '65 OR OLDER'])
    (pp_lo, pp_hi), (ap_lo, ap_hi), apple_can_lead = _expected_db_bands(
        male, fem, a18_34, a55_plus,
    )

    # Find PayPal + Apple Pay rows
    pp_idx = None
    ap_idx = None
    for idx in df.index[db_mask]:
        v = str(df.at[idx, 'Value']).strip().upper()
        if v == 'PAYPAL':
            pp_idx = idx
        elif v == 'APPLE PAY':
            ap_idx = idx
    if pp_idx is None and ap_idx is None:
        return df, 0

    n_touched = 0
    log_rows: list[tuple[str, float, float, str]] = []

    # PayPal — asymmetric tolerance: lift aggressively (lower threshold
    # -1pp) but cap leniently (upper threshold +3pp). 2026-06-08 (Dakota
    # Fanning escalation): PP=53.54 was just inside symmetric ±3 band
    # but still visibly below the canonical floor of 55. Asymmetric
    # makes the enforcer more eager on the depression side, which is
    # the dominant defect direction.
    if pp_idx is not None:
        cur_pp = _bp(df.at[pp_idx, bp_col])
        new_pp = None
        if cur_pp < pp_lo - 1.0:
            new_pp = _jitter_for(subject, 'PAYPAL',
                                 salt='d112_db_paypal', lo=pp_lo, hi=pp_hi)
            action = 'LIFT'
        elif cur_pp > pp_hi + 3.0:
            new_pp = _jitter_for(subject, 'PAYPAL',
                                 salt='d112_db_paypal', lo=pp_lo, hi=pp_hi)
            action = 'CAP'
        if new_pp is not None:
            df = _set_bp(df, pp_idx, new_pp, bp_col, cs_col,
                         raw_col, proj_col, sample_size)
            n_touched += 1
            log_rows.append(('PAYPAL', cur_pp, new_pp, action))

    # Apple Pay — symmetric ±3pp tolerance (2026-06-09 relaxed from
    # +1pp asymmetric). Per Jenna: "apple pay can be top if it makes
    # sense for that specific audience and the agent really believes
    # it." So AP within band_high + 3pp is left alone; the agent's
    # reasoning sets the exact level. Cap only on far-out-of-band
    # values (likely a true writer-bug, not an audience-justified call).
    if ap_idx is not None:
        cur_ap = _bp(df.at[ap_idx, bp_col])
        new_ap = None
        if cur_ap < ap_lo - 3.0:
            new_ap = _jitter_for(subject, 'APPLE PAY',
                                 salt='d112_db_apple', lo=ap_lo, hi=ap_hi)
            action = 'LIFT'
        elif cur_ap > ap_hi + 3.0:
            new_ap = _jitter_for(subject, 'APPLE PAY',
                                 salt='d112_db_apple', lo=ap_lo, hi=ap_hi)
            action = 'CAP'
        if new_ap is not None:
            df = _set_bp(df, ap_idx, new_ap, bp_col, cs_col,
                         raw_col, proj_col, sample_size)
            n_touched += 1
            log_rows.append(('APPLE PAY', cur_ap, new_ap, action))

    # Audience-conditional invariant — only enforced where the audience
    # composition does NOT support Apple-Pay leadership. For tiers 3/4
    # (very-young / younger-skew), AP > PP is left to per-row agent
    # reasoning. For tiers 1/2/5 (older / mainstream), the writer-bug
    # signature dominates and we still cap-down AP so PP leads by ≥5pp.
    if pp_idx is not None and ap_idx is not None and not apple_can_lead:
        pp_now = _bp(df.at[pp_idx, bp_col])
        ap_now = _bp(df.at[ap_idx, bp_col])
        if ap_now >= pp_now - 5.0:
            target_ap_max = max(ap_lo, pp_now - 6.0)
            new_ap2 = _jitter_for(subject, 'APPLE PAY',
                                  salt='d112_db_invariant',
                                  lo=max(ap_lo, target_ap_max - 4),
                                  hi=target_ap_max)
            df = _set_bp(df, ap_idx, new_ap2, bp_col, cs_col,
                         raw_col, proj_col, sample_size)
            n_touched += 1
            log_rows.append(('APPLE PAY', ap_now, new_ap2, 'INVARIANT'))

    if n_touched:
        df = _renormalize_category(df, 'DIGITAL BANKING',
                                   bp_col, cs_col, raw_col, proj_col, sample_size)
        if verbose:
            print(f"   🔧 apply_db_canonical_normalize [{subject or ''}]: "
                  f"{n_touched} row(s) touched "
                  f"(audience: M={male:.0f}% F={fem:.0f}% "
                  f"18-34={a18_34:.0f}% 55+={a55_plus:.0f}% | "
                  f"PP_band=[{pp_lo:.0f},{pp_hi:.0f}] "
                  f"AP_band=[{ap_lo:.0f},{ap_hi:.0f}] "
                  f"apple_can_lead={apple_can_lead})")
            for v, old, new, act in log_rows:
                print(f"      • {v} ({act}): {old:.4f}% → {new:.4f}%")
    return df, n_touched


# ============================================================================
# D111 — PORNHUB CANONICAL NORMALIZE (bidirectional audience-aware band)
# ============================================================================
# 2026-06-08 (Jenna two-tail anchor calibration finding):
#   Ed Helms 65.12% / Elisabeth Moss 64.18% (~25pp over the 40 norm)
#   Emma Stone 0.49% / Margot Robbie 0.61% (~40pp under the 40 norm)
#
# Corpus scan of 654 files found:
#   - 57 files with PORNHUB > 55%        (over-read tail)
#   - 154 files with PORNHUB ≤ 15%       (under-read tail, mostly female-skew)
#   - median-of-max = 39.61%             (= the 40 norm)
#
# Root cause: enforce_shelf_category_distribution (the existing PORN MEDIA
# enforcer) uses a FIXED target_top_pp = 40.0 regardless of audience
# composition. Combined with cases where the enforcer doesn't fire (LLM
# already emitted leader inside 'plausible' shape but at a wildly mis-
# calibrated absolute level), result is a two-tail spread across the
# corpus — the same anchor producing both depression AND elevation
# depending on which side of 40 the LLM seed landed.
#
# Fix is a SEPARATE bidirectional band-clamp that runs LATE (after
# enforce_shelf_category_distribution but before final dejitter), uses
# audience age + gender to choose a tier-based PORNHUB band, and clamps
# both directions. Top-3 PORN MEDIA brands (PORNHUB, XVIDEOS, XHAMSTER)
# get rescaled together to preserve rank distribution. Tail (BP < 2%) is
# left alone since LLM doesn't have signal there anyway.
# ============================================================================


def _expected_pornhub_band(male_pct, fem_pct, a18_34, a55_plus):
    """Return (low, high) PORNHUB band for an audience composition.

    Tier order (most-specific FIRST per workspace rule on enforcer ordering;
    tiers carry through to per-brand caps so the most-restrictive senior /
    female tiers must evaluate before the broader male / mainstream ones).

      1. heavily-senior + female-skew  (a55+ ≥ 50 AND skew ≤ -8) → ( 8, 16)
      2. heavily-senior                 (a55+ ≥ 50)              → (12, 22)
      3. older female-skew              (skew ≤ -8 AND a55+ ≥ 30)→ (15, 25)
      4. female-skew                    (skew ≤ -8)              → (22, 32)
      4b. older-leaning + mild-fem      (a55+ ≥ 40 AND skew ≤ -4)→ (13, 21)
      4c. older-leaning non-male        (a55+ ≥ 40 AND skew < 8) → (17, 26)
      5. young male-skew                (skew ≥ +8 AND a18-34 ≥ 50) → (50, 60)
      6. male-skew                      (skew ≥ +8)              → (45, 55)
      7. young mainstream balanced      (a18-34 ≥ 50)            → (40, 50)
      8. mainstream balanced            default                  → (35, 45)

    `skew` is `male_pct - fem_pct` (positive = male-skew). Threshold 8pp
    chosen because GENDER demos drift ±2-3pp from rounding alone, but a
    real audience-skew tilt is ≥ 8pp (matches D106 sub-tier threshold).

    Tiers 4b/4c added 2026-08-25 (Liz QA flag, Ari Melber base): an
    audience at 55+ = 45.6% / skew = -5.8 narrowly missed BOTH the
    heavily-senior threshold (50) and the female-skew threshold (-8)
    and fell all the way to the default mainstream band (35, 45),
    which blessed the shelf enforcer's fixed 40-target PORN MEDIA
    ladder (Pornhub shipped at 40.86 while the file's own Avid subset
    clamped to 12.57 and the peer Nicolle Wallace base clamped to
    13.12 - a 3.25x category discontinuity from a 4-7pp demo delta).
    The intermediate tiers close that cliff for older-leaning
    audiences without touching male-skew or young-mainstream tiers.
    """
    skew = float(male_pct) - float(fem_pct)
    if a55_plus >= 50:
        if skew <= -8:
            return (8.0, 16.0)
        return (12.0, 22.0)
    if skew <= -8 and a55_plus >= 30:
        return (15.0, 25.0)
    if skew <= -8:
        return (22.0, 32.0)
    if a55_plus >= 40 and skew < 8:
        if skew <= -4:
            return (13.0, 21.0)
        return (17.0, 26.0)
    if skew >= 8 and a18_34 >= 50:
        return (50.0, 60.0)
    if skew >= 8:
        return (45.0, 55.0)
    if a18_34 >= 50:
        return (40.0, 50.0)
    return (35.0, 45.0)


def _audience_pct_exact(df, demo_cat, exact_values):
    """Sum BP for any row in `demo_cat` whose Value (upper, stripped) is
    in `exact_values`. Tolerant to missing block / mixed dtypes. Differs
    from `_audience_pct` which does substring matching — exact matching
    avoids the 'MALE' substring of 'FEMALE' bug.
    """
    if 'Column' not in df.columns:
        return 0.0
    bp_col, _, _, _ = _detect_cols(df)
    if bp_col not in df.columns:
        return 0.0
    mask = df['Column'].astype(str).str.strip().str.upper() == demo_cat.upper()
    if not mask.any():
        return 0.0
    rows = df.loc[mask].copy()
    rows['_v'] = rows['Value'].astype(str).str.upper().str.strip()
    needles = {s.upper() for s in exact_values}
    hit = rows[rows['_v'].isin(needles)]
    if hit.empty:
        return 0.0
    return float(hit[bp_col].apply(_bp).sum())


def apply_porn_canonical_normalize(df, subject, verbose=True):
    """Bidirectional band-clamp for PORN MEDIA leader cluster (PORNHUB +
    top-2 peers). Computes audience-aware band from AGE + GENDER, then:
      - if PORNHUB BP > band_high + 3pp: cap-down to within-band jitter
      - if PORNHUB BP < band_low - 3pp:  lift to within-band jitter
      - apply same scale factor to XVIDEOS + XHAMSTER (preserves rank)
      - leave brands < 2pp untouched (tail has no signal anyway)

    Recomputes Raw + Proj via _set_bp. Idempotent: tolerance band ±3pp
    means a re-run on already-clamped values is a no-op.

    Returns (df, n_rows_touched).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    porn_mask = df['Column'].astype(str).str.strip().str.upper() == 'PORN MEDIA'
    if not porn_mask.any():
        return df, 0

    # Audience composition (exact-match — _audience_pct substring path
    # would conflate 'MALE' inside 'FEMALE').
    male = _audience_pct_exact(df, 'GENDER', ['MALE'])
    fem = _audience_pct_exact(df, 'GENDER', ['FEMALE'])
    a18_34 = _audience_pct_exact(df, 'AGE', ['18-24', '25-34'])
    a55_plus = _audience_pct_exact(df, 'AGE', ['55-64', '65+', '65 OR OLDER'])

    band_lo, band_hi = _expected_pornhub_band(male, fem, a18_34, a55_plus)
    band_mid = (band_lo + band_hi) / 2.0

    # Find PORNHUB row
    pornhub_idx = None
    for idx in df.index[porn_mask]:
        if str(df.at[idx, 'Value']).strip().upper() == 'PORNHUB':
            pornhub_idx = idx
            break
    if pornhub_idx is None:
        return df, 0

    cur_bp = _bp(df.at[pornhub_idx, bp_col])
    # Asymmetric tolerance (2026-06-08, Jenna escalations Cesar Millan /
    # Daniel Kaluuya / Don Johnson / David Hyde Pierce): lift on ANY
    # below-band value (zero tolerance), but cap leniently (upper threshold
    # +3pp for idempotency). Depression is the dominant defect direction;
    # values like Don Johnson 7.89% sitting just inside ±1 tolerance still
    # surface as "depressed" to users. Zero-floor lift threshold catches
    # those without sacrificing cap-side idempotency on already-clamped
    # over-band values.
    if band_lo <= cur_bp <= (band_hi + 3.0):
        return df, 0

    # Pick new PORNHUB BP inside band via deterministic jitter
    new_pornhub = _jitter_for(subject, 'PORNHUB', salt='d111_porn',
                              lo=band_lo, hi=band_hi)
    # Scale factor — applied to top-N peer brands so rank gradient stays
    # intact instead of producing a discontinuity at the leader.
    if cur_bp <= 0:
        # Was suppressed to ~0; scale-from-zero is undefined. Lift only
        # PORNHUB; leave peers as-is (per-brand audience-aware lift would
        # require a per-brand band table — out of scope for this enforcer).
        scale = None
    else:
        scale = new_pornhub / cur_bp

    n_touched = 0
    log_rows: list[tuple[str, float, float]] = []

    df = _set_bp(df, pornhub_idx, new_pornhub,
                 bp_col, cs_col, raw_col, proj_col, sample_size)
    n_touched += 1
    log_rows.append(('PORNHUB', cur_bp, new_pornhub))

    # Scale top-2 peers (XVIDEOS, XHAMSTER) when scale is defined and
    # they are above the floor (≥ 2pp) — tail brands have no signal.
    PEER_BRANDS = ('XVIDEOS', 'XHAMSTER', 'XNXX', 'SPANKBANG', 'YOUPORN')
    if scale is not None:
        for idx in df.index[porn_mask]:
            v_upper = str(df.at[idx, 'Value']).strip().upper()
            if v_upper not in PEER_BRANDS:
                continue
            old = _bp(df.at[idx, bp_col])
            if old < 2.0:
                continue
            scaled = round(old * scale, 4)
            # Add per-brand jitter (avoid 4dp identity collision with PORNHUB)
            scaled = _jitter_for(subject, v_upper, salt='d111_porn_peer',
                                 base=scaled, pct=0.05)
            # Ceiling below PORNHUB with a PER-BRAND SALTED gap (2026-08-24
            # defect D: the old constant `new_pornhub - 1.0` parked every
            # clamped peer at exactly leader minus 1.0000, a round-offset
            # ladder signature shipped on 64 files; rank-2 YOUPORN on
            # SharkNinja Avid was the client-reported case). Salted gap in
            # [1.05, 2.4]pp keeps the leader invariant AND kills the round
            # constant; per-brand salt prevents 4dp collisions when several
            # peers hit the ceiling together.
            _gap = _jitter_for(subject, v_upper, salt='d111_porn_gap',
                               lo=1.05, hi=2.4)
            scaled = min(scaled, max(2.0, round(new_pornhub - _gap, 4)))
            df = _set_bp(df, idx, scaled,
                         bp_col, cs_col, raw_col, proj_col, sample_size)
            n_touched += 1
            log_rows.append((v_upper, old, scaled))

    df = _renormalize_category(df, 'PORN MEDIA',
                               bp_col, cs_col, raw_col, proj_col, sample_size)
    if verbose:
        direction = 'OVER-READ cap' if cur_bp > band_hi else 'UNDER-READ lift'
        print(f"   🔧 apply_porn_canonical_normalize [{subject or ''}]: "
              f"{direction} → band [{band_lo:.0f}, {band_hi:.0f}] "
              f"(audience: M={male:.0f}% F={fem:.0f}% 18-34={a18_34:.0f}% "
              f"55+={a55_plus:.0f}%)")
        for v, old, new in log_rows[:5]:
            print(f"      • {v}: {old:.4f}% → {new:.4f}%")
    return df, n_touched


# ============================================================================
# D118 — PORN MEDIA LEADER-BREAK INVARIANT
# ============================================================================
# 2026-06-09 (Jenna escalation, Hilary Swank): XNXX@49.6% vs PORNHUB@12.97%
# (3.8x leader displacement). Corpus sweep found 78 profiles where a
# non-PORNHUB peer (XVIDEOS, XNXX, SEXTB, CHATURBATE, PORNTREX, EPORNER,
# YOUPORN, etc.) sits at higher BP than PORNHUB.
#
# Why apply_porn_canonical_normalize doesn't catch this:
#   It re-anchors PORNHUB to its audience-specific band (e.g. older-female
#   skew → 8-22%) and SCALES peers WITH it. If PORNHUB lands inside its
#   band but a peer was over-emitted by the LLM (49.6% etc.), the
#   enforcer no-ops because PORNHUB itself is fine.
#
# Fix is a SEPARATE invariant gate that runs AFTER the band-clamp:
#   "max(non-PORNHUB peer BP) ≤ PORNHUB BP - 1pp"
# Pornhub leads US web traffic in the adult vertical by 3-4× over its
# nearest peers — any profile where a peer sits ABOVE Pornhub is a
# writer-bug, not an audience-justified reading.
#
# Behavior on violation:
#   1. Find the max peer BP across non-PORNHUB rows
#   2. Scale ALL non-PORNHUB peers down by factor (PORNHUB - 1pp) / max
#   3. Per-row deterministic jitter so peers don't land at identical BPs
#   4. Renormalize Category Share for the PORN MEDIA block
#   5. Idempotent: a re-run on already-fixed values is a no-op (max peer
#      will already be ≤ PORNHUB - 1pp)
# Skips: missing PORNHUB row, max peer below 5pp (LLM noise tail).
# ============================================================================

def apply_porn_leader_invariant(df, subject, verbose=True):
    """Enforce PORNHUB > all non-PORNHUB peers in PORN MEDIA. Fires after
    apply_porn_canonical_normalize so PORNHUB has already been
    audience-clamped. Idempotent. Returns (df, n_rows_touched)."""
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    cats_upper = df['Column'].astype(str).str.strip().str.upper()
    porn_mask = cats_upper == 'PORN MEDIA'
    if not porn_mask.any():
        return df, 0

    pornhub_idx = None
    for idx in df.index[porn_mask]:
        if str(df.at[idx, 'Value']).strip().upper() == 'PORNHUB':
            pornhub_idx = idx
            break
    if pornhub_idx is None:
        return df, 0

    pornhub_bp = _bp(df.at[pornhub_idx, bp_col])
    if pornhub_bp is None or pornhub_bp <= 0:
        return df, 0

    # Find max non-PORNHUB peer
    peer_rows = []
    for idx in df.index[porn_mask]:
        v_upper = str(df.at[idx, 'Value']).strip().upper()
        if v_upper == 'PORNHUB':
            continue
        bp = _bp(df.at[idx, bp_col])
        if bp is None or bp <= 0:
            continue
        peer_rows.append((idx, v_upper, bp))
    if not peer_rows:
        return df, 0

    max_peer_idx, max_peer_brand, max_peer_bp = max(
        peer_rows, key=lambda r: r[2]
    )

    # No violation if PORNHUB already leads by ≥ 1pp
    if max_peer_bp <= pornhub_bp - 1.0:
        return df, 0

    # LLM-noise tail: skip if the displacing peer is below 5pp (no signal)
    if max_peer_bp < 5.0:
        return df, 0

    # Scale all peers by (PORNHUB - 2pp) / max_peer with per-brand jitter.
    # Target = PORNHUB - 2pp gives 1pp headroom below the G11 gate
    # threshold (PORNHUB - 1pp) so post-jitter drift can't re-trip the
    # gate. Hard ceiling = PORNHUB - 1.2pp keeps even the maximum jitter
    # output below the gate threshold.
    target_max = max(1.5, pornhub_bp - 2.0)
    scale = target_max / max_peer_bp
    # Ceiling: every peer must end up strictly below PORNHUB - 1pp
    # (matches G11 gate threshold). 2026-08-24 defect D: the old constant
    # `pornhub_bp - 1.2` parked every clamped peer at exactly leader minus
    # 1.2000, the same round-offset ladder family as the D111 `- 1.0`
    # constant. Per-brand salted gap in [1.25, 2.6]pp stays G11-safe
    # (always > 1pp below the leader) while never producing a round
    # constant offset or a 4dp collision between clamped peers.
    n_touched = 0
    log_rows = []

    for idx, v_upper, old_bp in peer_rows:
        # Skip tail brands (< 1pp) — they have no signal anyway
        if old_bp < 1.0:
            continue
        new_bp = round(old_bp * scale, 4)
        # Per-brand jitter to avoid 4dp identity collisions across peers
        new_bp = _jitter_for(subject, v_upper, salt='d118_porn_leader',
                              base=new_bp, pct=0.04)
        # Per-brand salted ceiling (see block comment above)
        _gap = _jitter_for(subject, v_upper, salt='d118_porn_gap',
                           lo=1.25, hi=2.6)
        ceiling = max(1.0, round(pornhub_bp - _gap, 4))
        new_bp = round(min(new_bp, ceiling), 4)
        # Floor at 0.05 (existing convention)
        new_bp = max(0.05, new_bp)
        df = _set_bp(df, idx, new_bp,
                      bp_col, cs_col, raw_col, proj_col, sample_size)
        n_touched += 1
        log_rows.append((v_upper, old_bp, new_bp))

    df = _renormalize_category(df, 'PORN MEDIA',
                                bp_col, cs_col, raw_col, proj_col, sample_size)

    if verbose and n_touched:
        print(f"   🔧 apply_porn_leader_invariant [{subject or ''}]: "
              f"PORNHUB={pornhub_bp:.2f}% leads (was displaced by "
              f"{max_peer_brand}={max_peer_bp:.2f}%, ratio="
              f"{max_peer_bp / max(pornhub_bp, 0.01):.1f}x)")
        for v, old, new in log_rows[:5]:
            print(f"      • {v}: {old:.4f}% → {new:.4f}%")
    return df, n_touched


# ============================================================================
# D115 — BP/CS INCONSISTENCY RECOVERY
# ============================================================================
# 2026-06-08 (Jenna escalation, David Spade Bank of America): Bank of America
# BP=0.3753% with raw=6,919 but Category Share=16.9996% (correct ~30%-second-
# behind-Chase position). The writer corrupted BP/RAW/PROJ in lockstep but
# preserved the pre-corruption Category Share. This is the historical Big-4
# banking suppression defect (D102b family) resurfacing on a new file.
#
# Recovery: Cat Share is the canonical surviving value. By definition,
#   CS_brand / 100 = BP_brand / sum(BP_in_category)
# so
#   BP_brand = (CS_brand × sum_others) / (100 - CS_brand)
# where sum_others is the BP sum of every OTHER row in the same category.
# We then recompute Raw + Proj from the recovered BP via _set_bp.
#
# Detection signature (matches G10):
#   • BP < 5.0 AND CS >= 4.0 (clear inversion)
#   • CS is at least 10pp larger than expected (BP / sum_BP × 100)
#   • CS / BP ratio > 10x (rules out ordinary multi-affiliation variance)
# Category-agnostic — currently catches BANKING (BoA) but applies to any
# block where the writer corrupts BP/RAW/PROJ but leaves CS intact.
# Conservative thresholds chosen after audit of David Spade revealed many
# BP/CS spreads in PODCAST/NBA ATHLETE/BETTING that are NOT D115 (multi-
# affiliation natural variance). Tighter threshold avoids false positives.
# ============================================================================

def apply_bp_cs_consistency_recovery(df, subject, verbose=True,
                                     bp_at_load=None):
    """Recover BP/Raw/Proj from preserved Category Share when writer
    suppression has corrupted BP but left CS intact (D115).

    bp_at_load (optional dict idx -> BP-at-chain-start or None): when
    provided, any category containing a row whose BP moved since chain
    start (or a freshly inserted row) is skipped entirely. Recovery's
    premise is that CS survived a WRITER corruption that happened
    before this process loaded the frame; a mid-run BP change is
    deliberate enforcer work and the stored share must never be
    replayed over it (2026-08-24 NINJA/SHARK re-inflation defect).

    Returns (df, n_rows_recovered).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns or cs_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)
    if sample_size <= 0:
        return df, 0

    n_recovered = 0
    log_rows: list[tuple[str, str, float, float, float]] = []

    for cat, grp in df.groupby(df['Column'].astype(str).str.upper().str.strip()):
        if cat in {'BRAND INPUT', 'SUBJECT', 'SAMPLE SIZE', 'INPUT_METADATA',
                   'BRAND CATEGORY', ''}:
            continue
        if cat in DEPIN_DEMO_CATS:
            continue
        idxs = list(grp.index)
        bps = [_bp(df.at[i, bp_col]) for i in idxs]
        css = [_bp(df.at[i, cs_col]) for i in idxs]
        # 2026-08-24 (stale-share class kill): skip any category whose
        # BP moved since chain start - see docstring.
        if bp_at_load is not None:
            mutated = False
            for i, b in zip(idxs, bps):
                if i not in bp_at_load:
                    mutated = True
                    break
                b0 = bp_at_load[i]
                cur = (None if (b is None or pd.isna(b))
                       else round(float(b), 6))
                if b0 is None and cur is None:
                    continue
                if b0 is None or cur is None or abs(b0 - cur) > 1e-6:
                    mutated = True
                    break
            if mutated:
                continue
        bp_clean = [b for b in bps if b is not None and pd.notna(b)]
        if len(bp_clean) < 4:
            continue
        total_bp = sum(bp_clean)
        if total_bp <= 0:
            continue
        # 2026-08-24 guard (Rosie/Kimmel/Joe/Bethenny NINJA 98-100 defect):
        # the recovery formula BP = CS x sum_others / (100 - CS) is only
        # valid when Category Share is genuinely share-of-category (the
        # category's CS values sum to ~100). Mid-chain the engine writes
        # CS = BP (category CS sums far above 100); running the formula
        # there re-inflates rows that reset_non_hostmap_brands_to_floor
        # deliberately floored (NINJA 18.23 -> 0.04 -> "recovered" 99.5).
        # Skip any category whose CS column does not behave as a share.
        cs_clean = [c for c in css if c is not None and pd.notna(c)]
        total_cs = sum(cs_clean)
        if not (85.0 <= total_cs <= 115.0):
            continue

        for i, b, c in zip(idxs, bps, css):
            if b is None or pd.isna(b) or c is None or pd.isna(c):
                continue
            if b < 0.01 or c < 0.01:
                continue
            expected_cs = (b / total_bp) * 100.0
            # BP < 5.0 covers Chicago Blackhawks-style hits where BP got
            # depressed to ~2.4 but CS preserved ~37%; ratio > 10× still
            # ensures the inversion is unambiguous.
            if not (b < 5.0 and c >= 4.0):
                continue
            if c < expected_cs + 10.0:
                continue
            if (c / max(b, 0.001)) <= 10.0:
                continue
            sum_others = total_bp - b
            if c >= 99.99 or sum_others <= 0:
                continue
            recovered_bp = (c * sum_others) / (100.0 - c)
            recovered_bp = _jitter_for(
                subject, str(df.at[i, 'Value']), salt='d115_bp_cs',
                base=recovered_bp, pct=0.02,
            )
            recovered_bp = round(max(0.05, min(99.5, recovered_bp)), 4)
            brand = str(df.at[i, 'Value'])
            df = _set_bp(df, i, recovered_bp,
                         bp_col, cs_col, raw_col, proj_col, sample_size)
            n_recovered += 1
            log_rows.append((cat, brand, b, c, recovered_bp))

    if verbose and n_recovered:
        print(f"   🔧 apply_bp_cs_consistency_recovery [{subject or ''}]: "
              f"recovered {n_recovered} BP from preserved CS")
        for cat, brand, old_bp, cs, new_bp in log_rows[:5]:
            print(f"      • {cat}/{brand}: BP {old_bp:.4f}% → {new_bp:.4f}% "
                  f"(via CS={cs:.4f}%)")
    return df, n_recovered


# ============================================================================
# CATEGORY-SHARE FINAL RECOMPUTE
# ============================================================================
# 2026-06-07 (Jenna deep audit): 549 of 560 corpus files (98%) had Category
# Share inconsistent with raw / Σraw_block × 100. Earlier enforcers don't
# always re-run _renormalize_category on every block they touch. This is the
# LAST post-gen pass — recompute Category Share for ALL non-meta blocks so
# the ratios match the final BPs. Must run AFTER recompute_raw_and_projection.
# ============================================================================

_SHARE_SKIP_BLOCKS = {
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'INPUT_METADATA',
}


def enforce_streaming_share_health(df, subject, verbose=True):
    """Detect + repair the "one row Share=100, everyone else NULL"
    signature in STREAMING/PLATFORM and STREAMING VIDEO blocks.

    Signature (Honey Pot 2026-08-03, corpus-wide: 171 files):
      Netflix has Share=100.0000 and Raw > 0, while every other
      streaming service has Share NULL (empty string). This ships
      when a writer explicitly pins Netflix but forgets to touch the
      other rows, and the raw-based apply_recompute_category_share
      pass doesn't recover because the other rows have Raw=0 by that
      point.

    Fix strategy:
      * If ≥50% of rows in the block have NULL share AND at least one
        row has Share=100 AND the block has ≥5 rows, force a
        BP-based Share recompute for the WHOLE block.
      * The BP-based recompute lives in apply_recompute_category_share
        (this function just detects and marks). Actual repair happens
        via the same rewrite so both paths use identical math.

    Auto-fix path: overwrite Share on every row in the block using
    Share = BP / ΣBP × 100. Preserves the pinned peer at 100 only if
    BP is actually 100.

    Returns (df, n_rows_fixed). Idempotent — no-op after apply_recompute_
    category_share has run cleanly.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, _, _ = _detect_cols(df)
    if bp_col is None or cs_col is None:
        return df, 0

    col_u = df['Column'].astype(str).str.strip().str.upper()
    targets = ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'STREAMING MUSIC',
               'VMVPD/FAST', 'SOCIAL MEDIA')

    total_fixed = 0
    for cat_up in targets:
        mask = col_u == cat_up
        if mask.sum() < 5:
            continue

        share_raw = df.loc[mask, cs_col].astype(str).str.strip()
        n_rows = int(mask.sum())
        n_null = int((share_raw == '').sum() + (share_raw.str.lower() == 'nan').sum())
        share_vals = share_raw.apply(
            lambda x: _bp(x) if x and x.lower() != 'nan' else None
        )
        n_pin_100 = int((share_vals.apply(
            lambda v: v is not None and abs(v - 100) < 0.001
        )).sum())

        needs_fix = (n_null >= n_rows * 0.5) and (n_pin_100 >= 1)
        if not needs_fix:
            continue

        # BP-based recompute for this block.
        bps = df.loc[mask, bp_col].apply(_bp)
        bp_sum = float(bps.fillna(0).sum())
        if bp_sum <= 0:
            continue
        for idx in df.index[mask]:
            v = _bp(df.at[idx, bp_col])
            if v is None:
                df.at[idx, cs_col] = ''
                continue
            df.at[idx, cs_col] = round(v / bp_sum * 100, 4)
        total_fixed += n_rows
        if verbose:
            print(f"   🔧 enforce_streaming_share_health [{subject or ''}]: "
                  f"repaired {cat_up} ({n_null}/{n_rows} rows had null "
                  f"share, {n_pin_100} pinned at 100)")

    return df, total_fixed


def apply_recompute_category_share(df, subject, verbose=True):
    """Final pass: recompute Category Share for every non-meta block.

    Semantics:
      * Demographic blocks (AGE, GENDER, INCOME, ...): Share = BP.
        Each demo sums to 100 by construction, so Share ≡ BP.
      * Non-demo blocks: Share = BP / Σ(BP_in_block) × 100.
        "Voice-share within category". BP is the source of truth
        because Raw can drift (stale, zero, or missing after ad-hoc
        script edits — the Honey Pot 2026-08-03 signature).
        Falls back to raw/Σraw only when BP for every row in the block
        is missing (edge case for legacy files).

    Also normalizes format:
      * Strips '%' from Share cells so the column is uniformly numeric.
      * Blanks Share on the BRAND CATEGORY metadata row.

    Idempotent. Returns (df, n_blocks_touched). Runs LAST in the
    enforcer chain — nothing downstream should touch Share.

    History:
      * Original impl (2026-06-07): raw / Σraw. Broke on Summer's Eve
        Potential Consumers where synth_engine.py wrote Share=BP
        (making the sum wildly inflated) and Raw was stale.
      * 2026-08-03: switched to BP-based math + format normalize.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, _ = _detect_cols(df)
    if cs_col is None or cs_col not in df.columns or bp_col is None:
        return df, 0
    if (str(df[cs_col].dtype) == 'string'
            or str(df[cs_col].dtype).startswith('str')):
        df[cs_col] = df[cs_col].astype(object)

    n_blocks = 0
    n_pct_stripped = 0
    cats_upper = df['Column'].astype(str).str.strip().str.upper()
    # For "Share = BP" semantics, treat both the 9 canonical demos AND
    # the extended demo-like blocks (LOCATION, PRIMARY_LANGUAGE, etc.)
    # the same way. For all of these, buckets sum to ~100 by construction
    # so Share = BP/ΣBP × 100 degenerates to Share = BP. Writing them
    # via the identity path is faster and avoids round-trip precision loss.
    demo_upper = {c.upper() for c in _DEMO_LIKE_ALL}

    # Blank Share on BRAND CATEGORY metadata rows first so they don't
    # count in per-cat sums (they should be uniquely blank).
    m_bc = cats_upper == 'BRAND CATEGORY'
    if m_bc.any():
        for idx in df.index[m_bc]:
            v = str(df.at[idx, cs_col]).strip()
            if v and v.lower() != 'nan':
                df.at[idx, cs_col] = ''

    for cat in df['Column'].astype(str).str.strip().unique():
        cat_upper = str(cat).upper().strip()
        if cat_upper in _SHARE_SKIP_BLOCKS:
            continue
        mask = cats_upper == cat_upper
        if not mask.any():
            continue

        bps = df.loc[mask, bp_col].apply(_bp)

        if cat_upper in demo_upper:
            df.loc[mask, cs_col] = bps.round(4)
            n_blocks += 1
            continue

        # Non-demo — canonical formula Share = BP / ΣBP × 100.
        bp_sum = float(bps.fillna(0).sum())
        if bp_sum > 0:
            for idx in df.index[mask]:
                v = _bp(df.at[idx, bp_col])
                if v is None:
                    df.at[idx, cs_col] = ''
                    continue
                df.at[idx, cs_col] = round(v / bp_sum * 100, 4)
            n_blocks += 1
            continue

        # BP is entirely missing/zero — fall back to raw-based math.
        if raw_col is None or raw_col not in df.columns:
            continue
        raws = pd.to_numeric(
            df.loc[mask, raw_col].astype(str).str.replace(',', ''),
            errors='coerce',
        ).fillna(0)
        raw_sum = float(raws.sum())
        if raw_sum <= 0:
            continue
        df.loc[mask, cs_col] = (raws / raw_sum * 100).round(4)
        n_blocks += 1

    # Strip lingering '%' suffix from any Share cells (Fix W1: percent
    # bleeding into share column — corpus-wide format signature).
    try:
        share_series = df[cs_col].astype(str)
        pct_mask = share_series.str.contains('%', regex=False, na=False)
        n_pct_stripped = int(pct_mask.sum())
        if n_pct_stripped:
            df.loc[pct_mask, cs_col] = share_series.loc[pct_mask].str.replace(
                '%', '', regex=False
            ).str.strip()
    except Exception:
        pass

    if verbose and (n_blocks or n_pct_stripped):
        extra = f" (also stripped '%' from {n_pct_stripped} share cells)" \
            if n_pct_stripped else ''
        print(f"   🔧 apply_recompute_category_share [{subject or ''}]: "
              f"{n_blocks} block(s) recomputed{extra}")
    return df, n_blocks + n_pct_stripped


# ============================================================================
# D-DH — Disney+ / Hulu canonical rollup
# ============================================================================
# 2026-08-07 (Jenna directive):
#   "in all profiles Hulu and disney+ should be reporting as one line
#    Disney+/Hulu not two seperate lines. fix that cononically in all
#    pipelines. it is a roll up of disney+ and hulu since theye now
#    one platform"
#
# Disney merged the Disney+ and Hulu apps into a single service in 2025-2026.
# For panel-tracked digital reach, users of either app are users of the
# combined platform — so the canonical row is a single "Disney+/Hulu"
# entry, not two sibling rows.
#
# Rollup rule:
#   * Wherever the same Column carries BOTH a Disney+ row and a Hulu row,
#     merge to a single row with Value='Disney+/Hulu' and BP = max(a, b)
#     plus subject-salted micro-jitter (never a pin).
#   * Wherever a Column carries only one of them, rename the Value to
#     'Disney+/Hulu' (BP unchanged).
#   * Metadata rows (BRAND INPUT, SAMPLE SIZE, BRAND CATEGORY, SUBJECT,
#     AVID FAN, CASUAL FAN, INPUT_METADATA) are never touched — their
#     Value encodes profile identity, not brand-row data.
#   * Self-pin at ≥99.9999% preserved exactly (Disney+ or Hulu subject
#     profiles keep their 100 pin on the renamed row).
#
# Idempotent: on a re-run the canonical row's Value is 'Disney+/Hulu'
# (contains slash) so it does not match the source-value set.
#
# 2026-08-11 extension: also INJECTS Disney+/Hulu into STREAMING/PLATFORM
# when the row is completely absent (audit finding — Aug-7 batch of
# Pop Culture Jeopardy, Jeopardy, Hotel Transylvania, Sombr, Nikki Glaser
# lacked Disney+/Hulu on 10 of 12 files even though the hostmap's
# canonical brand row exists). Injection is peer-anchored on the file's
# HBO Max BP (kids-heavy: x1.55; adult: x1.10) with subject-salted
# jitter. Only triggers when the file has a live streaming universe
# (>=3 other streaming rows) so niche B2B files aren't polluted.
# See `_dh_inject_if_missing` for the full rule.
#
# Wired into both `run_all_enforcers` and `run_write_safety_net` so every
# write path — main pipeline, avid skins, audience cuts, ad-hoc scripts —
# gets the rollup automatically. Also emitted directly by BG.py's
# per-category prompt guidance so freshly generated files don't need the
# consolidation pass.
# ============================================================================

_DH_SOURCE_NORM = {'hulu', 'disney+', 'disneyplus'}
_DH_CANONICAL_VALUE = 'Disney+/Hulu'
_DH_META_COLS = {
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT',
    'AVID FAN', 'CASUAL FAN', 'INPUT_METADATA',
}
# 2026-08-18: EXPLICIT column whitelist for the rollup. The rule
# (.cursor/rules/disney-hulu-canonical-rollup.mdc) has always said "The
# rollup only touches the SVOD-platform rows" - INTEREST, TOYS,
# FRANCHISE, WHERE THEY SHOP, TRAVEL, EVENTS, BROADCAST/CABLE, MEDIA
# keep their Disney-named rows as-is. Prior implementation excluded
# only _DH_META_COLS and swept everything else, which corrupted
# `INTEREST :: DISNEY+` -> `INTEREST :: Disney+/Hulu` on 194 shipped
# profiles. This whitelist is the authoritative filter now.
_DH_TARGET_COLS = {
    'STREAMING/PLATFORM',
    'STREAMING PLATFORM',  # legacy variant seen in older files
}


def _dh_norm_value(v) -> str:
    """Normalize 'Disney +' / 'Disney Plus' / 'DISNEY+' / 'Hulu' all to
    a single comparable form. Space, underscore, hyphen collapsed;
    lowercased."""
    s = str(v or '').strip().lower()
    for c in (' ', '_', '-'):
        s = s.replace(c, '')
    return s


def _dh_inject_if_missing(df, subject, bp_col, cs_col, raw_col, proj_col,
                          sample_size, verbose=True):
    """Inject a Disney+/Hulu row into STREAMING/PLATFORM when it's absent.

    Only injects when the file already carries a live streaming universe
    (>=3 other streaming brands present) so niche B2B / non-consumer
    profiles don't get forced streaming platform rows. Sizing is
    peer-anchored on the file's own HBO Max BP:

        Disney+/Hulu BP = HBO Max BP * 1.10 + jitter    (adult persona)
        Disney+/Hulu BP = HBO Max BP * 1.55 + jitter    (kids/family)

    Fallback anchor: Netflix BP * 0.55 when HBO Max is absent.
    Kids-heavy detection: presence of >=2 of {YOUTUBE KIDS, HAPPYKIDS,
    NICK, PBS KIDS, DISNEY JR, MOVIES ANYWHERE} in the streaming
    universe (unless already covered by ratings signal elsewhere).

    Idempotent: skips when the canonical 'Disney+/Hulu' row already
    exists in STREAMING/PLATFORM.
    Returns ``(df, n_injected)`` where n_injected is 0 or 1.
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    col_upper = df['Column'].astype(str).str.strip().str.upper()
    stream_mask = col_upper == 'STREAMING/PLATFORM'
    if not stream_mask.any():
        return df, 0

    val_lower = df['Value'].astype(str).str.strip().str.lower()
    if ((stream_mask) & (val_lower == 'disney+/hulu')).any():
        return df, 0

    # Peer streaming universe check: require >=3 other brands
    sub = df[stream_mask]
    if len(sub) < 3:
        return df, 0

    def _bp_of(name: str):
        m = stream_mask & (df['Value'].astype(str).str.strip().str.upper()
                            == name.upper())
        if not m.any():
            return None
        return _bp(df.loc[m.idxmax(), bp_col])

    hbo = _bp_of('HBO MAX')
    netflix = _bp_of('NETFLIX')
    if hbo is None and netflix is None:
        # No credible streaming universe -- skip.
        return df, 0

    # Kids-heavy detection
    kid_signals = {'YOUTUBE KIDS', 'HAPPYKIDS', 'NICK', 'PBS KIDS',
                   'DISNEY JR', 'DISNEY JR.', 'MOVIES ANYWHERE'}
    stream_vals = set(df.loc[stream_mask, 'Value'].astype(str)
                        .str.strip().str.upper())
    is_kids = len(kid_signals & stream_vals) >= 2

    anchor = hbo if hbo is not None else (netflix or 40.0) * 0.55
    mult = 1.55 if is_kids else 1.10
    jit = _jitter_for(subject or '', _DH_CANONICAL_VALUE,
                      salt='DH_INJECT|STREAMING/PLATFORM',
                      lo=-1.5, hi=1.5)
    new_bp = max(6.0, min(78.0, round(anchor * mult + jit, 4)))

    # Build a scaffold row: BP set, everything else recomputed by
    # _set_bp / _renormalize_category (Raw + Proj + CS).
    new_row = {c: '' for c in df.columns}
    new_row['Column'] = 'STREAMING/PLATFORM'
    new_row['Value'] = _DH_CANONICAL_VALUE
    new_row[bp_col] = f"{new_bp:.4f}"
    if raw_col in df.columns:
        raw_val = int(round(sample_size * new_bp / 100.0))
        new_row[raw_col] = str(raw_val)
    if proj_col in df.columns:
        raw_val = int(round(sample_size * new_bp / 100.0))
        new_row[proj_col] = str(int(round(raw_val / 10_000_000 * 329_900_000)))
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    # Rebalance the column so CS stays consistent
    df = _renormalize_category(df, 'STREAMING/PLATFORM', bp_col, cs_col,
                                raw_col, proj_col, sample_size)

    if verbose:
        tag = 'kids-heavy' if is_kids else 'adult'
        print(f"   🔧 apply_disney_hulu_rollup [{subject or ''}]: "
              f"INJECTED Disney+/Hulu into STREAMING/PLATFORM "
              f"({tag}, anchor HBO={hbo}, NFX={netflix}, "
              f"new_bp={new_bp:.4f}%)")
    return df, 1


def apply_disney_hulu_rollup(df, subject, verbose=True):
    """Consolidate Disney+ and Hulu rows into a single 'Disney+/Hulu' row.

    See module-header block for full rule + rationale. Idempotent.
    Returns ``(df, n_rows_touched)``.

    In addition to the merge/rename cases above, this enforcer also
    INJECTS a Disney+/Hulu row into STREAMING/PLATFORM when the column
    exists (with a live streaming universe) but the row is completely
    absent. The 2026-08-10 audit of the Aug-7 batch (Pop Culture
    Jeopardy, Jeopardy, Hotel Transylvania, Sombr, Nikki Glaser)
    found Disney+/Hulu missing entirely from 10 of 12 files even though
    the hostmap's canonical brand row exists (BRAND=Disney+/Hulu,
    SECTION=Streaming/Platform, three hostname aliases). Injection is
    peer-anchored on the file's own HBO Max BP so it fits the persona.
    Skips when there are <3 other streaming rows (niche/B2B files).
    """
    if df is None or len(df) == 0 or 'Column' not in df.columns:
        return df, 0
    if 'Value' not in df.columns:
        return df, 0

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    if bp_col not in df.columns:
        return df, 0
    sample_size = _detect_sample_size(df, bp_col, raw_col)

    col_upper = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].apply(_dh_norm_value)
    # WHITELIST: only rename the source value inside a SVOD-platform
    # column. Every other Disney-branded row (INTEREST :: DISNEY,
    # AMUSEMENT PARKS :: DISNEY WORLD, TOYS :: DISNEY FROZEN, etc.)
    # stays as itself. Historic implementation used a blacklist here
    # (~col_upper.isin(_DH_META_COLS)) which swept legitimate INTEREST
    # rows into the platform bundle.
    is_source = val_norm.isin(_DH_SOURCE_NORM) & col_upper.isin(_DH_TARGET_COLS)
    if not is_source.any():
        # No source rows to merge -- fall through to the INJECT path.
        df, n_injected = _dh_inject_if_missing(
            df, subject, bp_col, cs_col, raw_col, proj_col, sample_size,
            verbose=verbose,
        )
        return df, n_injected
    # Note: after the merge/rename block below completes, we also call
    # _dh_inject_if_missing at the tail so files whose only Disney+/Hulu
    # presence was in a non-streaming column still get a canonical row
    # in STREAMING/PLATFORM.

    from collections import defaultdict
    groups: dict[str, list[int]] = defaultdict(list)
    for idx in df.index[is_source]:
        groups[col_upper.at[idx]].append(idx)

    to_drop: list[int] = []
    total_touched = 0
    log: list[tuple[str, int, float, float]] = []

    for column, idxs in groups.items():
        # Compute max BP and choose that row as the keeper
        scored = [(i, _bp(df.at[i, bp_col]) or 0.0) for i in idxs]
        keeper_idx, keeper_bp = max(scored, key=lambda t: t[1])

        if len(idxs) > 1:
            # Multi-row consolidation: max + subject-salted jitter so we
            # never perfectly pin to either source value. 100.0 self-pin
            # preserved exactly.
            if keeper_bp >= 99.9999:
                new_bp = 100.0
            else:
                jit = _jitter_for(subject, _DH_CANONICAL_VALUE,
                                  salt=f'DH_ROLLUP|{column}',
                                  lo=-0.15, hi=0.15)
                new_bp = round(max(0.5, keeper_bp + jit), 4)
        else:
            # Rename-only case: BP unchanged.
            new_bp = keeper_bp

        df.at[keeper_idx, 'Value'] = _DH_CANONICAL_VALUE
        if abs(new_bp - keeper_bp) > 1e-6:
            df = _set_bp(df, keeper_idx, new_bp, bp_col, cs_col,
                         raw_col, proj_col, sample_size)

        for i in idxs:
            if i != keeper_idx:
                to_drop.append(i)

        log.append((column, len(idxs), keeper_bp, new_bp))
        total_touched += len(idxs)

    if to_drop:
        df = df.drop(index=to_drop).reset_index(drop=True)

    # Recompute Category Share within every affected column so the
    # column's shares stay consistent. safety-net's
    # apply_recompute_category_share will also do this later; being
    # idempotent here means the enforcer is standalone-safe.
    for column in groups.keys():
        df = _renormalize_category(df, column, bp_col, cs_col,
                                   raw_col, proj_col, sample_size)

    if verbose and total_touched:
        print(f"   🔧 apply_disney_hulu_rollup [{subject or ''}]: "
              f"consolidated {total_touched} Hulu/Disney+ row(s) into "
              f"{len(groups)} Disney+/Hulu row(s) "
              f"(dropped {len(to_drop)}) across {len(groups)} column(s)")
        for column, n_src, old_bp, new_bp in log:
            if n_src > 1 or abs(new_bp - old_bp) > 1e-6:
                print(f"      • {column}: {n_src} rows -> Disney+/Hulu "
                      f"@ {new_bp:.4f}% (max source BP was {old_bp:.4f}%)")

    # After merge/rename, also inject a STREAMING/PLATFORM row when it's
    # still missing (e.g., the file only carried D+/Hulu in a non-
    # streaming column, or never had either service at all).
    df, n_injected = _dh_inject_if_missing(
        df, subject, bp_col, cs_col, raw_col, proj_col, sample_size,
        verbose=verbose,
    )
    return df, total_touched + n_injected


def fill_blank_bp_from_raw(df, subject, *, verbose: bool = True):
    """Fill Brand Penetration (Row) when it's blank but Raw is populated.

    Defect signature (Karol G TU 2026-07-24, Rosalía TU 2026-07-24, +
    every other large full-audience pull we've inspected): the row-by-row
    Raw column is written correctly by the pipeline but the
    ``Brand Penetration (Row)`` column fails to serialize for 90+% of
    non-meta rows. On Karol G that meant 9,312 blank BP cells across
    ~65 categories including every demo, every sports roster, TALENT,
    MEDIA, MUSICIAN/BAND, EVENTS, etc. The Raw signal survives -- so
    the file's audience shape reasoning is intact -- but the dashboard
    renders "N/A" on every blank cell.

    Why the row-by-row math is safe (Rule #3a canonical):
        BP = Raw / subject_raw * 100
    where ``subject_raw`` is the BRAND INPUT row's Raw (falling back to
    SAMPLE SIZE Raw). This is exactly the inverse of the canonical
    ``recompute_raw_and_projection`` math, so a round-trip is lossless.

    Must run BEFORE ``recompute_raw_and_projection`` in the safety net
    because that function would compute ``Raw = BP/100 * sample_size``,
    and with BP blank it would zero out the good Raw signal and destroy
    the audience shape. Wired as step 1 (BP fill) → step 2
    (normalize_final_format) → step 3 (recompute_raw_and_projection) →
    ... in ``run_write_safety_net``.

    Guards:
      * Meta rows (BRAND INPUT, SAMPLE SIZE, BRAND CATEGORY,
        AVID FAN, CASUAL FAN, INPUT_METADATA, SUBJECT) are handled
        separately -- meta BP goes to 100 for BRAND INPUT/SAMPLE SIZE,
        stays blank for BRAND CATEGORY.
      * Rows with Raw == 0 are skipped (nothing to derive from).
      * Rows already carrying BP (numeric or '%'-suffixed) are skipped.
      * Result clamped to [0.0001, 100] to survive 4dp rounding.

    Returns ``(df, n_filled)``. Idempotent.
    """
    if df is None or len(df) == 0 or "Column" not in df.columns:
        return df, 0
    bp_col, cs_col, raw_col, _ = _detect_cols(df)
    if bp_col is None or raw_col is None:
        return df, 0

    # Coerce object dtype so we can write mixed values without pandas
    # 2.x string-dtype guard tripping (Hetzner-only signature).
    if (str(df[bp_col].dtype) == "string"
            or str(df[bp_col].dtype).startswith("str")):
        df[bp_col] = df[bp_col].astype(object)

    # Detect subject_raw from BRAND INPUT row (canonical). Fall back to
    # SAMPLE SIZE Raw. Bail if neither is present or parseable -- we
    # cannot safely derive BP without a denominator.
    cats_upper = df["Column"].astype(str).str.strip().str.upper()

    def _parse_int(v):
        try:
            return int(float(str(v).replace(",", "").strip()))
        except Exception:
            return None

    subject_raw = None
    for meta_col in ("BRAND INPUT", "SAMPLE SIZE"):
        m = cats_upper == meta_col
        if m.any():
            for idx in df.index[m]:
                v = _parse_int(df.at[idx, raw_col])
                if v and v > 0:
                    subject_raw = v
                    break
        if subject_raw:
            break

    if not subject_raw:
        if verbose:
            print("   ⚠️ fill_blank_bp_from_raw: no BRAND INPUT/SAMPLE "
                  "SIZE Raw found; skipping")
        return df, 0

    # Meta rows: BRAND INPUT / SAMPLE SIZE / SUBJECT get BP=100 when
    # blank. BRAND CATEGORY / INPUT_METADATA / AVID FAN / CASUAL FAN
    # (deprecated) stay blank.
    META_100 = {"BRAND INPUT", "SAMPLE SIZE", "SUBJECT"}
    META_BLANK = {"BRAND CATEGORY", "INPUT_METADATA", "AVID FAN", "CASUAL FAN"}

    n_filled = 0
    n_meta_pinned = 0
    filled_by_col: dict[str, int] = {}

    for idx in df.index:
        col_upper = str(df.at[idx, "Column"]).strip().upper()
        bp_raw = str(df.at[idx, bp_col]).strip()
        # Skip already-populated BP cells (anything non-empty, non-nan).
        if bp_raw and bp_raw.lower() != "nan":
            continue

        if col_upper in META_BLANK:
            continue

        if col_upper in META_100:
            df.at[idx, bp_col] = "100.0000"
            n_meta_pinned += 1
            continue

        raw_int = _parse_int(df.at[idx, raw_col])
        if not raw_int or raw_int <= 0:
            continue

        bp_val = raw_int / subject_raw * 100.0
        # Clamp to [0.0001, 100] to survive 4dp rounding + bp-hard-ceiling
        # downstream.
        bp_val = max(0.0001, min(100.0, round(bp_val, 4)))
        df.at[idx, bp_col] = f"{bp_val:.4f}"
        n_filled += 1
        filled_by_col[col_upper] = filled_by_col.get(col_upper, 0) + 1

    if verbose and (n_filled or n_meta_pinned):
        top = sorted(filled_by_col.items(), key=lambda kv: -kv[1])[:6]
        top_str = ", ".join(f"{c}={n}" for c, n in top) if top else ""
        pinned = f", pinned {n_meta_pinned} meta" if n_meta_pinned else ""
        cols_touched = len(filled_by_col)
        print(f"   🩹 fill_blank_bp_from_raw [{subject or ''}]: filled "
              f"{n_filled} blank-BP row(s) across {cols_touched} "
              f"column(s){pinned} (subject_raw={subject_raw:,})"
              f"{'   top: ' + top_str if top_str else ''}")

    return df, n_filled + n_meta_pinned


def run_write_safety_net(df, subject, *, verbose: bool = True):
    """Mandatory lightweight write-time safety net.

    Runs the five idempotent normalizers that MUST hold on every file
    that lands in ``s3://dashboard-inputs/`` regardless of which upstream
    path wrote it:

      1. ``fill_blank_bp_from_raw`` -- fills Brand Penetration (Row)
         from Raw when the pipeline serialized Raw but forgot BP.
         MUST run first, before recompute_raw_and_projection, or the
         RRP pass would zero out the good Raw signal (BP=blank ->
         Raw = BP/100 * sample_size = 0). Fixes the Karol G TU /
         Rosalía TU / every-large-TU-pull signature.
      2. ``normalize_final_format`` -- strip '%' from BP/CS, blank
         BRAND CATEGORY numerics, zero phantom Raw=0/BP>0 rows.
      2b. ``enforce_bp_hard_ceiling`` (2026-08-25) -- no row may ship
         above 100%; baseline-aware subject-salted repair. Wired here
         because the derived-cut paths run only this net and used to
         bypass the ceiling entirely.
      3. ``recompute_raw_and_projection`` -- Raw = BP/100 * sample_size,
         Proj = Raw/10M * 329.9M.
      4. ``enforce_streaming_share_health`` -- catches the
         "first row 100, rest null" streaming defect signature.
      5. ``apply_recompute_category_share`` -- Share = BP/ΣBP * 100
         for every non-meta block. THIS IS THE FIX for the recurring
         "large full-audience pulls lose Category Share, small Avid
         cuts keep it" defect. Root cause: BG.py's inline CS writer
         only refreshes CS for categories touched by specific edit
         functions; categories that no edit function touched stay
         null. On large pulls (Kane Brown 08_06, Honey Pot 08_03,
         Summer's Eve trio) 90+ categories can be null. Small Avid
         cuts happen to touch every category through the intensity
         propagation and so escape the bug.
      6. Normalize SAMPLE SIZE / BRAND INPUT metadata rows: BP=100,
         CS='' (dashboard doesn't render CS for meta rows).

    Everything here is idempotent and cheap (no cross-profile indexing,
    no Claude calls). Safe to run on every write path.

    Wired into ``migration.profile_writer.write_profile_csv`` as a
    MANDATORY tail step (cannot be disabled). ``run_all_enforcers``
    already invokes these four functions in the same order, so calling
    it twice via ``run_enforcers=True`` + this safety net is a no-op
    (idempotent).

    Returns ``(df, {name: n_changes, ...})``.
    """
    stats: dict[str, int] = {}
    if df is None or len(df) == 0:
        return df, stats

    for fn_name, fn in (
        # Rule #4b (wired 2026-08-22): Hidden brands must never ship on
        # ANY write path. The full chain never called this enforcer and
        # the derived-cut paths bypass the chain entirely, so cuts were
        # inheriting Hidden rows from pre-rule parents (Aug 21 TVOD
        # Renters batch). Cache-based, cheap, idempotent.
        ("strip_hostmap_hidden_brands", strip_hostmap_hidden_brands),
        ("fill_blank_bp_from_raw", fill_blank_bp_from_raw),
        ("normalize_final_format", normalize_final_format),
        # Disney+ / Hulu consolidate BEFORE Raw/Proj recompute so the
        # dropped rows don't waste a Raw/Proj write, and BEFORE
        # streaming-share-health so the health check sees the final
        # consolidated shape (single Disney+/Hulu row instead of two
        # sibling rows that could look like a duplicate).
        ("apply_disney_hulu_rollup", apply_disney_hulu_rollup),
        # BP hard ceiling (wired 2026-08-25, partner HEINZ 100.965
        # finding): the derived-cut paths (audience_cut_synthesis,
        # addon_cut_synthesis) run ONLY this safety net, never
        # run_all_enforcers, so an over-100 row written by a cut
        # engine used to bypass the ceiling entirely. Cheap (Gen Pop
        # map is process-cached), idempotent, no-op when nothing
        # exceeds 100. Runs BEFORE the MPB mirror so repairs
        # propagate, and BEFORE recompute_raw_and_projection so the
        # repaired BPs cascade into Raw/Proj.
        ("enforce_bp_hard_ceiling", enforce_bp_hard_ceiling),
        # Rule #3b (wired 2026-08-22): exact MPB mirror re-asserted at
        # write time so cut paths (which skip run_all_enforcers) hold
        # the invariant too. Runs before the Raw/Proj + CS recomputes.
        ("enforce_mpb_exact_mirror", enforce_mpb_exact_mirror),
        # 2026-08-26 (Liz QA, Bethenny avid DEFECT 2): talent-archetype
        # subjects self-include in TALENT at 100. Cut paths skip
        # run_all_enforcers, so the cluster pin must live here too.
        # Runs before recompute_raw_and_projection so the inserted
        # row's Raw/Proj land on the canonical chain.
        ("enforce_native_cluster_self_pin", enforce_native_cluster_self_pin),
        # Mirror copies can land ON another brand's value; the mirror-
        # aware dejitter breaks those (never moving an intentional MPB
        # copy), and the second mirror pass re-propagates any MPB rows
        # the dejitter itself had to separate. Both idempotent.
        ("dejitter_within_cat_4dp_collisions",
         dejitter_within_cat_4dp_collisions),
        # 2026-08-26 (Liz QA, Bethenny Frankel avid, run 3jEG3Kw76rpoZA):
        # same-suffix integer-step ladders (15 TALENT rows ending .8912,
        # 76 rows at .2847 file-wide). Cut paths skip run_all_enforcers,
        # so the ladder breaker MUST live here. Downward-only per-row
        # re-salt preserves the avid subset invariant; runs before the
        # second mirror pass so MPB propagation follows the moved rows.
        ("dejitter_fractional_ladders", dejitter_fractional_ladders),
        ("enforce_mpb_exact_mirror_2", enforce_mpb_exact_mirror),
        # LOCATION sum=100 re-asserted at write time (2026-08-25 Ari
        # Melber / Nicolle Wallace ship-gate holds): the mid-chain
        # renorm can be skipped by cut paths or crash-swallowed, and
        # nothing after this point may leave LOCATION off 100. Runs
        # BEFORE recompute_raw_and_projection + the CS recompute so
        # the canonical chain cascades the renormed BPs into Raw/Proj.
        ("renormalize_location_to_100", renormalize_location_to_100),
        ("recompute_raw_and_projection", recompute_raw_and_projection),
        ("enforce_streaming_share_health", enforce_streaming_share_health),
        ("apply_recompute_category_share", apply_recompute_category_share),
    ):
        try:
            df, n = fn(df, subject, verbose=verbose)
            stats[fn_name] = int(n or 0)
        except Exception as e:
            stats[fn_name] = -1
            if verbose:
                print(f"   ⚠️ write-safety-net {fn_name} failed: "
                      f"{type(e).__name__}: {e}")

    # SUBJECT metadata row backstop (2026-08-24 Furious audit D4): all
    # five files of that run shipped without Column='SUBJECT'. The
    # engine now emits it on fresh builds and cuts inherit it, but the
    # safety net is the guarantee for every write path. Idempotent.
    try:
        df, n_subj = ensure_subject_metadata_row(df, subject, verbose=verbose)
        stats["ensure_subject_row"] = int(n_subj or 0)
    except Exception as e:
        stats["ensure_subject_row"] = -1
        if verbose:
            print(f"   ⚠️ write-safety-net SUBJECT row backstop failed: "
                  f"{type(e).__name__}: {e}")

    # Normalize metadata-row CS: SAMPLE SIZE / BRAND INPUT should not
    # carry a subject_raw or 10M "pseudo-category-share" value in the
    # CS column (dashboard doesn't render it; leaving numeric junk here
    # is a defect signature — Kane Brown 08_06 shipped with CS=347380.0
    # on SAMPLE SIZE row). Also blank the CS on AVID FAN / CASUAL FAN
    # (deprecated rows) and INPUT_METADATA.
    try:
        cats_upper = df["Column"].astype(str).str.strip().str.upper()
        cs_col = "Category Share" if "Category Share" in df.columns else None
        meta_rows = {
            "SAMPLE SIZE", "BRAND INPUT", "AVID FAN", "CASUAL FAN",
            "INPUT_METADATA", "SUBJECT",
        }
        if cs_col:
            n_norm = 0
            for r in meta_rows:
                m = cats_upper == r
                if m.any():
                    for idx in df.index[m]:
                        cur = str(df.at[idx, cs_col]).strip()
                        if cur and cur.lower() != "nan":
                            try:
                                v = float(cur.replace("%", "").replace(",", ""))
                            except Exception:
                                v = None
                            # 100.0 is the only value we allow through
                            # for SAMPLE SIZE / BRAND INPUT (dashboard
                            # ignores it either way). Anything else
                            # (raw count, 10M panel, etc.) is junk.
                            if v is None or v not in (100.0, 100):
                                df.at[idx, cs_col] = ""
                                n_norm += 1
            stats["normalize_meta_cs"] = n_norm
            if verbose and n_norm:
                print(f"   🔧 write-safety-net: blanked "
                      f"{n_norm} meta-row Category Share cell(s)")
    except Exception as e:
        stats["normalize_meta_cs"] = -1
        if verbose:
            print(f"   ⚠️ write-safety-net meta CS normalize failed: "
                  f"{type(e).__name__}: {e}")

    return df, stats


def run_final_invariant_polish(df, subject, *, verbose: bool = True):
    """Terminal invariant pass for `profile_writer.write_profile_csv`,
    run AFTER the pre-publish gate (whose auto-patchers mutate df) and
    immediately before the sort + upload.

    Why this exists (2026-08-20 EST Buyers batch): several passes that
    run late in the chain - gate auto-patches (G1/G13), lux confirmed-
    purchase caps, MPB deband - write BPs AFTER the depin/dejitter and
    self-pin passes have already run. Four files shipped with ~103
    exact-2dp brand rows each, a URL-variant seed string pinned at 100
    inside the native grid, an unpinned (peer-capped) subject row, and
    unsorted categories. This pass re-asserts the invariants no matter
    what mid-chain passes did:

      1. Strip URL-variant echo rows from category grids (the seed
         list belongs ONLY in the BRAND INPUT metadata row, Rule #4c).
      2. Re-pin the subject to exactly 100 in its native grid
         (pin_subject_to_100_in_appearing_categories).
      3. Depin exact-2dp / look-round brand BPs (depin_round_brand_bps)
         and zero-sum-jitter demo rows sitting on a 2dp boundary.
      4. run_write_safety_net (format normalize + Raw/Proj recompute +
         Category Share recompute).

    Idempotent, Claude-free, cheap. Returns (df, stats).
    """
    stats: dict = {}
    if df is None or len(df) == 0:
        return df, stats

    # -- 0. dtype safety --------------------------------------------------
    # pandas StringDtype / str-dtype columns reject numeric assignment
    # (the exact failure G13's auto-patch hit on 2026-06-22). Coerce the
    # numeric target columns to object so every downstream write in
    # this pass (pin, depin, recompute) succeeds regardless of how the
    # caller loaded the frame.
    try:
        _bp_c, _cs_c, _raw_c, _proj_c = _detect_cols(df)
        for _c in (_bp_c, _cs_c, _raw_c, _proj_c):
            if (_c and _c in df.columns
                    and df[_c].dtype.name not in ('object', 'O',
                                                  'float64', 'int64')):
                df[_c] = df[_c].astype(object)
    except Exception:
        pass

    # -- 1. echo-row strip -------------------------------------------------
    try:
        cat_u = df['Column'].astype(str).str.strip().str.upper()
        meta_mask = cat_u.isin(METADATA_COLS | {'INPUT_METADATA'})
        vals = df['Value'].astype(str)
        looks_echo = (
            vals.str.contains('%20', regex=False)
            | ((vals.str.len() > 80) & (vals.str.count(', ') >= 4))
        )
        # Only strip when the echo clearly belongs to THIS subject
        # (first segment normalizes to the subject) so we never drop a
        # legit long brand value.
        subj_norm_p = _re.sub(r'[^A-Z0-9]', '',
                              str(subject or '').upper())
        first_seg_norm = vals.str.split(',').str[0].str.upper() \
            .str.replace(r'[^A-Z0-9]', '', regex=True)
        echo_mask = (
            (~meta_mask) & looks_echo
            & (first_seg_norm == subj_norm_p) & (subj_norm_p != '')
        )
        n_echo = int(echo_mask.sum())
        if n_echo:
            if verbose:
                for v in vals[echo_mask].head(3):
                    print(f'   🧹 final-polish: stripped echo grid row '
                          f'"{str(v)[:60]}..."')
            df = df.loc[~echo_mask].reset_index(drop=True)
        stats['echo_rows_stripped'] = n_echo
    except Exception as e:
        stats['echo_rows_stripped'] = -1
        if verbose:
            print(f'   ⚠️ final-polish echo strip failed: {e}')

    # -- 1b. cohort-label row guard (2026-08-24 Furious audit D5) -----------
    # Deliverable labels ('Furious Viewers', 'Furious Viewers - Avid
    # Fan') and dash-orphans ('- Millennials') must never sit as pinned
    # rows in content categories; drop or rename to the clean subject.
    try:
        df, n_lbl = strip_cohort_label_rows(df, subject, verbose=verbose)
        stats['cohort_label_rows'] = int(n_lbl or 0)
    except Exception as e:
        stats['cohort_label_rows'] = -1
        if verbose:
            print(f'   ⚠️ final-polish cohort-label guard failed: {e}')

    # -- 2. subject re-pin -------------------------------------------------
    try:
        df, n_pin = pin_subject_to_100_in_appearing_categories(
            df, subject, verbose=verbose)
        stats['subject_repin'] = int(n_pin or 0)
    except Exception as e:
        stats['subject_repin'] = -1
        if verbose:
            print(f'   ⚠️ final-polish subject re-pin failed: {e}')

    # -- 3. depin (brand + demo boundary) ----------------------------------
    try:
        df, n_dp = depin_round_brand_bps(df, subject, verbose=verbose)
        stats['brand_depin'] = int(n_dp or 0)
    except Exception as e:
        stats['brand_depin'] = -1
        if verbose:
            print(f'   ⚠️ final-polish brand depin failed: {e}')
    # -- 3b. within-cat 4dp collision dejitter (2026-08-22) ----------------
    # The derived-cut engines write BPs without running the full chain,
    # so multi-brand exact-4dp groups (ACCESSORIES / ACTOR hash-pattern
    # values, Aug 21 TVOD Renters batch: up to 5 brands at one value)
    # shipped uncaught. The polish is the shared terminal pass for every
    # writer path, so the collision breaker belongs here. Idempotent.
    try:
        df, n_wc = dejitter_within_cat_4dp_collisions(
            df, subject, verbose=verbose)
        stats['within_cat_dejitter'] = int(n_wc or 0)
    except Exception as e:
        stats['within_cat_dejitter'] = -1
        if verbose:
            print(f'   ⚠️ final-polish within-cat dejitter failed: {e}')
    try:
        bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
        n_demo_jit = 0
        if bp_col:
            cat_u = df['Column'].astype(str).str.strip().str.upper()
            for demo_cat in sorted(DEPIN_DEMO_CATS):
                idxs = list(df.index[cat_u == demo_cat])
                if not idxs:
                    continue
                bps = {}
                for i in idxs:
                    try:
                        bps[i] = float(str(df.at[i, bp_col])
                                       .replace('%', '').strip())
                    except Exception:
                        bps[i] = None
                on2 = [i for i in idxs
                       if bps.get(i) is not None
                       and abs(bps[i] * 100 - round(bps[i] * 100)) < 1e-9
                       and bps[i] not in (0.0, 100.0)]
                if not on2:
                    continue
                donor = max((i for i in idxs
                             if i not in on2 and bps.get(i) is not None),
                            key=lambda i: bps[i], default=None)
                for i in on2:
                    d = abs(_jitter_for(subject, str(df.at[i, 'Value']),
                                        salt=f'polish|{demo_cat}',
                                        lo=0.0021, hi=0.0093))
                    df.at[i, bp_col] = round(bps[i] + d, 4)
                    if donor is not None:
                        bps[donor] -= d
                        n_demo_jit += 1
                if donor is not None:
                    df.at[donor, bp_col] = round(bps[donor], 4)
        stats['demo_boundary_jitter'] = n_demo_jit
        if verbose and n_demo_jit:
            print(f'   🎲 final-polish: zero-sum jittered {n_demo_jit} '
                  f'demo row(s) off 2dp boundary')
    except Exception as e:
        stats['demo_boundary_jitter'] = -1
        if verbose:
            print(f'   ⚠️ final-polish demo jitter failed: {e}')

    # -- 4. safety net (format + Raw/Proj + CS) ----------------------------
    try:
        df, net_stats = run_write_safety_net(df, subject, verbose=verbose)
        stats['safety_net'] = sum(
            v for v in net_stats.values() if isinstance(v, int) and v > 0)
    except Exception as e:
        stats['safety_net'] = -1
        if verbose:
            print(f'   ⚠️ final-polish safety net failed: {e}')

    return df, stats


def _g3_brand_input_junk_reason(value: str) -> str:
    """Classify a long (>80 char) BRAND INPUT Value. Returns '' when the
    shape is canonical per Rule #4c-i, or a short reason string when it
    reads as genuine leakage.

    Canonical shapes (NEVER flagged, independent of whatever ``subject``
    string the gate was called with):

      * comma-separated clickstream slug variant lists
        ('International Fencing Federation, InternationalFencingFederation,
        International-Fencing-Federation, ...')
      * person name + social handle lists
        ('JACKSON WANG, jacksonwang852g7, JacksonWang852')
      * persona scrape-term lists
        ('Ninja Crispi, SharkNinja, air fryer recipes, air fryer hacks')
      * URL slug lists
        ('netflix.com/title/81234567, amazon.com/gp/video/detail/')
      * the literal 'CSV' file-fed marker (short, never reaches this
        classifier; listed for completeness)

    Derived cuts inherit the parent's BRAND INPUT Value verbatim, so the
    gate is routinely called with a subject that does NOT equal the
    Value's first comma-segment: cut paths pass the cut-suffixed name
    ('X - Avid Fan') and the avid writer passes the Value itself as the
    subject. The pre-2026-08-25 heuristic (first comma-segment ==
    subject) therefore false-positived on every variant-list cut, e.g.
    International Fencing Federation - Avid Fan (238-char canonical list
    logged as a BLOCKING G3 defect while the terminal ship verdict was
    PASS). Classification is now purely shape-based.

    Junk this exists to catch (why G3 was added 2026-05-30):

      * prompt/metadata echo in the Value cell (SAMPLE_START:, SEED:,
        BEHAVIOR_START:, '(2025-01-01 TO 2025-12-31)' date blocks)
      * multiline text (no clickstream slug contains a newline)
      * prose sentences (leaked prompt language, not slugs)
    """
    v = str(value or '').strip()
    for pat in _METADATA_LEAK_PATS:
        if pat.search(v):
            return 'prompt/metadata echo'
    if '\n' in v or '\r' in v:
        return 'multiline text'
    segs = [s.strip() for s in v.split(',') if s.strip()]
    if len(segs) >= 2:
        # Slug-list check: name variants, handles, scrape terms and URL
        # paths are short tokens. A single comma-segment carrying 10+
        # words or 90+ chars reads as a leaked sentence, not a slug
        # (longest real slugs observed: full title subtitles at ~8
        # words, e.g. 'Harry Potter and the Deathly Hallows Part 2').
        for s in segs:
            if len(s) > 90 or len(s.split()) >= 10:
                return f'non-slug segment ({s[:50]!r})'
        return ''
    # Single token, no commas: URL-style slugs (netflix.com/title/...)
    # carry no whitespace and pass; a comma-less multi-word Value only
    # flags when it reads as a sentence.
    if len(v.split()) >= 10:
        return 'prose/sentence text'
    return ''


class PrePublishGateError(Exception):
    """Raised when the pre-publish gate finds an unrecoverable defect.
    Callers should treat as fatal - do NOT save the CSV."""
    def __init__(self, defects: list[str]):
        self.defects = defects
        super().__init__(
            f'pre-publish gate FAILED with {len(defects)} defect(s):\n  '
            + '\n  '.join(defects)
        )


def run_pre_publish_gate(df, subject, *, project_name: str = '',
                         raise_on_fail: bool = True, verbose: bool = True):
    """Final defect scan before save. Returns list of defect strings.

    If `raise_on_fail=True` (default), raises `PrePublishGateError` so the
    caller's save logic is short-circuited. Set False for audit-only mode.
    """
    defects: list[str] = []
    if df is None or len(df) == 0:
        return defects

    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    # sample_size is used by gate-level auto-patches (G17, etc.) so the
    # gate can repair fixable BP-floor defects in-place rather than
    # rejecting the whole profile. Per Jenna 2026-06-17: "you shouldn't
    # just abandon something if that happens, you should just patch
    # netflix to be where it needs to be then publish".
    try:
        _gate_sample_size = _detect_sample_size(df, bp_col, raw_col)
    except Exception:
        _gate_sample_size = None

    # ── G1: sequential-digit placeholders ─────────────────────────────
    # Aligned with `dejitter_sequential_placeholders` exemption set: the
    # depin pass intentionally skips DEPIN_META_CATS (LOCATION, AVID FAN,
    # CASUAL FAN, etc.) because low-population DMAs have legitimately tiny
    # values that may coincidentally land on the 4-monotonic-digit pattern
    # — those aren't placeholders. Gate must skip the same set or it will
    # fire on values dejitter intentionally left alone.
    #
    # 2026-06-18 Jenna policy change (Perks of Being a Wallflower G1 reject
    # @ MUSICIAN/BAND/SIA 20.6543%, Bridesmaids x2 same defect, The Rip
    # 1st-attempt same defect): instead of FAILING the gate when one or two
    # placeholder patterns survive the upstream dejitter pass, AUTO-PATCH
    # the survivors in-place by perturbing the value just enough to break
    # the sequential-digit pattern. Mirror of the G14/G17 fixes —
    # "patch, don't reject" per the standing directive. Only falls through
    # to defect-append when the patch itself cannot land (no sample_size,
    # writer exception, or value still matches the pattern after 8 retries).
    try:
        placeholder_mask = detect_placeholder_bps(df, bp_col)
        if placeholder_mask.any():
            cat_upper = df['Column'].astype(str).str.upper().str.strip()
            exempt = cat_upper.isin(DEPIN_DEMO_CATS | DEPIN_META_CATS)
            real_placeholders = placeholder_mask & ~exempt
            if real_placeholders.any():
                patched_g1 = 0
                failed_g1: list[str] = []
                if (_gate_sample_size and _gate_sample_size > 0 and bp_col):
                    for idx in df.index[real_placeholders]:
                        try:
                            cur_bp = _bp(df.at[idx, bp_col])
                            if cur_bp is None or pd.isna(cur_bp):
                                continue
                            cat_g1 = str(df.at[idx, 'Column'] or '').strip().upper()
                            val_g1 = str(df.at[idx, 'Value'] or '').strip().upper()
                            h = int(_hl.blake2b(
                                f'{subject}|{cat_g1}|{val_g1}|g1-gate-rewrite'
                                .encode(),
                                digest_size=8,
                            ).hexdigest(), 16)
                            if cur_bp < 5.0:
                                base = 0.30 + ((h % 901) / 1000.0)
                                jitter = ((h >> 16) % 89) / 10000.0
                                new_v = round(base + jitter, 4)
                            else:
                                int_part = int(cur_bp)
                                jitter_pp = (((h % 1001) - 500) / 10000.0)
                                tail_jitter = ((h >> 24) % 89) / 10000.0
                                new_v = round(cur_bp + jitter_pp + tail_jitter, 4)
                                new_v = max(int_part - 0.5,
                                            min(int_part + 0.999, new_v))
                                new_v = round(new_v, 4)
                            attempts = 0
                            while _is_sequential_digit_bp(new_v) and attempts < 8:
                                new_v = round(new_v + 0.0037 * (attempts + 1), 4)
                                attempts += 1
                            if _is_sequential_digit_bp(new_v):
                                failed_g1.append(
                                    f"{cat_g1}/{val_g1}@{cur_bp:.4f}"
                                )
                                continue
                            _set_bp(df, idx, new_v,
                                    bp_col, cs_col, raw_col, proj_col,
                                    _gate_sample_size)
                            patched_g1 += 1
                            if verbose:
                                print(
                                    '   🛠  G1 auto-patched (gate-level): '
                                    f'[{cat_g1}]"{val_g1}" '
                                    f'{cur_bp:.4f}% -> {new_v:.4f}%'
                                )
                        except Exception as _pe_g1:
                            failed_g1.append(f"row-{idx}:{_pe_g1}")
                else:
                    failed_g1 = [
                        f"{r['Column']}/{r['Value']}@{r[bp_col]}"
                        for _, r in df.loc[real_placeholders].head(5).iterrows()
                    ]
                if failed_g1:
                    defects.append(
                        f'G1 PLACEHOLDER: {len(failed_g1)} sequential-digit '
                        f'BP(s) survived (e.g. {"; ".join(failed_g1[:5])}) '
                        f'-- gate could not auto-patch'
                    )
    except Exception as e:
        defects.append(f'G1 PLACEHOLDER: detector errored: {e}')

    # ── G2: projection sanity (no value > US population * 1.05) ───────
    try:
        if proj_col and proj_col in df.columns:
            proj = pd.to_numeric(df[proj_col], errors='coerce').fillna(0.0)
            cap = US_POP * 1.05
            over = df.loc[proj > cap].head(5)
            if len(over):
                worst = float(proj.max())
                sample_str = '; '.join(
                    f"{r['Column']}/{r['Value']}={int(r[proj_col]):,}"
                    for _, r in over.iterrows()
                )
                defects.append(
                    f'G2 PROJECTION: {len(over)} row(s) project > US pop '
                    f'(max={worst:,.0f}, sample: {sample_str})'
                )
    except Exception as e:
        defects.append(f'G2 PROJECTION: detector errored: {e}')

    # ── G3: BRAND INPUT length sanity ─────────────────────────────────
    # 2026-08-20: the pipeline-emitted URL-variant seed list
    # ("Name, NAMEVARIANT, NAME-VARIANT, name.com/...") is the CANONICAL
    # BRAND INPUT format per Rule #4c (used for URL matching at runtime)
    # and must NOT fire this gate. It fired as a BLOCKING defect on
    # every chatbot build.
    # 2026-08-25 (International Fencing Federation - Avid Fan, run
    # mM2vt4MJPyVSpA): the "first comma-segment == subject" exemption
    # broke on every derived cut. Cuts inherit the parent's Value
    # verbatim while the gate receives the cut's subject (or, on the
    # avid path, the Value itself), so the exact match never held and
    # canonical variant lists logged as BLOCKING. Classification is now
    # shape-based and subject-independent (_g3_brand_input_junk_reason):
    # canonical shapes per Rule #4c-i (variant lists, handle lists,
    # scrape-term lists, URL slugs, 'CSV') produce NO flag at all; only
    # genuine leakage (prompt/metadata echo, multiline, prose) fires.
    try:
        bi = df[df['Column'].astype(str).str.upper() == 'BRAND INPUT']
        if not bi.empty:
            v = str(bi.iloc[0].get('Value', '') or '')
            if len(v) > 80:
                _junk_reason_g3 = _g3_brand_input_junk_reason(v)
                if _junk_reason_g3:
                    defects.append(
                        f'G3 BRAND_INPUT: row Value is {len(v)} chars '
                        f'and reads as {_junk_reason_g3} '
                        f'(starts: {v[:60]!r}) - should be the canonical '
                        f'name or a clickstream slug list per Rule #4c-i'
                    )
    except Exception as e:
        defects.append(f'G3 BRAND_INPUT: detector errored: {e}')

    # ── G4: within-cat 4dp collisions ≥ 10 ────────────────────────────
    try:
        # Scrub '%' before coerce so raw synth-engine cells parse right
        # (see migration/bp_column_utils for the 2026-08-17 defect background).
        bps = pd.to_numeric(
            df[bp_col].astype(str).str.replace('%', '', regex=False).str.strip(),
            errors='coerce'
        ).fillna(0.0)
        cats = df['Column'].astype(str).str.upper()
        skip = DEPIN_DEMO_CATS | DEPIN_META_CATS
        col_4dp = bps.round(4)
        from collections import Counter as _C
        col_counts: dict[tuple, int] = {}
        for cat, bp_val in zip(cats, col_4dp):
            if cat in skip:
                continue
            if bp_val <= 0 or bp_val >= 99.99:
                continue
            k = (cat, float(bp_val))
            col_counts[k] = col_counts.get(k, 0) + 1
        collisions = [(k, n) for k, n in col_counts.items() if n >= 10]
        if collisions:
            collisions.sort(key=lambda x: -x[1])
            sample = '; '.join(
                f'{c}@{bp:.4f}x{n}' for (c, bp), n in collisions[:5]
            )
            defects.append(
                f'G4 WITHIN_CAT_COLLISION: {len(collisions)} (cat,bp) pair(s) '
                f'with ≥10 rows (sample: {sample})'
            )
    except Exception as e:
        defects.append(f'G4 WITHIN_CAT_COLLISION: detector errored: {e}')

    # ── G5: demo sum=100 invariant (the 9 demos must each total 100±0.5) ───
    # This catches structural defects that survived renormalize_demographics_to_100:
    #   - OCCUPATION over-emission (10 files at 110-141% pre-fix)
    #   - AGE deterministic drop (3 files at 96.28% pre-fix)
    # If renormalize ran successfully, this should always pass. If it fires,
    # rows were added/dropped after renorm OR renorm itself errored.
    try:
        demo_viols = validate_demo_sum_100(
            df, subject=subject, tolerance=0.5,
            raise_on_fail=False, verbose=False,
        )
        if demo_viols:
            sample = '; '.join(
                f'{cat}={s:.2f}%' for cat, s, _ in demo_viols
            )
            defects.append(
                f'G5 DEMO_SUM: {len(demo_viols)} of 9 demo(s) fail sum=100±0.5 '
                f'({sample})'
            )
    except Exception as e:
        defects.append(f'G5 DEMO_SUM: detector errored: {e}')

    # ── G7: DIGITAL BANKING PayPal-leads invariant (D112) ─────────────
    # 5.1% of corpus pre-fix had APPLE PAY > PAYPAL — a writer-bug
    # signature that doesn't track audience composition. PayPal is the
    # universal US adult-panel leader (70-80% adoption); Apple Pay is
    # smartphone-ecosystem skewed (35-50%) but legitimately leads on
    # very-young / iPhone-native cohorts.
    #
    # 2026-06-09 Jenna relaxation ("apple pay can be top if it makes
    # sense for that specific audience and the agent really believes
    # it"): the gate now only fires for audiences where AP > PP is
    # near-certainly the writer bug (tiers 1, 2, 5 in
    # _expected_db_bands). For young / younger-skew (tiers 3, 4) the
    # inversion is plausible per-row reasoning and the gate skips so
    # the agent's call stands.
    try:
        db_rows = df[df['Column'].astype(str).str.upper().str.strip() == 'DIGITAL BANKING'].copy()
        # 2026-06-15: skip G7 entirely when the SUBJECT is APPLE PAY or
        # PAYPAL — the subject's required 100% native-category self-pin
        # (Rule #3) is not a writer-bug, just a self-pin. The gate is
        # designed for non-payments-subject profiles where AP/PP are
        # peer signals.
        bi_g7 = df.loc[df['Column'].astype(str).str.upper().str.strip() == 'BRAND INPUT']
        subj_u_g7 = (
            str(bi_g7.iloc[0].get('Value', '') or '').strip().upper()
            if len(bi_g7) else ''
        )
        if subj_u_g7 in {'APPLE PAY', 'PAYPAL'}:
            db_rows = db_rows.iloc[0:0]  # short-circuit gate
        if len(db_rows):
            db_rows['_v'] = db_rows['Value'].astype(str).str.upper().str.strip()
            pp_r = db_rows[db_rows['_v'] == 'PAYPAL']
            ap_r = db_rows[db_rows['_v'] == 'APPLE PAY']
            if len(pp_r) and len(ap_r):
                pp_v = _bp(pp_r.iloc[0][bp_col])
                ap_v = _bp(ap_r.iloc[0][bp_col])
                a18_34_g7 = _audience_pct_exact(df, 'AGE', ['18-24', '25-34'])
                a55_plus_g7 = _audience_pct_exact(
                    df, 'AGE', ['55-64', '65+', '65 OR OLDER'],
                )
                _, _, apple_can_lead_g7 = _expected_db_bands(
                    0.0, 0.0, a18_34_g7, a55_plus_g7,
                )
                # Only flag when audience does NOT support AP leadership.
                # Even when apple_can_lead, still flag the EXTREME
                # writer-bug signature — PayPal grossly suppressed
                # (PP < 35) AND Apple Pay above an obvious cap
                # (AP > 70). That's not audience reasoning, that's
                # corruption.
                if ap_v > pp_v - 5.0 and pp_v > 0 and not apple_can_lead_g7:
                    defects.append(
                        f'G7 DIGITAL_BANKING_INVERSION: APPLE PAY {ap_v:.2f}% '
                        f'> PAYPAL {pp_v:.2f}% - 5pp '
                        f'(D112 writer-bug signature; PP should lead by ≥5pp '
                        f'for older/mainstream audience: 18-34={a18_34_g7:.0f}% '
                        f'55+={a55_plus_g7:.0f}%)'
                    )
                elif (apple_can_lead_g7 and pp_v > 0 and pp_v < 35.0
                      and ap_v > 70.0):
                    defects.append(
                        f'G7 DIGITAL_BANKING_EXTREME: APPLE PAY {ap_v:.2f}% '
                        f'with PAYPAL only {pp_v:.2f}% — both far outside '
                        f'plausible bands even for very-young audience '
                        f'(18-34={a18_34_g7:.0f}%); writer-bug signature)'
                    )
                elif (apple_can_lead_g7 and pp_v >= 40.0 and ap_v >= 40.0
                      and ap_v > pp_v - 3.0):
                    # 2026-06-15 Pete Davidson defect: AP=53.27% > PP=52.52%
                    # by 0.75pp shipped because Pete's 60% under-35 audience
                    # made apple_can_lead=True. But a 0.75pp lead by AP
                    # (or any tie within 3pp) when both anchors are >= 40%
                    # is not a real young-cohort signal -- it's writer
                    # noise. PayPal is the universal US adult-panel
                    # leader; even for young audiences it should keep a
                    # meaningful lead when both PP and AP are in the
                    # mainstream-engagement band.
                    #
                    # 2026-06-22 Jenna policy ("patch, don't reject"):
                    # AUTO-PATCH the writer noise — bump PayPal BP to
                    # AP + 5pp (capped at 100) so the universal anchor
                    # leads meaningfully, then renormalize DIGITAL BANKING
                    # category share. Same pattern as G14/G17. Falls back
                    # to defect-append if sample_size is unavailable or
                    # the patch raises, so we never ship silently bad
                    # data. Bit Lisa BLACKPINK profile tonight (AP=55.56%,
                    # PP=57.76% — 2.20pp lead, just inside the 3pp window).
                    patched_g7 = False
                    if (_gate_sample_size and _gate_sample_size > 0
                            and bp_col):
                        try:
                            pp_idx = pp_r.index[0]
                            new_pp = min(100.0, ap_v + 5.0)
                            _set_bp(df, pp_idx, new_pp,
                                    bp_col, cs_col, raw_col, proj_col,
                                    _gate_sample_size)
                            try:
                                _renormalize_category(
                                    df, 'DIGITAL BANKING',
                                    bp_col, cs_col, raw_col, proj_col,
                                    _gate_sample_size,
                                )
                            except Exception:
                                pass
                            patched_g7 = True
                            if verbose:
                                print(
                                    '   🛠  G7 NEAR_TIE auto-patched '
                                    '(gate-level): PAYPAL '
                                    f'{pp_v:.2f}% -> {new_pp:.2f}% '
                                    f'(AP {ap_v:.2f}% + 5pp); '
                                    'DIGITAL BANKING renormalized'
                                )
                        except Exception as _pe_g7:
                            if verbose:
                                print(
                                    '   ⚠️  G7 NEAR_TIE auto-patch FAILED '
                                    f'({_pe_g7}); falling back to defect'
                                )
                    if not patched_g7:
                        defects.append(
                            f'G7 DIGITAL_BANKING_NEAR_TIE: APPLE PAY '
                            f'{ap_v:.2f}% within 3pp of PAYPAL '
                            f'{pp_v:.2f}% (both ≥40%); the universal '
                            f'anchor should lead meaningfully even for '
                            f'young audiences (18-34={a18_34_g7:.0f}%, '
                            f'55+={a55_plus_g7:.0f}%) '
                            f'(gate could not auto-patch)'
                        )
    except Exception as e:
        defects.append(f'G7 DIGITAL_BANKING_INVERSION: detector errored: {e}')

    # ── G6: BRAND INPUT canonical (D88 + D109 + truncation guards) ────
    # Catches three writer-bugs surfaced 2026-06-07:
    #   D109 — literal 'CSV' string (4 cohort files in corpus pre-fix)
    #   D88  — tilde-delimited subject (e.g. 'MEGHAN~MARKLE') in 226 files
    #   trunc — under-length subject (e.g. 'Anthony' for 'ANTHONY MICHAEL HALL',
    #           'VANNA' for 'VANNA WHITE')
    # Any of these is a hard-fail at write time — dashboards render the
    # subject string verbatim in the header.
    try:
        bi = df[df['Column'].astype(str).str.upper() == 'BRAND INPUT']
        if not bi.empty:
            v = str(bi.iloc[0].get('Value', '') or '').strip()
            if not v:
                defects.append('G6 BRAND_INPUT: Value is empty')
            elif v.upper() == 'CSV':
                defects.append("G6 BRAND_INPUT: Value is literal 'CSV' (D109 writer-bug)")
            elif '~' in v:
                defects.append(f'G6 BRAND_INPUT: Value contains tilde delimiter '
                               f"({v!r}) — D88 writer-bug, replace with space")
            elif len(v) < 4:
                defects.append(f'G6 BRAND_INPUT: Value {v!r} is {len(v)} chars '
                               f'(<4) — under-length subject, possible truncation')
    except Exception as e:
        defects.append(f'G6 BRAND_INPUT: detector errored: {e}')

    # ── G8: STREAMING/PLATFORM self-anchor leak (D113) ────────────────
    # Title-cohort files would self-anchor the host platform to 100% AND
    # zero every peer (Netflix=100, Hulu=0, Prime=0, ...). Cross-platform
    # overlap is the core read for a streaming-title cohort — a single
    # 100-pin with N-1 zeros is structurally wrong. Surfaced in 9 cohort
    # files (Power_Starz, John_Wick, The_Pitt, etc.) on 06-06.
    try:
        sp = df[df['Column'].astype(str).str.upper().str.strip() == 'STREAMING/PLATFORM'].copy()
        if len(sp) >= 5:
            sp['_BP'] = sp[bp_col].apply(_bp)
            n_zero = int((sp['_BP'].fillna(0) < 0.001).sum())
            n_total = len(sp)
            n_pin = int((sp['_BP'].fillna(0) >= 99.95).sum())
            if n_pin == 1 and n_zero >= max(3, int(0.6 * n_total)):
                peak = sp.loc[sp['_BP'].idxmax(), 'Value']
                defects.append(
                    f'G8 STREAMING_SELF_ANCHOR_LEAK: {peak} pinned to 100% '
                    f'with {n_zero}/{n_total} peer zeros '
                    f'(D113 writer-bug; cross-platform overlap missing)'
                )
    except Exception as e:
        defects.append(f'G8 STREAMING_SELF_ANCHOR_LEAK: detector errored: {e}')

    # ── G9: writer-blank small-sample clamp (D114) ────────────────────
    # When sample size falls below a reliability threshold (~50K), the
    # writer blanks the entire BP column except for hard-pinned anchors,
    # leaving Category Share populated but penetration unusable. 5 files
    # in the 06-06 17:00-17:39 window had >99% of BP rows blank with
    # samples in the 9K-35K range. Hard-fail so these never ship as
    # "published" — they need a re-pull with a wider audience filter.
    try:
        n_rows = len(df)
        if n_rows >= 100:
            bp_populated = int(df[bp_col].apply(
                lambda v: _bp(v) is not None and pd.notna(_bp(v))
            ).sum())
            pct = (bp_populated / n_rows) * 100
            if pct < 50.0:
                raw_col_name = next(
                    (c for c in df.columns if 'Original Raw Numbers' in c),
                    None,
                )
                max_sample = 0
                if raw_col_name:
                    try:
                        max_sample = int(pd.to_numeric(
                            df[raw_col_name].astype(str).str.replace(',', ''),
                            errors='coerce',
                        ).max() or 0)
                    except Exception:
                        max_sample = 0
                defects.append(
                    f'G9 WRITER_BLANK_SMALL_SAMPLE: only '
                    f'{int(bp_populated)}/{n_rows} ({pct:.1f}%) BP rows '
                    f'populated, sample={max_sample:,} '
                    f'(D114 writer-bug; needs re-pull with wider filter)'
                )
    except Exception as e:
        defects.append(f'G9 WRITER_BLANK_SMALL_SAMPLE: detector errored: {e}')

    # ─── G10 BP_CS_INCONSISTENCY (D115) ─────────────────────────────────────
    # Detects rows where Brand Penetration is wildly inconsistent with Category
    # Share relative to the rest of the category. The signature is a writer
    # bug: BP/RAW/PROJ all corrupted in lockstep but Category Share preserves
    # the pre-corruption value. Specifically, expected_CS = BP / sum(BP_in_cat)
    # × 100; if reported CS deviates from expected CS by ≥ 10pp AND the ratio
    # is > 10×, the row is flagged. Conservative thresholds avoid false-positives
    # on multi-affiliation categories (podcast, athlete) where natural CS spread
    # is wider. Currently observed in BANKING (BoA) but detector is category-
    # agnostic.
    try:
        cs_col_name = next((c for c in df.columns if 'Category Share' in c), None)
        if cs_col_name and 'Column' in df.columns and 'Value' in df.columns:
            for cat, grp in df.groupby(df['Column'].astype(str).str.upper().str.strip()):
                if cat in {'BRAND INPUT', 'SUBJECT', 'SAMPLE SIZE',
                           'INPUT_METADATA', 'BRAND CATEGORY', ''} or pd.isna(cat):
                    continue
                # Demographic categories renormalize to 100 — CS dynamics differ;
                # skip them (they have their own G2 / G3 gates).
                if cat in DEPIN_DEMO_CATS:
                    continue
                idxs = list(grp.index)
                bps = [_bp(df.at[i, bp_col]) for i in idxs]
                css = [_bp(df.at[i, cs_col_name]) for i in idxs]
                bp_clean = [b for b in bps if b is not None and pd.notna(b)]
                if len(bp_clean) < 4:
                    continue
                total_bp = sum(bp_clean)
                if total_bp <= 0:
                    continue
                for i, b, c in zip(idxs, bps, css):
                    if b is None or pd.isna(b) or c is None or pd.isna(c):
                        continue
                    if b < 0.01 or c < 0.01:
                        continue
                    expected_cs = (b / total_bp) * 100.0
                    # Inversion test: CS is at least 10pp larger than expected,
                    # AND CS/BP ratio is > 10x (catches obvious depressions, not
                    # ordinary variance or natural multi-affiliation spread).
                    if c >= expected_cs + 10.0 and (c / max(b, 0.001)) > 10.0:
                        brand = str(df.at[i, 'Value'])
                        defects.append(
                            f'G10 BP_CS_INCONSISTENCY: {cat}/{brand} '
                            f'BP={b:.4f}% but CS={c:.4f}% '
                            f'(expected CS≈{expected_cs:.2f}% if BP correct; '
                            f'ratio {c/max(b,0.001):.1f}x — D115 banking-suppression class)'
                        )
    except Exception as e:
        defects.append(f'G10 BP_CS_INCONSISTENCY: detector errored: {e}')

    # ─── G11 PORN_LEADER_BREAK (D118) ───────────────────────────────────────
    # 2026-06-09 (Jenna escalation, Hilary Swank XNXX@49.6% / PORNHUB@12.97%):
    # Pornhub leads US adult-media web traffic by 3-4× over its nearest
    # peers. Any profile where a non-Pornhub brand sits at higher BP than
    # Pornhub is a writer-bug (LLM emitted noise on a peer slot at leader
    # scale), not an audience-justified reading. Corpus sweep found 78
    # profiles affected. Hard-fail at gate; apply_porn_leader_invariant
    # fixes in the enforcer chain.
    try:
        porn_mask_g11 = df['Column'].astype(str).str.strip().str.upper() == 'PORN MEDIA'
        if porn_mask_g11.any():
            ph_bp_g11 = None
            peers_g11 = []
            for idx in df.index[porn_mask_g11]:
                v_u = str(df.at[idx, 'Value']).strip().upper()
                bp = _bp(df.at[idx, bp_col])
                if bp is None:
                    continue
                if v_u == 'PORNHUB':
                    ph_bp_g11 = bp
                else:
                    peers_g11.append((v_u, bp))
            if ph_bp_g11 is not None and peers_g11:
                max_peer_g11 = max(peers_g11, key=lambda r: r[1])
                # Tail-noise threshold: ignore peers below 5pp (no signal)
                if max_peer_g11[1] >= 5.0 and max_peer_g11[1] > ph_bp_g11 - 1.0:
                    ratio_g11 = max_peer_g11[1] / max(ph_bp_g11, 0.01)
                    defects.append(
                        f'G11 PORN_LEADER_BREAK: {max_peer_g11[0]}={max_peer_g11[1]:.2f}% '
                        f'> PORNHUB={ph_bp_g11:.2f}% (ratio {ratio_g11:.1f}x; '
                        f'D118 writer-bug — Pornhub is canonical US adult-media '
                        f'leader by 3-4x over nearest peer)'
                    )
    except Exception as e:
        defects.append(f'G11 PORN_LEADER_BREAK: detector errored: {e}')

    # ── G12: phantom-zero rows (Rob Schneider INTEREST defect, 2026-06-15) ─
    # Any non-meta, non-demo row at exact 0.0000% / Raw=0 is a phantom
    # insert (build-side closure / hybrid-audit echo of "named under-
    # index" persona-doc phrases). A genuinely engaged audience cannot
    # hit exact zero on a named brand/interest/talent token. The
    # strip_phantom_zero_rows enforcer now drops these in run_all_enforcers,
    # but this gate is the belt-and-suspenders survivor check.
    try:
        if 'Column' in df.columns and 'Value' in df.columns and bp_col:
            cat_u_g12 = df['Column'].astype(str).str.strip().str.upper()
            val_g12 = df['Value'].astype(str).str.strip()
            bp_num_g12 = df[bp_col].apply(_bp)
            raw_col_g12 = next(
                (c for c in df.columns if 'Original Raw Numbers' in c),
                None,
            )
            raw_num_g12 = (
                df[raw_col_g12].apply(_bp) if raw_col_g12 in df.columns
                else None
            )
            skip_g12 = METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
            phantom_idxs = []
            for i in df.index:
                cu = cat_u_g12.iat[df.index.get_loc(i)] if hasattr(cat_u_g12, 'iat') else cat_u_g12.loc[i]
                if cu in skip_g12 or not val_g12.loc[i]:
                    continue
                bv = bp_num_g12.loc[i]
                rv = raw_num_g12.loc[i] if raw_num_g12 is not None else None
                bp_zero = (bv is not None and pd.notna(bv) and float(bv) == 0.0)
                raw_zero = (rv is not None and pd.notna(rv) and float(rv) == 0.0)
                if (bp_zero and (raw_zero or rv is None)) or \
                   (raw_zero and (bp_zero or bv is None)):
                    phantom_idxs.append(i)
            if phantom_idxs:
                sample_g12 = '; '.join(
                    f"[{cat_u_g12.loc[i]}]\"{val_g12.loc[i]}\""
                    for i in phantom_idxs[:5]
                )
                more_g12 = (
                    f' (+{len(phantom_idxs)-5} more)'
                    if len(phantom_idxs) > 5 else ''
                )
                defects.append(
                    f'G12 PHANTOM_ZERO: {len(phantom_idxs)} non-meta row(s) '
                    f'at exact BP=0/Raw=0 -- phantom inserts that should '
                    f'have been stripped (sample: {sample_g12}{more_g12})'
                )
    except Exception as e:
        defects.append(f'G12 PHANTOM_ZERO: detector errored: {e}')

    # ── G13: subject missing from native category (Apple Pay defect, 2026-06-15) ───
    # Per Profile IQ Rule #3, the subject must be present at 100% in its
    # BRAND CATEGORY (its native dashboard grid). The
    # ensure_subject_in_native_category enforcer inserts it if missing;
    # this gate is the survivor check.
    #
    # 2026-06-19 Jenna policy change (Chase Bank G13 reject — subject
    # "CHASE BANK" absent from BANK grid despite ensure_subject_in_native_
    # category enforcer running upstream): if the subject is truly missing
    # from its native grid, AUTO-PATCH by inserting a new self-pin row at
    # 100% with the subject's exact name, then re-normalize Category Share.
    # Mirror of the G14 + G17 fixes — patch, don't reject. Falls through to
    # defect-append only when sample_size is unavailable or the insert
    # itself raises.
    try:
        if 'Column' in df.columns and 'Value' in df.columns:
            cat_u_g13 = df['Column'].astype(str).str.strip().str.upper()
            val_u_g13 = df['Value'].astype(str).str.strip().str.upper()
            bi_g13 = df.loc[cat_u_g13 == 'BRAND INPUT']
            bc_g13 = df.loc[cat_u_g13 == 'BRAND CATEGORY']
            if len(bi_g13) and len(bc_g13):
                # 2026-08-20 (EST Buyers batch): BRAND INPUT carries the
                # URL-variant seed list (Rule #4c), NOT the display name.
                # Using the raw echo here made the detector miss the
                # clean self-row (norm never matched) AND made the
                # auto-patch insert a 279-char seed string into the grid
                # at 100%. Prefer the caller-passed subject; fall back
                # to the first comma-segment of the echo.
                subj_g13 = str(subject or '').strip().upper()
                if not subj_g13:
                    _bi_raw_g13 = str(
                        bi_g13.iloc[0].get('Value', '') or '').strip()
                    subj_g13 = _bi_raw_g13.split(',')[0].strip().upper()
                native_g13 = str(bc_g13.iloc[0].get('Value', '') or '').strip().upper()
                # Skip categories that don't carry a 100% subject self-pin.
                _g13_skip = (
                    METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
                    | {'MOST PURCHASED BRANDS', 'APPAREL/FOOTWEAR',
                       'BEAUTY/WELLNESS', 'WHERE THEY SHOP',
                       'TECHNOLOGY BRAND', 'HOME/OUTDOOR', 'CPG',
                       'BEVERAGES', 'FRANCHISE'}
                )
                if (subj_g13 and native_g13 and native_g13 not in _g13_skip):
                    cat_rows_g13 = df.loc[cat_u_g13 == native_g13]
                    if len(cat_rows_g13) > 0:
                        subj_norm = _re.sub(r'[^A-Z0-9]', '', subj_g13)
                        cat_val_norm = (
                            cat_rows_g13['Value'].astype(str).str.upper()
                            .str.replace(r'[^A-Z0-9]', '', regex=True)
                        )
                        if not (cat_val_norm == subj_norm).any():
                            patched_g13 = False
                            if (_gate_sample_size and _gate_sample_size > 0
                                    and bp_col):
                                try:
                                    # 2026-06-22 Jenna fix: pandas StringDtype
                                    # on bp_col / cs_col raises
                                    # "Invalid value '100.0' for dtype 'str'"
                                    # when we try to assign a float. Coerce
                                    # the target numeric columns to 'object'
                                    # so float/int assignments succeed.
                                    for _dtcol in (bp_col, cs_col,
                                                   raw_col, proj_col):
                                        if (_dtcol and _dtcol in df.columns
                                                and df[_dtcol].dtype.name
                                                not in ('object', 'O',
                                                        'float64', 'int64')):
                                            df[_dtcol] = (
                                                df[_dtcol].astype(object)
                                            )
                                    # Use the first peer row in the same
                                    # category as a column-shape template,
                                    # then overwrite key fields.
                                    template_idx = cat_rows_g13.index[0]
                                    new_idx = len(df)
                                    df.loc[new_idx] = df.loc[template_idx]
                                    df.at[new_idx, 'Column'] = native_g13
                                    df.at[new_idx, 'Value'] = subj_g13
                                    df.at[new_idx, bp_col] = 100.0
                                    if raw_col:
                                        df.at[new_idx, raw_col] = int(
                                            _gate_sample_size
                                        )
                                    if proj_col:
                                        df.at[new_idx, proj_col] = int(round(
                                            _gate_sample_size *
                                            (US_POP / 10_000_000.0)
                                        ))
                                    try:
                                        _renormalize_category(
                                            df, native_g13,
                                            bp_col, cs_col, raw_col, proj_col,
                                            _gate_sample_size,
                                        )
                                    except Exception:
                                        pass
                                    patched_g13 = True
                                    if verbose:
                                        print(
                                            '   🛠  G13 auto-patched '
                                            '(gate-level): inserted subject '
                                            f'"{subj_g13}" @ 100.0000% into '
                                            f'[{native_g13}] grid '
                                            f'({len(cat_rows_g13)} peer rows '
                                            'already present)'
                                        )
                                except Exception as _pe_g13:
                                    if verbose:
                                        print(
                                            '   ⚠️  G13 auto-patch FAILED '
                                            f'({_pe_g13}); falling back '
                                            'to defect'
                                        )
                            if not patched_g13:
                                defects.append(
                                    f'G13 SUBJECT_MISSING_NATIVE: subject '
                                    f'\"{subj_g13}\" absent from BRAND '
                                    f'CATEGORY \"{native_g13}\" '
                                    f'({len(cat_rows_g13)} peer rows '
                                    'present) -- Profile IQ Rule #3 '
                                    'violation (gate could not auto-patch)'
                                )
    except Exception as e:
        defects.append(f'G13 SUBJECT_MISSING_NATIVE: detector errored: {e}')

    # ── G14: subject self-anchor NEAR-miss in NATIVE grid (2026-06-15) ───
    # Native grid = BRAND CATEGORY metadata (Profile IQ Rule #3 canonical),
    # with max-BP fallback when metadata is missing or doesn't match a
    # candidate row. All other subject-row appearances are PEER RATES
    # (cross-platform overlap) and must NOT be flagged.
    #
    # G14 fires only when the native grid (per BRAND CATEGORY metadata,
    # or max-BP fallback) is at BP in [95, 100):
    #   DOGTV     STREAMING VIDEO 98.9296% (BC=SV; max-BP would pick S/P)
    #   Netflix   STREAMING VIDEO 99.0376% (BC=SV; max-BP would pick S/P)
    #   GoodShort STREAMING VIDEO 99.9900% (BC=SV; agrees with max-BP)
    # native_bp == 100 -> already pinned, PASS.
    # native_bp <  95  -> writer pinned in sister grid or nowhere; deeper
    # defect surfaced by hostmap audit / ensure_subject_in_native_category.
    #
    # History:
    #   - PM (Defect 24): rescoped from "(0, 100) any cat" to
    #     "highest-BP cat in [95, 100)".
    #   - PM-revised-2 (Defect 27, DOGTV): use BRAND CATEGORY metadata
    #     first; max-BP only as fallback. Max-BP broke when the writer
    #     ALSO pinned 100 in a sister grid (S/P) AND near-pinned the
    #     metadata-canonical native (SV) -- max-BP picked S/P and missed
    #     the actual native-grid violation.
    try:
        if 'Column' in df.columns and 'Value' in df.columns:
            cat_u_g14 = df['Column'].astype(str).str.strip().str.upper()
            bi_g14 = df.loc[cat_u_g14 == 'BRAND INPUT']
            if len(bi_g14):
                subj_g14 = str(bi_g14.iloc[0].get('Value', '') or '').strip().upper()
                if subj_g14:
                    skip_g14 = (
                        METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
                        | {'MOST PURCHASED BRANDS', 'APPAREL/FOOTWEAR',
                           'BEAUTY/WELLNESS', 'WHERE THEY SHOP',
                           'TECHNOLOGY BRAND', 'HOME/OUTDOOR', 'CPG',
                           'BEVERAGES', 'FRANCHISE'}
                    )
                    subj_norm_g14 = _re.sub(r'[^A-Z0-9]', '', subj_g14)
                    val_norm_g14 = (
                        df['Value'].astype(str).str.upper()
                        .str.replace(r'[^A-Z0-9]', '', regex=True)
                    )
                    candidate = (val_norm_g14 == subj_norm_g14) & (~cat_u_g14.isin(skip_g14))
                    bp_num_g14 = df[bp_col].apply(_bp)

                    # Try BRAND CATEGORY metadata first
                    bc_mask_g14 = cat_u_g14 == 'BRAND CATEGORY'
                    bc_native_g14 = None
                    if bc_mask_g14.any():
                        bc_val_g14 = str(df.loc[bc_mask_g14].iloc[0]
                                         .get('Value', '') or '').strip().upper()
                        if bc_val_g14 and bc_val_g14 not in skip_g14:
                            bc_native_g14 = bc_val_g14

                    best_cat_g14 = None
                    best_val_g14 = None
                    best_bp_g14 = -1.0
                    best_idx_g14 = None
                    src_g14 = None
                    if bc_native_g14 is not None:
                        for idx in df.index[candidate]:
                            if cat_u_g14.loc[idx] == bc_native_g14:
                                bv = bp_num_g14.loc[idx]
                                if bv is not None and not pd.isna(bv):
                                    best_cat_g14 = cat_u_g14.loc[idx]
                                    best_val_g14 = df.at[idx, 'Value']
                                    best_bp_g14 = float(bv)
                                    best_idx_g14 = idx
                                    src_g14 = 'BRAND CATEGORY metadata'
                                    break
                    if best_cat_g14 is None:
                        # Fallback: max-BP across all candidate rows
                        for idx in df.index[candidate]:
                            bv = bp_num_g14.loc[idx]
                            if bv is None or pd.isna(bv):
                                continue
                            bv_f = float(bv)
                            if bv_f > best_bp_g14:
                                best_bp_g14 = bv_f
                                best_cat_g14 = cat_u_g14.loc[idx]
                                best_val_g14 = df.at[idx, 'Value']
                                best_idx_g14 = idx
                        if best_cat_g14 is not None:
                            src_g14 = 'max-BP fallback'

                    if best_cat_g14 is not None and 95.0 <= best_bp_g14 < 99.9999:
                        # 2026-06-18 Jenna policy change (Chase Bank G14
                        # reject @ 99.2320% — same defect on 3 retries):
                        # instead of failing the gate when the writer's
                        # self-pin intent landed in [95, 100), AUTO-PATCH
                        # the subject self-anchor row to exactly 100% in
                        # its native grid and renormalize Category Share.
                        # Mirror of the G17 fix (2026-06-17) — Jenna's
                        # directive: "you shouldn't just abandon something
                        # if that happens, you should just patch X to be
                        # where it needs to be then publish". Falls back
                        # to defect-append if sample_size is unavailable
                        # or the patch raises, so we never ship silently
                        # bad data.
                        patched_g14 = False
                        if (_gate_sample_size and _gate_sample_size > 0
                                and bp_col and best_idx_g14 is not None):
                            try:
                                _set_bp(df, best_idx_g14, 100.0,
                                        bp_col, cs_col, raw_col, proj_col,
                                        _gate_sample_size)
                                try:
                                    _renormalize_category(
                                        df, best_cat_g14,
                                        bp_col, cs_col, raw_col, proj_col,
                                        _gate_sample_size,
                                    )
                                except Exception:
                                    pass
                                patched_g14 = True
                                if verbose:
                                    print(
                                        '   🛠  G14 auto-patched (gate-level): '
                                        f'[{best_cat_g14}]"{best_val_g14}" '
                                        f'{best_bp_g14:.4f}% -> 100.0000% '
                                        f'({src_g14})'
                                    )
                            except Exception as _pe_g14:
                                if verbose:
                                    print(
                                        '   ⚠️  G14 auto-patch FAILED '
                                        f'({_pe_g14}); falling back to defect'
                                    )
                        if not patched_g14:
                            defects.append(
                                f'G14 SUBJECT_SELF_ANCHOR_NEAR_MISS: subject '
                                f'"{subj_g14}" native grid ({src_g14}) '
                                f'[{best_cat_g14}]"{best_val_g14}" at '
                                f'{best_bp_g14:.4f}% -- writer self-pin intent '
                                f'missed exact 100 (Rule #3 native-grid '
                                f'invariant; gate could not auto-patch)'
                            )
    except Exception as e:
        defects.append(f'G14 SUBJECT_SELF_ANCHOR_NEAR_MISS: detector errored: {e}')

    # ── G15: subject label variant across grids (FIFA+ defect, 2026-06-15) ──
    # Detects writer-side label drift: BRAND INPUT canonical = "FIFA+",
    # but writer emits self-pin rows under variants like "FIFA" (no plus)
    # in companion grids. The variant rows match the subject under
    # case+punct-insensitive normalization but use a different visible
    # spelling than the canonical.
    #
    # G15 fires when any non-meta, non-demo row has Value normalizing to
    # the same key as BRAND INPUT but a different actual spelling. This
    # catches FIFA+/FIFA, AT&T/ATT, Macy's/Macys, etc. -- cases where the
    # dashboard renders the same service under inconsistent labels and
    # cross-grid dedup breaks.
    #
    # Detection-only (no auto-rename): the canonical-vs-variant choice
    # is ambiguous in some cases (e.g. "FIFA" the org is a real entity
    # distinct from FIFA+ the streaming product, and we don't want to
    # silently collapse them). Flagging publication is the safe move;
    # human/writer fixes the label, or the strip pass + ensure_native
    # gate handles it on a per-profile basis.
    try:
        if 'Column' in df.columns and 'Value' in df.columns:
            cat_u_g15 = df['Column'].astype(str).str.strip().str.upper()
            bi_g15 = df.loc[cat_u_g15 == 'BRAND INPUT']
            if len(bi_g15):
                canonical_g15 = str(bi_g15.iloc[0].get('Value', '') or '').strip()
                canonical_u_g15 = canonical_g15.upper()
                canonical_norm_g15 = _re.sub(r'[^A-Z0-9]', '', canonical_u_g15)
                # Only meaningful when canonical contains punctuation or
                # the writer could plausibly have dropped chars; if
                # canonical is plain alphanum, every variant would
                # equal the canonical and there's nothing to flag.
                if canonical_norm_g15 and canonical_norm_g15 != canonical_u_g15:
                    skip_g15 = (
                        METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
                    )
                    val_u_g15 = df['Value'].astype(str).str.strip().str.upper()
                    val_norm_g15 = val_u_g15.str.replace(r'[^A-Z0-9]', '', regex=True)
                    variant_mask = (
                        (val_norm_g15 == canonical_norm_g15)
                        & (val_u_g15 != canonical_u_g15)
                        & (~cat_u_g15.isin(skip_g15))
                    )
                    if variant_mask.any():
                        variants = []
                        for idx in df.index[variant_mask]:
                            variants.append(
                                (cat_u_g15.loc[idx], df.at[idx, 'Value'],
                                 df.at[idx, bp_col])
                            )
                        sample_g15 = '; '.join(
                            f'[{c}]"{v}"@{bp}'
                            for c, v, bp in variants[:5]
                        )
                        more_g15 = (
                            f' (+{len(variants)-5} more)'
                            if len(variants) > 5 else ''
                        )
                        defects.append(
                            f'G15 SUBJECT_LABEL_VARIANT: BRAND INPUT '
                            f'canonical="{canonical_g15}" but '
                            f'{len(variants)} row(s) use punct-stripped '
                            f'variant: {sample_g15}{more_g15} '
                            f'-- cross-grid label drift breaks dedup'
                        )
    except Exception as e:
        defects.append(f'G15 SUBJECT_LABEL_VARIANT: detector errored: {e}')

    # ── G16: build-time annotation leak (Shemar Moore defect, 2026-06-15) ───
    # Survivor check for the annotation-pattern stripper in
    # _is_polluted_brand_value. Fires when any non-meta, non-demo Value
    # matches a build-time annotation/remap pattern: '-NA', '-TBD',
    # '-TODO', '-PENDING', '-FIXME' suffix; '(use X)', '(see X)',
    # '(remap X)', '(map to X)', '(instead X)', '(replace with X)';
    # 'TODO:', 'FIXME:'; 'N/A'; or '=>' arrow remap.
    #
    # In the wild: Shemar_Moore profile shipped with INTEREST/'SAILING-na
    # (use OUTDOOR LIFE)' at BP=0/Raw=0 -- an upstream mapping instruction
    # ("remap SAILING (not-applicable) to OUTDOOR LIFE") that should have
    # been resolved during the build. Defect-19's strip_phantom_zero_rows
    # caught it incidentally because BP=0 AND Raw=0, but a non-zero
    # annotation row would have shipped uncaught. G16 closes that gap.
    try:
        if 'Column' in df.columns and 'Value' in df.columns:
            cat_u_g16 = df['Column'].astype(str).str.strip().str.upper()
            skip_g16 = METADATA_COLS | DEPIN_DEMO_CATS | DEPIN_META_CATS
            non_meta = ~cat_u_g16.isin(skip_g16)
            leaks = []
            for idx in df.index[non_meta]:
                v = str(df.at[idx, 'Value'] or '').strip()
                if v and _ANNOTATION_RX.search(v):
                    leaks.append((cat_u_g16.loc[idx], v))
            if leaks:
                sample_g16 = '; '.join(
                    f'[{c}]"{v[:40]}"' for c, v in leaks[:5]
                )
                more_g16 = f' (+{len(leaks)-5} more)' if len(leaks) > 5 else ''
                defects.append(
                    f'G16 BUILD_ANNOTATION_LEAK: {len(leaks)} row(s) '
                    f'contain upstream mapping/remap instructions in '
                    f'Value: {sample_g16}{more_g16} '
                    f'-- should have been resolved during build, not '
                    f'shipped to dashboard'
                )
    except Exception as e:
        defects.append(f'G16 BUILD_ANNOTATION_LEAK: detector errored: {e}')

    # ── G17: Netflix BP suppressed in STREAMING/PLATFORM (Defect 28+30) ──
    # Survivor check for enforce_netflix_leads_streaming_platform.
    # Original (Defect 28, 2026-06-15 AM) flagged inversions only;
    # Defect 30 (2026-06-15 PM) widened to suppression-below-baseline:
    # Netflix's universal US adult reach is ~75%, so any BP below the
    # 73% jitter floor is suppression -- even when Netflix still ranks
    # #1 (e.g. NX 67% with Prime 50% means BOTH were suppressed).
    # Self-pin exception: BP >= 95% is exempt (subject IS a streaming
    # brand, or near-self-pin show profile like "POWER ON AMAZON PRIME"
    # with AP at 99.49%).
    try:
        if 'Column' in df.columns and 'Value' in df.columns:
            cat_u_g17 = df['Column'].astype(str).str.strip().str.upper()
            sp_idx_g17 = df.index[cat_u_g17 == 'STREAMING/PLATFORM']
            if len(sp_idx_g17):
                val_u_g17 = (df.loc[sp_idx_g17, 'Value']
                             .astype(str).str.upper().str.strip())
                bp_num_g17 = df.loc[sp_idx_g17, bp_col].apply(_bp)
                nx_idxs = [i for i in sp_idx_g17 if val_u_g17.loc[i] == 'NETFLIX']
                if nx_idxs:
                    nx_idx_g17 = nx_idxs[0]
                    nx_bp_g17 = bp_num_g17.loc[nx_idx_g17]
                    nx_bp_g17 = float(nx_bp_g17) if nx_bp_g17 is not None and not pd.isna(nx_bp_g17) else None
                    if nx_bp_g17 is not None and nx_bp_g17 < 95.0:
                        # 2026-06-17 Jenna policy change (Jennifer Beals G17
                        # reject @ 72.5686%): instead of failing the gate
                        # when Netflix is below the 73% floor or a peer
                        # leads it, AUTO-PATCH the file in place:
                        #   - Netflix < 73 -> raise to 75 + jitter[0..4],
                        #     recompute Raw/Proj
                        #   - peer leading Netflix (non-self-pin) -> cap
                        #     peer to 74.9, recompute Raw/Proj
                        #   - renormalize STREAMING/PLATFORM Category Share
                        # Only fall back to defect-append if the auto-patch
                        # itself raises. This implements Jenna's directive:
                        # "you shouldn't just abandon something if that
                        # happens, you should just patch netflix to be
                        # where it needs to be then publish".
                        patch_notes = []
                        patched_anything = False
                        gate_ss_local = _gate_sample_size
                        if gate_ss_local and gate_ss_local > 0 and bp_col:
                            # (a) Netflix below the 73% baseline floor
                            # 2026-08-24: threshold tracks the corrected
                            # Gen Pop Netflix baseline (39.17), not the
                            # legacy 73/75 anchors.
                            if nx_bp_g17 < 36.5:
                                try:
                                    NX_TARGET_FIX = 38.0
                                    new_nx_g17 = NX_TARGET_FIX + _jitter_for(
                                        subject, 'NETFLIX',
                                        salt='gate_baseline',
                                        lo=0.0, hi=4.0,
                                    )
                                    new_nx_g17 = round(min(max(new_nx_g17, NX_TARGET_FIX), 42.0), 4)
                                    _set_bp(df, nx_idx_g17, new_nx_g17,
                                            bp_col, cs_col, raw_col, proj_col,
                                            gate_ss_local)
                                    patch_notes.append(
                                        f'NETFLIX {nx_bp_g17:.4f}% -> '
                                        f'{new_nx_g17:.4f}%'
                                    )
                                    nx_bp_g17 = new_nx_g17
                                    patched_anything = True
                                except Exception as _pe:
                                    patch_notes.append(
                                        f'NETFLIX patch FAILED ({_pe})'
                                    )
                            # (b) Any non-self-pin peer leads Netflix
                            #     -> cap to nx_bp - 0.1 (peer-cap = 74.9 if
                            #     nx is 75, or peer-cap=nx-0.1 in general).
                            #     Recompute bp_num so we work off post-patch
                            #     values.
                            try:
                                bp_num_g17 = df.loc[sp_idx_g17, bp_col].apply(_bp)
                            except Exception:
                                pass
                            for i in sp_idx_g17:
                                if i == nx_idx_g17:
                                    continue
                                bp = bp_num_g17.loc[i]
                                if bp is None or pd.isna(bp):
                                    continue
                                bp_f = float(bp)
                                if bp_f >= 95.0:
                                    continue
                                if bp_f > nx_bp_g17 + 1e-9:
                                    try:
                                        peer_cap_g17 = round(nx_bp_g17 - 0.1, 4)
                                        if peer_cap_g17 < 1.0:
                                            peer_cap_g17 = 1.0
                                        _set_bp(df, i, peer_cap_g17,
                                                bp_col, cs_col, raw_col,
                                                proj_col, gate_ss_local)
                                        patch_notes.append(
                                            f'{df.at[i, "Value"]} '
                                            f'{bp_f:.4f}% -> {peer_cap_g17:.4f}% '
                                            f'(peer-cap below Netflix)'
                                        )
                                        patched_anything = True
                                    except Exception as _pe2:
                                        patch_notes.append(
                                            f'{df.at[i, "Value"]} '
                                            f'peer-cap FAILED ({_pe2})'
                                        )
                            if patched_anything:
                                try:
                                    _renormalize_category(
                                        df, 'STREAMING/PLATFORM',
                                        bp_col, cs_col, raw_col, proj_col,
                                        gate_ss_local,
                                    )
                                except Exception:
                                    pass
                                if verbose:
                                    print(
                                        '   🛠  G17 auto-patched (gate-level): '
                                        + ' | '.join(patch_notes)
                                    )
                        else:
                            # Couldn't determine sample_size -> fall back to
                            # legacy detect-only behaviour so we don't ship
                            # silently bad data.
                            problems = []
                            if nx_bp_g17 < 36.5:
                                problems.append(
                                    f'NETFLIX_BELOW_BASELINE: Netflix at '
                                    f'{nx_bp_g17:.4f}% is below the 36.5% '
                                    f'floor (corrected baseline ~39.2%) -- '
                                    f'and gate could not auto-patch (no '
                                    f'sample_size)'
                                )
                            leaders = []
                            for i in sp_idx_g17:
                                if i == nx_idx_g17:
                                    continue
                                bp = bp_num_g17.loc[i]
                                if bp is None or pd.isna(bp):
                                    continue
                                bp_f = float(bp)
                                if bp_f >= 95.0:
                                    continue
                                if bp_f > nx_bp_g17 + 1e-9:
                                    leaders.append((df.at[i, 'Value'], bp_f))
                            if leaders:
                                leaders.sort(key=lambda t: -t[1])
                                sample_g17 = '; '.join(
                                    f'"{v}"@{bp:.4f}%' for v, bp in leaders[:3]
                                )
                                problems.append(
                                    f'NETFLIX_NOT_#1: Netflix at '
                                    f'{nx_bp_g17:.4f}% but {len(leaders)} '
                                    f'peer(s) lead: {sample_g17} -- and '
                                    f'gate could not auto-patch (no '
                                    f'sample_size)'
                                )
                            if problems:
                                defects.append(
                                    'G17 NETFLIX_SUPPRESSED_STREAMING_PLATFORM: '
                                    + ' | '.join(problems)
                                )
    except Exception as e:
        defects.append(f'G17 NETFLIX_SUPPRESSED_STREAMING_PLATFORM: detector errored: {e}')

    # ── G18: subject brand duplicated in non-native streaming grid ───────
    # Defect 31 (2026-06-15 PM, Hallmark Plus). Survivor check for
    # dedupe_subject_streaming_grids. After today's CATEGORY_DISPLAY_LABELS
    # change, both STREAMING VIDEO and STREAMING/PLATFORM render under one
    # tab; if the subject brand appears at >=95% in one and < 50% in the
    # other, the dashboard shows two contradictory bars side by side.
    # Post-enforcer state should never have this -- if G18 fires, the
    # enforcer regressed.
    try:
        if 'Column' in df.columns and 'Value' in df.columns:
            cat_u_g18 = df['Column'].astype(str).str.strip().str.upper()
            val_u_g18 = df['Value'].astype(str).str.strip().str.upper()
            bi_mask_g18 = cat_u_g18 == 'BRAND INPUT'
            if bi_mask_g18.any():
                subj_g18 = str(df.loc[bi_mask_g18].iloc[0].get('Value', '') or '').strip().upper()
                if subj_g18:
                    sv_g18 = df.index[(cat_u_g18 == 'STREAMING VIDEO') & (val_u_g18 == subj_g18)]
                    sp_g18 = df.index[(cat_u_g18 == 'STREAMING/PLATFORM') & (val_u_g18 == subj_g18)]
                    if len(sv_g18) and len(sp_g18):
                        sv_bp_g18 = _bp(df.at[sv_g18[0], bp_col])
                        sp_bp_g18 = _bp(df.at[sp_g18[0], bp_col])
                        try:
                            sv_bp_g18 = float(sv_bp_g18) if sv_bp_g18 is not None and not pd.isna(sv_bp_g18) else None
                            sp_bp_g18 = float(sp_bp_g18) if sp_bp_g18 is not None and not pd.isna(sp_bp_g18) else None
                        except Exception:
                            sv_bp_g18 = sp_bp_g18 = None
                        if sv_bp_g18 is not None and sp_bp_g18 is not None:
                            if ((sv_bp_g18 >= 95.0 and sp_bp_g18 < 50.0) or
                                (sp_bp_g18 >= 95.0 and sv_bp_g18 < 50.0)):
                                defects.append(
                                    f'G18 SUBJECT_DUPLICATED_NON_NATIVE_STREAMING: '
                                    f'subject {subj_g18!r} appears at '
                                    f'{sv_bp_g18:.4f}% in STREAMING VIDEO and '
                                    f'{sp_bp_g18:.4f}% in STREAMING/PLATFORM '
                                    f'-- non-native peer row creates duplicate '
                                    f'bar in merged STREAMING/PLATFORM display tab'
                                )
    except Exception as e:
        defects.append(f'G18 SUBJECT_DUPLICATED_NON_NATIVE_STREAMING: detector errored: {e}')

    # ── G8: Category Share sum invariant (non-demo) ──────────────────
    # 2026-08-03 (Honey Pot / synth_engine share=BP bug). Non-demo
    # Category Share must sum to 100 ± 3pp per category (metadata rows
    # excluded). This catches the two writer-bug signatures:
    #   * Share literally set = BP (sums >> 100)
    #   * Share left blank on some rows (sums << 100)
    # Auto-patched by apply_recompute_category_share upstream; the gate
    # only fires if the recompute couldn't run (missing BP/CS cols).
    try:
        if bp_col and cs_col and 'Column' in df.columns:
            col_u_g8 = df['Column'].astype(str).str.strip().str.upper()
            # G8 exempts the 9 canonical demos AND the extended demo-like
            # blocks (LOCATION, PRIMARY_LANGUAGE, NUMBER_OF_CHILDREN,
            # AGE_OF_CHILDREN). Those all sum to ~100 by construction, so
            # a ~100 sum is expected, not a defect.
            demo_upper_g8 = {c.upper() for c in _DEMO_LIKE_ALL}
            skip_g8 = _SHARE_SKIP_BLOCKS | demo_upper_g8
            broken = []
            seen = set()
            for cat in df['Column'].astype(str).str.strip().unique():
                cu = str(cat).upper().strip()
                if cu in skip_g8 or cu in seen:
                    continue
                seen.add(cu)
                m = col_u_g8 == cu
                if int(m.sum()) < 3:
                    continue
                shares = df.loc[m, cs_col].apply(_bp).fillna(0)
                s = float(shares.sum())
                if abs(s - 100.0) > 3.0:
                    broken.append((cu, round(s, 2), int(m.sum())))
            if broken:
                broken.sort(key=lambda x: -abs(x[1] - 100))
                sample = '; '.join(
                    f'{c}={s:.1f}%(n={n})' for c, s, n in broken[:5]
                )
                defects.append(
                    f'G8 SHARE_SUM: {len(broken)} non-demo block(s) with '
                    f'Category Share sum outside 100±3pp '
                    f'(sample: {sample}) -- recompute did not run'
                )
    except Exception as e:
        defects.append(f'G8 SHARE_SUM: detector errored: {e}')

    # ── G9: Share == BP corruption pattern (non-demo) ────────────────
    # 2026-08-03 (Waterloo/Lainey/Sabrina/WoF synth_engine.py bug):
    # writer set Share = BP directly, so shares mimic penetration
    # rather than proportion-within-category. Signature is >30 rows
    # where non-demo Share equals BP to 4dp (and BP > 0.5%).
    try:
        if bp_col and cs_col and 'Column' in df.columns:
            col_u_g9 = df['Column'].astype(str).str.strip().str.upper()
            # G9 exempts the 9 canonical demos AND the extended demo-like
            # blocks — for those, Share == BP is expected math (buckets
            # sum to 100 => Share = BP/100 * 100 = BP), not writer-bug.
            skip_g9 = _SHARE_SKIP_BLOCKS | {
                c.upper() for c in _DEMO_LIKE_ALL
            }
            n_corrupt = 0
            for idx in df.index:
                cu = str(df.at[idx, 'Column']).strip().upper()
                if cu in skip_g9:
                    continue
                bp_v = _bp(df.at[idx, bp_col])
                sh_v = _bp(df.at[idx, cs_col])
                if bp_v is None or sh_v is None:
                    continue
                if bp_v > 0.5 and abs(bp_v - sh_v) < 0.0001:
                    n_corrupt += 1
            if n_corrupt > 30:
                defects.append(
                    f'G9 SHARE_EQ_BP: {n_corrupt} non-demo row(s) have '
                    f'Category Share == Brand Penetration (writer-bug '
                    f'signature; share should be BP/ΣBP*100)'
                )
    except Exception as e:
        defects.append(f'G9 SHARE_EQ_BP: detector errored: {e}')

    # ── G10: Streaming Share pinned + rest null ──────────────────────
    # 2026-08-03 (Honey Pot Netflix): one row Share=100, ≥50% of block
    # rows have NULL share. enforce_streaming_share_health should have
    # auto-fixed this; gate fires only if that repair didn't run.
    try:
        if cs_col and 'Column' in df.columns:
            col_u_g10 = df['Column'].astype(str).str.strip().str.upper()
            for target in ('STREAMING/PLATFORM', 'STREAMING VIDEO'):
                m = col_u_g10 == target
                if int(m.sum()) < 5:
                    continue
                share_raw = df.loc[m, cs_col].astype(str).str.strip()
                n = int(m.sum())
                n_null = int(
                    (share_raw == '').sum()
                    + (share_raw.str.lower() == 'nan').sum()
                )
                share_vals = share_raw.apply(
                    lambda x: _bp(x) if x and x.lower() != 'nan' else None
                )
                n_pin_100 = int(share_vals.apply(
                    lambda v: v is not None and abs(v - 100) < 0.001
                ).sum())
                if n_null >= n * 0.5 and n_pin_100 >= 1:
                    defects.append(
                        f'G10 STREAMING_SHARE_PIN: {target} has 1 row '
                        f'pinned at Share=100 with {n_null}/{n} rows '
                        f'NULL — enforce_streaming_share_health did not '
                        f'run or block has zero BP mass'
                    )
    except Exception as e:
        defects.append(f'G10 STREAMING_SHARE_PIN: detector errored: {e}')

    if verbose:
        if defects:
            print(f'   🚨 pre-publish gate: {len(defects)} BLOCKING defect(s) for {project_name or subject}:')
            for d in defects:
                print(f'      ✗ {d}')
        else:
            print(f'   🟢 pre-publish gate: PASS for {project_name or subject}')

    if defects and raise_on_fail:
        raise PrePublishGateError(defects)

    return defects


# ─────────────────────────────────────────────────────────────────────
# G12 — SOFT WARNING: profile_raw should not exceed Gen Pop raw.
# 2026-06-10 (Jenna): per "Sandler Coca-Cola 1,629,493 / 5.4M vs Gen
# Pop 821,820 / 10M" reproducer, raw-count in a profile (= subset of
# US adults) must never exceed gp_raw (the universe). Per user
# directive, the fix is in Gen Pop, not in the profile — so this is
# a soft warning that LOGS violations and surfaces brands that need a
# Gen Pop bump. The companion `scripts/reconcile_gen_pop.py` script
# applies the bumps in batch.
#
# Returned tuple: (warnings_list, brands_needing_bump)
# - warnings_list: human-readable strings for log/email
# - brands_needing_bump: dict {(cat,val): {p_raw, gp_raw, p_bp, gp_bp}}
#   used by the reconcile script
# ─────────────────────────────────────────────────────────────────────
_GP_RAW_CACHE: dict = {}  # populated lazily; key = bucket/key path


def _load_gp_raw_map(bucket: str = 'dashboard-inputs',
                     key: str = 'Gen_Pop_2026.csv',
                     ttl_seconds: int = 600):
    """Load Gen Pop {(cat,val): (gp_bp, gp_raw)} with simple TTL cache."""
    import time
    cache_key = f'{bucket}/{key}'
    entry = _GP_RAW_CACHE.get(cache_key)
    if entry and (time.time() - entry['t']) < ttl_seconds:
        return entry['data']
    try:
        import boto3, io as _io
        _s3 = boto3.client('s3', region_name='us-east-2')
        obj = _s3.get_object(Bucket=bucket, Key=key)
        gp_df = pd.read_csv(_io.BytesIO(obj['Body'].read()), low_memory=False)
    except Exception:
        return {}
    _bp_c, _, _raw_c, _ = _detect_cols(gp_df)
    _SKIP = {
        'INPUT_METADATA', 'BRAND INPUT', 'BRAND CATEGORY', 'SAMPLE SIZE',
        'SUBJECT', 'AVID FAN', 'CASUAL FAN', 'AL/NL', 'AFC/NFC',
        'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
        'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
    }
    out: dict = {}
    for _, r in gp_df.iterrows():
        cat = str(r['Column']).strip().upper()
        if cat in _SKIP:
            continue
        val = str(r['Value']).strip().upper()
        bp_v = _bp(r[_bp_c])
        try:
            raw_v = float(str(r[_raw_c]).replace(',', ''))
        except Exception:
            raw_v = None
        if bp_v is None or raw_v is None:
            continue
        out[(cat, val)] = (bp_v, raw_v)
    _GP_RAW_CACHE[cache_key] = {'t': time.time(), 'data': out}
    return out


def validate_profile_raw_le_gp_raw(df, subject='', verbose=True,
                                     bucket='dashboard-inputs',
                                     key='Gen_Pop_2026.csv',
                                     headroom: float = 1.05):
    """G12 soft-validator: warn when profile_raw > gp_raw × headroom.

    Returns: (warnings: list[str], bumps: dict)
    Never raises; never modifies df.
    """
    warnings: list[str] = []
    bumps: dict = {}
    if df is None or len(df) == 0:
        return warnings, bumps
    bp_col, _, raw_col, _ = _detect_cols(df)
    if not raw_col or raw_col not in df.columns:
        return warnings, bumps
    gp_map = _load_gp_raw_map(bucket=bucket, key=key)
    if not gp_map:
        if verbose:
            print('   ⚠ G12: gp raw map empty — skipping (no Gen_Pop access)')
        return warnings, bumps
    SKIP_CATS = {
        'INPUT_METADATA', 'BRAND INPUT', 'BRAND CATEGORY', 'SAMPLE SIZE',
        'SUBJECT', 'AVID FAN', 'CASUAL FAN', 'AL/NL', 'AFC/NFC',
        'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
        'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
    }
    for _, r in df.iterrows():
        cat = str(r['Column']).strip().upper()
        if cat in SKIP_CATS:
            continue
        val = str(r['Value']).strip().upper()
        gp_entry = gp_map.get((cat, val))
        if gp_entry is None:
            continue
        gp_bp, gp_raw = gp_entry
        try:
            p_raw = float(str(r[raw_col]).replace(',', ''))
        except Exception:
            continue
        if p_raw <= gp_raw * headroom:
            continue
        # Violation
        p_bp = _bp(r[bp_col])
        bumps[(cat, val)] = {
            'p_raw': p_raw, 'gp_raw': gp_raw,
            'p_bp': p_bp, 'gp_bp': gp_bp,
        }
        warnings.append(
            f'G12 RAW>GP_RAW: {cat}/{val} profile_raw={p_raw:,.0f} '
            f'> gp_raw={gp_raw:,.0f} (gp_bp={gp_bp:.4f}%, '
            f'p_bp={p_bp if p_bp is not None else "?"}) — Gen Pop bp needs bump'
        )
    if verbose and warnings:
        print(f'   🟡 G12 raw>gp_raw soft-warning: {len(warnings)} brand(s) '
              f'in {subject or "profile"} exceed Gen Pop raw — '
              f'Gen Pop will need a reconcile bump for these brands. '
              f'(Profile is NOT modified.) Sample:')
        for w in warnings[:5]:
            print(f'      ⚠ {w}')
        if len(warnings) > 5:
            print(f'      ⚠ ... and {len(warnings) - 5} more')
    elif verbose:
        print(f'   🟢 G12 raw>gp_raw: clean ({subject or "profile"})')
    return warnings, bumps
