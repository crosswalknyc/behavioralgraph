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
from collections import defaultdict

import pandas as pd

US_POP = 329_900_000


# ============================================================================
# Hostmap gating (workspace rule #4 — never lift/add a brand that isn't
# in `reference.host_mapping`). Loaded lazily from disk cache or ClickHouse.
# ============================================================================

_HOSTMAP_CACHE_PATH = '/tmp/hostmap_brands.txt'
_HOSTMAP_NORMALIZED = None     # set on first _ensure_hostmap_loaded() call
_HOSTMAP_RAW_UPPER = None
_HOSTMAP_NORM_TO_CANONICAL = None  # normalized → canonical Sheet4 casing
_HOSTMAP_GAPS = []             # populated by lift attempts on non-hostmap brands


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
    global _HOSTMAP_NORMALIZED, _HOSTMAP_RAW_UPPER, _HOSTMAP_NORM_TO_CANONICAL
    if _HOSTMAP_NORMALIZED is not None:
        return True
    candidates = [
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands_canonical.txt',
        '/root/finished_codes/reference/hostmap_brands_canonical.txt',
        _HOSTMAP_CACHE_PATH,
        '/Users/jennamenking/Desktop/finished_codes/reference/hostmap_brands.txt',
        '/root/finished_codes/reference/hostmap_brands.txt',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    lines = [line.strip() for line in f if line.strip()]
                _HOSTMAP_RAW_UPPER = {b.upper() for b in lines}
                _HOSTMAP_NORMALIZED = {_norm_brand(b) for b in lines}
                # First-wins map: normalized → canonical Sheet4 form (as
                # stored in reference.host_mapping). If hostmap has both
                # title-case and uppercase variants of the same normalized
                # brand, prefer title-case (more readable canonical).
                _HOSTMAP_NORM_TO_CANONICAL = {}
                for b in lines:
                    nk = _norm_brand(b)
                    cur = _HOSTMAP_NORM_TO_CANONICAL.get(nk)
                    # Prefer the variant that isn't all-caps (more canonical)
                    if cur is None or (cur.isupper() and not b.isupper()):
                        _HOSTMAP_NORM_TO_CANONICAL[nk] = b
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
    """Return the Sheet4 canonical casing for a brand (or None if not
    in hostmap). E.g. _hostmap_canonical('COCA COLA') -> 'Coca-Cola'."""
    if not _ensure_hostmap_loaded():
        return None
    if _HOSTMAP_NORM_TO_CANONICAL is None:
        return None
    return _HOSTMAP_NORM_TO_CANONICAL.get(_norm_brand(brand))


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


def _set_bp(df, idx, new_bp, bp_col, cs_col, raw_col, proj_col, sample_size):
    """Set new BP on a row and recompute raw + projection. CategoryShare
    recomputed downstream via _renormalize_category."""
    df.at[idx, bp_col] = round(float(new_bp), 4)
    if raw_col:
        df.at[idx, raw_col] = int(round(sample_size * new_bp / 100.0))
    if proj_col:
        df.at[idx, proj_col] = int(round(US_POP * new_bp / 100.0))
    return df


def _renormalize_category(df, cat, bp_col, cs_col, raw_col, proj_col, sample_size):
    """After modifying BPs in a category, recompute Category Share so the
    category sums to 100% (or as close as the non-modified rows allow).
    Uses _bp() to parse so it handles both bare floats and "22.6197%"
    strings (mixed dtypes appear when only some rows have been rewritten).
    """
    if cs_col is None:
        return df
    mask = df['Column'].astype(str).str.strip().str.upper() == cat
    bp_floats = df.loc[mask, bp_col].apply(_bp)
    bp_total = bp_floats.sum()
    if bp_total > 0:
        df.loc[mask, cs_col] = (bp_floats / bp_total * 100).round(4)
    if raw_col:
        df.loc[mask, raw_col] = (
            bp_floats / 100.0 * sample_size
        ).round(0).astype(int)
    if proj_col:
        df.loc[mask, proj_col] = (
            bp_floats / 100.0 * US_POP
        ).round(0).astype(int)
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


def _is_polluted_brand_value(v):
    if not v:
        return False
    v = v.strip()
    if v.startswith(('Bing |', 'Google |', 'Yahoo |', 'DuckDuckGo |')):
        return True
    if v.startswith('.') or v.startswith('http'):
        return True
    return False


def strip_polluted_brand_values(df, subject, verbose=True):
    """Drop brand rows whose Value is a search-result string or URL fragment
    (e.g. 'Bing | Breaking: Taylor Swift trending today', 'Google | Maps').
    Skips metadata columns (INPUT_METADATA / BRAND INPUT / SAMPLE SIZE)
    which legitimately contain long structured strings."""
    return _strip_rows(
        df, subject,
        lambda c, v: _is_polluted_brand_value(v),
        label='polluted-brand', verbose=verbose,
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
    ('INSURANCE', 'STATE FARM'):  {'older': 14.0, 'mid': 15.0, 'young': 16.0},
    ('INSURANCE', 'ALLSTATE'):    {'older': 11.0, 'mid': 11.0, 'young': 11.0},

    # AUTOMOBILE — mass-auto research panel (CarFax / dealer apps)
    ('AUTOMOBILE', 'TOYOTA'):    {'older': 14.0, 'mid': 20.0, 'young': 26.0},
    ('AUTOMOBILE', 'HONDA'):     {'older': 13.0, 'mid': 18.0, 'young': 22.0},
    ('AUTOMOBILE', 'FORD'):      {'older': 14.0, 'mid': 16.0, 'young': 17.0},
    ('AUTOMOBILE', 'CHEVROLET'): {'older': 12.0, 'mid': 14.0, 'young': 15.0},

    # MOST PURCHASED BRANDS / APPAREL/FOOTWEAR — Nike mass footwear (companion sync)
    ('MOST PURCHASED BRANDS', 'NIKE'): {'older': 18.0, 'mid': 32.0, 'young': 48.0},
    ('APPAREL/FOOTWEAR',      'NIKE'): {'older': 18.0, 'mid': 32.0, 'young': 48.0},

    # MOVIE THEATER — mass-theatrical-attendance panel
    ('MOVIE THEATER', 'AMC THEATRES'):       {'older': 24.0, 'mid': 30.0, 'young': 36.0},
    ('MOVIE THEATER', 'CINEMARK THEATRES'):  {'older': 16.0, 'mid': 22.0, 'young': 26.0},
    ('MOVIE THEATER', 'REGAL CINEMAS'):      {'older': 14.0, 'mid': 18.0, 'young': 22.0},

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
    ('SOCIAL MEDIA', 'LINKEDIN'):  {'18-24': 30, '25-34': 44, '35-44': 38, '45-54': 32, '55-64': 24, '65+': 13},
    ('SOCIAL MEDIA', 'PINTEREST'): {'18-24': 38, '25-34': 36, '35-44': 35, '45-54': 28, '55-64': 25, '65+': 21},
    ('SOCIAL MEDIA', 'X'):         {'18-24': 32, '25-34': 27, '35-44': 21, '45-54': 16, '55-64': 12, '65+':  6},

    # SEARCH / AI — Pew + LLM-adoption survey 2024
    ('SEARCH ENGINE/AI', 'CHAT GPT'):   {'18-24': 55, '25-34': 48, '35-44': 38, '45-54': 26, '55-64': 16, '65+':  9},
    ('SEARCH ENGINE/AI', 'GEMINI'):     {'18-24': 22, '25-34': 20, '35-44': 16, '45-54': 12, '55-64':  9, '65+':  5},
    ('SEARCH ENGINE/AI', 'PERPLEXITY'): {'18-24': 12, '25-34': 11, '35-44':  8, '45-54':  5, '55-64':  3, '65+':  2},
    ('SEARCH ENGINE/AI', 'CLAUDE AI'):  {'18-24': 14, '25-34': 12, '35-44':  9, '45-54':  6, '55-64':  3, '65+':  1},

    # WHERE THEY SHOP — pharmacy/mass retail visit-in-past-6mo, NOT universal
    ('WHERE THEY SHOP', 'CVS'):       {'18-24': 28, '25-34': 34, '35-44': 40, '45-54': 44, '55-64': 48, '65+': 52},
    ('WHERE THEY SHOP', 'WALGREENS'): {'18-24': 24, '25-34': 30, '35-44': 36, '45-54': 40, '55-64': 44, '65+': 48},
    ('WHERE THEY SHOP', 'TEMU'):      {'18-24': 42, '25-34': 38, '35-44': 28, '45-54': 18, '55-64': 10, '65+':  5},
    ('WHERE THEY SHOP', 'COSTCO'):    {'18-24': 24, '25-34': 32, '35-44': 40, '45-54': 42, '55-64': 40, '65+': 32},

    # TELECOM Big 3 — carrier share-of-audience (subscriber overlap)
    # T-MOBILE updated 2026-05-25 per colleague flag: was systematically under-read by 14-22pp
    # across Foosball/Keke/Dove/Nate pulls. T-Mo passed AT&T in US subs in 2023 (~33% share)
    # and has aggressive Magenta55+ program lifting older buckets too. Old benchmarks were
    # 28/28/26/22/18/12; new ones reflect ~33% national share at all working-age buckets.
    ('TELECOM', 'VERIZON'):  {'18-24': 28, '25-34': 30, '35-44': 32, '45-54': 33, '55-64': 32, '65+': 28},
    ('TELECOM', 'AT&T'):     {'18-24': 22, '25-34': 24, '35-44': 26, '45-54': 27, '55-64': 26, '65+': 22},
    ('TELECOM', 'T-MOBILE'): {'18-24': 34, '25-34': 34, '35-44': 32, '45-54': 30, '55-64': 26, '65+': 18},

    # BANKING Big 5 — primary-bank household share
    ('BANKING', 'CHASE'):           {'18-24': 28, '25-34': 35, '35-44': 36, '45-54': 34, '55-64': 30, '65+': 26},
    ('BANKING', 'BANK OF AMERICA'): {'18-24': 24, '25-34': 30, '35-44': 30, '45-54': 28, '55-64': 26, '65+': 22},
    ('BANKING', 'WELLS FARGO'):     {'18-24': 22, '25-34': 26, '35-44': 28, '45-54': 28, '55-64': 26, '65+': 22},
    ('BANKING', 'CITIBANK'):        {'18-24':  8, '25-34': 12, '35-44': 12, '45-54': 11, '55-64': 10, '65+':  8},
    ('BANKING', 'US BANK'):         {'18-24':  6, '25-34': 10, '35-44': 11, '45-54': 11, '55-64': 11, '65+': 10},

    # DIGITAL BANKING — younger-skewing P2P
    ('DIGITAL BANKING', 'PAYPAL'):   {'18-24': 56, '25-34': 58, '35-44': 52, '45-54': 46, '55-64': 38, '65+': 28},
    ('DIGITAL BANKING', 'VENMO'):    {'18-24': 64, '25-34': 56, '35-44': 38, '45-54': 22, '55-64': 12, '65+':  5},
    ('DIGITAL BANKING', 'CASH APP'): {'18-24': 52, '25-34': 44, '35-44': 28, '45-54': 18, '55-64': 11, '65+':  5},
    ('DIGITAL BANKING', 'ZELLE'):    {'18-24': 22, '25-34': 36, '35-44': 42, '45-54': 38, '55-64': 32, '65+': 22},
    ('DIGITAL BANKING', 'APPLE PAY'):{'18-24': 48, '25-34': 46, '35-44': 38, '45-54': 28, '55-64': 18, '65+':  9},
    # Ally / Chime are niche — NOT in benchmarks; legacy floor logic stays away

    # CREDIT PROVIDER — Visa/MC/Discover/Amex universal mass anchors
    # (added 2026-05-25 per Dove + LA Sparks + KD reviews — canonical fix)
    # Visa was systematically suppressed at 10-29% when persona-real is 65-82%.
    # Numbers from Federal Reserve consumer credit cardholder surveys + Forrester.
    ('CREDIT PROVIDER', 'VISA'):       {'18-24': 76, '25-34': 80, '35-44': 82, '45-54': 80, '55-64': 76, '65+': 70},
    ('CREDIT PROVIDER', 'MASTERCARD'): {'18-24': 40, '25-34': 48, '35-44': 50, '45-54': 46, '55-64': 42, '65+': 36},
    ('CREDIT PROVIDER', 'DISCOVER CREDIT CARD'): {'18-24': 18, '25-34': 22, '35-44': 24, '45-54': 22, '55-64': 20, '65+': 16},
    ('CREDIT PROVIDER', 'AMERICAN EXPRESS'): {'18-24': 14, '25-34': 18, '35-44': 22, '45-54': 24, '55-64': 22, '65+': 18},
    # Synchrony — store-card issuer (Amazon Store, PayPal Credit, Lowe's, Care Credit, Walmart).
    # (added 2026-05-25 per KD review — was at 2.67%, ~70M cardholders / 258M adults skews ~12-15%)
    ('CREDIT PROVIDER', 'SYNCHRONY'):  {'18-24':  8, '25-34': 12, '35-44': 14, '45-54': 14, '55-64': 12, '65+': 10},

    # TELECOM/ISP — Xfinity (Comcast) ~31M residential subs / ~131M US HH = ~24% HH coverage.
    # (added 2026-05-25 per KD review — was at 7.81%, panel-tracked ~18-22% for adults in coverage)
    ('TELECOM', 'XFINITY'): {'18-24': 14, '25-34': 20, '35-44': 22, '45-54': 22, '55-64': 20, '65+': 18},

    # STREAMING/PLATFORM — Amazon Prime Video ~150M US HH (Prime household halo).
    # (added 2026-05-25 per Gen Pop colleague review — was at 45% gen pop, real is 60-72%)
    # Younger heavy Prime adoption; older buckets still 50%+ via household sharing.
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): {'18-24': 58, '25-34': 70, '35-44': 72, '45-54': 70, '55-64': 64, '65+': 52},

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
    ('WHERE THEY SHOP', 'SEPHORA'):     {'18-24': 28, '25-34': 26, '35-44': 22, '45-54': 16, '55-64':  9, '65+':  5},
    ('WHERE THEY SHOP', 'ULTA BEAUTY'): {'18-24': 22, '25-34': 24, '35-44': 22, '45-54': 18, '55-64': 12, '65+':  8},

    # WHERE THEY SHOP — Target mass retail, slight female + young skew.
    # (added 2026-05-25 per Valkyrae audit — was at 38.30% on young female
    #  multicultural audience; persona-real 50-60%.)
    ('WHERE THEY SHOP', 'TARGET'): {'18-24': 52, '25-34': 56, '35-44': 52, '45-54': 46, '55-64': 38, '65+': 28},

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
    ('VIRTUAL MVPD FAST', 'XUMO'):         {'18-24':  4, '25-34':  6, '35-44':  8, '45-54': 10, '55-64': 10, '65+':  8},
    ('VIRTUAL MVPD FAST', 'YOUTUBE TV'):   {'18-24': 16, '25-34': 22, '35-44': 24, '45-54': 22, '55-64': 18, '65+': 14},
    ('VIRTUAL MVPD FAST', 'DIRECTV'):      {'18-24':  4, '25-34':  6, '35-44':  8, '45-54': 10, '55-64': 12, '65+': 14},

    # SEARCH ENGINE/AI — older Windows/Edge default surface.
    # (added 2026-05-25 per Patrick Stewart review — Bing/MSN systematically under-read
    #  for older audiences; Bing was 9.9% on 85% age-35+ audience.)
    ('SEARCH ENGINE/AI', 'BING'): {'18-24':  8, '25-34': 12, '35-44': 18, '45-54': 24, '55-64': 30, '65+': 32},
    ('SEARCH ENGINE/AI', 'MSN'):  {'18-24':  4, '25-34':  6, '35-44': 10, '45-54': 16, '55-64': 22, '65+': 26},
    # Copilot — bundled into Windows / Edge, older Windows skew.
    # (added 2026-05-25 per Sandra/Regina/Olivia/Queen lock-release pass — was at
    #  16.1172 lock on Regina/Queen but emerging audience-aware values should curve.)
    ('SEARCH ENGINE/AI', 'COPILOT'): {'18-24':  6, '25-34':  8, '35-44': 12, '45-54': 14, '55-64': 16, '65+': 14},

    # STREAMING/MUSIC — SiriusXM is age-curved (in-car commercial older). iHeart
    # and Pandora REMOVED from SEGMENT in favor of ETHNICITY (see Black radio
    # over-index below). Age-only would suppress Black audiences' real listening.
    ('STREAMING/MUSIC', 'SIRIUSXM'): {'18-24':  4, '25-34':  7, '35-44': 12, '45-54': 14, '55-64': 16, '65+': 16},

    # TELECOM — Spectrum ~30% US HH coverage; broadband + cable bundles.
    # NOTE: Spectrum is heavily REGIONAL (Texas/Carolinas/LA/NY) not just age.
    # Age curve approximates regional baseline; specific DMA tuning belongs in
    # the per-category research agent (Rule #2: reasoning > floors).
    ('TELECOM', 'SPECTRUM'): {'18-24': 12, '25-34': 16, '35-44': 18, '45-54': 18, '55-64': 18, '65+': 16},

    # APPAREL/FOOTWEAR — universal mass anchors. Nike + Adidas were stuck at
    # ~18-19% across Penelope/Patrick/Robin/Octavia (4 of 4 talent files in
    # the 5-25 batch). Adding age-curved targets (younger over-index).
    # (added 2026-05-25 per Penelope Cruz colleague review with backfill mention)
    ('APPAREL/FOOTWEAR', 'NIKE'):   {'18-24': 64, '25-34': 60, '35-44': 54, '45-54': 48, '55-64': 42, '65+': 36},
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
        if raw_col:
            new_row[raw_col] = int(round(sample_size * new_bp / 100.0))
        if proj_col:
            new_row[proj_col] = int(round(US_POP * new_bp / 100.0))
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
                ('SEARCH ENGINE/AI', 'CLAUDE AI'),
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
                if two_sided and abs(cur - floor) <= 3.0:
                    continue  # in band
                # tight jitter band ±1.5pp around persona target
                target = _jitter_for(
                    subject, brand_u, salt=f'panel-{target_band}-{cat_u}',
                    lo=max(0.05, floor - 1.5), hi=floor + 1.5,
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
# 2026-05-26 Defect Class #24 — TALENT ↔ sub-cat propagation
# ---------------------------------------------------------------------------
# Same defect as MPB ↔ sub-cat (Defect Class #23/#23b) but for talent rows.
# Audit on Gen Pop showed 892 TALENT names + 623 ACTOR + 420 ATHLETE + 375
# MUSICIAN + 53 HOST stuck at 0.005-0.030. Many of those names ARE present
# in BOTH TALENT and a sub-cat — but at very different magnitudes (e.g.
# Pedro Pascal TALENT 72.94% but ACTOR could be at floor for some profiles).
#
# This enforcer finds the MAX BP across (TALENT, ACTOR, ATHLETE,
# MUSICIAN/BAND, HOST/PERSONALITY, POLITICS/ACTIVIST, PODCAST, NFL ATHLETE,
# NBA ATHLETE, MLB ATHLETE, NHL ATHLETE, SOCCER) for each name and aligns
# every row of that name (in those cats) to MAX ± per-row deterministic
# jitter. Does NOT insert new rows.
# ---------------------------------------------------------------------------

_TALENT_FAMILY = {
    'TALENT', 'ACTOR', 'ATHLETE', 'MUSICIAN/BAND', 'MUSICIAN',
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
# Convenience wrapper
# ============================================================================

def run_all_enforcers(df, subject, brand_category=None, verbose=True):
    """Run every enforcer in order. Returns (df, total_changes)."""
    total = 0
    for fn in (
        strip_url_encoded_subject_dupes,
        strip_corporate_parents,
        strip_product_skus,
        strip_polluted_brand_values,
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
    return df, total
