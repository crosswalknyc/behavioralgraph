#!/usr/bin/env python3
"""Final ship gate - the independent terminal invariant check that runs
on the EXACT bytes about to land in s3://dashboard-inputs/, after every
enforcer, safety net, polish pass, and pre-publish gate has already had
its say.

Why this module exists (Jenna mandate, 2026-08-24): the audit in
PIPELINE_DEFECTS_FOR_JENNA_2026_08_24.md found four defect classes that
shipped DESPITE the 42-step enforcer chain, run_write_safety_net, and
run_pre_publish_gate. The root pattern was that our checks are
per-known-defect and mostly warn instead of block, and two of the four
defects were CREATED by enforcer interaction. A checker that shares
code with the enforcers can be blinded by the same bug that caused the
defect. So this gate:

  * does its OWN CSV parsing of the final frame / bytes,
  * does its OWN numeric coercion,
  * imports NOTHING from post_generation_enforcers.py, profile_writer's
    helpers, _set_bp, or the jitter modules.

The only cross-module import is migration.hostmap_norm.norm_key, which
is a pure data-lookup normalizer (explicitly allowed by the mandate).

Invariants asserted (each returns structured violations):

  I1 subject-only pins   no non-subject row BP >= 99.0 unless the
                         brand's independently-loaded Gen Pop baseline
                         is >= 30 (unknown baseline counts as < 30).
  I2 metadata            BRAND INPUT / SAMPLE SIZE / BRAND CATEGORY /
                         SUBJECT present exactly once; SAMPLE SIZE
                         BP = 100; BI Raw == SS Raw == sample; sample
                         last digit 1-9 and not a forbidden literal;
                         BRAND CATEGORY in the canonical set.
  I3 TU/cut rows         AVID FAN present on TUs with BP in [3, 55];
                         absent on cuts; CASUAL FAN absent everywhere.
  I4 subset sizing       cut sample strictly < parent TU sample; avid
                         cuts also per-brand subset coherence (blocks
                         on 10x count impossibilities where parent
                         reach >= 1%).
  I5 demo sums           9 canonical demos each sum to 100 +/- 0.5;
                         LOCATION to 100 +/- 1.5; no negative bucket.
  I6 chain math          Raw == round(BP/100 x sample) +/- 1 and
                         Proj == round(Raw/10M x 329.9M) +/- 18
                         (raw-rounding amplified x32.99).
  I7 share coherence     each non-demo category with a populated
                         Category Share sums to within [85, 115].
  I8 numeric artifacts   no comma, space, or misplaced percent sign in
                         the four numeric columns (reach/share carry
                         one trailing '%' by design); no exact-2dp BP
                         flood.
  I9 no hidden brands    nothing from reference/hostmap_hidden_brands
                         (norm-group semantics).
  I10 degenerate demos   TU files only: no canonical demo category may
                         hold a bucket >= 99 alongside a bucket at
                         exactly 0.0000 (a one-bucket demo on a TU is
                         the cut-mislabeled-as-TU defect signature;
                         sum-to-100 itself stays I5). Cuts are exempt:
                         gender / geo cuts legitimately pin one bucket
                         to ~99.99. Deterministic backstop under the
                         reasoned migration/demo_plausibility_gate.
  I11 reach above 100%   no row's BP may exceed 100.0 anywhere except
                         metadata rows and subject self-pins within
                         float noise of 100.
  I12 avid subset raws   avid cuts only, parent TU resolvable: every
                         shared non-exempt (category, brand) row must
                         satisfy round(cut BP/100 x cut sample) <=
                         round(parent BP/100 x parent sample). The
                         avid tier may over-index on penetration but
                         can never out-COUNT its parent (2026-08-25
                         partner finding: Bethenny avid shipped Real
                         Housewives of New York at 3.4x the parent's
                         panelist count). Parent lookup is fail-open:
                         unresolvable / unfetchable parent skips with
                         a log line, never blocks on infrastructure.
  I13 viewer carriage    consumption-scoped (viewers) TU files only:
                         the title's carrying platforms (from the
                         build's cached carriage research sidecar at
                         system/viewer_carriage/) must jointly cover
                         ~99+ of the streaming categories; exclusive
                         carrier ~100, multi-carrier union ~100 with
                         distinct 4dp values, alias rows consistent.
                         Fail-open when the sidecar is absent /
                         unconfident. Deterministically fixed by
                         enforce_viewer_carriage_constraint in the
                         writer's autofix pass (2026-08-26 Jenna
                         JKL/Rosie mandate).

  I16 self-property       content/franchise subjects: the subject's own
      coherence            merch/games/media rows must be coherent with
                           its own FRANCHISE anchor (>= 14% of it when
                           the anchor reads >= 40). 2026-08-26 Liz QA:
                           Paw Patrol shipped FRANCHISE 82.74 with own
                           TOYS/GAMES at 6.20. Deterministically fixed
                           by enforce_self_property_coherence (peer-
                           anchored, mirror-preserving arithmetic).
  I17 avid own-row        avid cuts only, parent TU resolvable: on the
      direction            subject's OWN property rows the avid BP must
                           read >= the parent's (avid fans always over-
                           index on their own property; Paw Patrol avid
                           shipped FRANCHISE at 3.97 vs parent 82.74).
                           Deterministically fixed by the own-row
                           direction pass in enforce_avid_subset_
                           coherence (parent-anchored premium, raw-
                           verified so I12 still holds).
  I18 exact-100           no non-subject row may sit at exactly
      non-subject pin      100.0000 (only metadata and the subject's
                           own self-pin family; companion sports pins
                           exempt). Paw Patrol shipped Paramount+ at
                           100.0000 on a universe defined across four
                           platforms. Deterministically fixed by
                           depin_exact_100_non_subject.
  I19 BRAND INPUT         URL-bearing BRAND INPUTs must not carry a
      landing page         generic platform landing page (fubo.tv/
                           welcome, netflix.com bare) - every visitor
                           of the platform would qualify. NOT auto-
                           fixed: the correct title URL is a research
                           judgment; the gate quarantines with a clear
                           reason instead.

Gate behavior: `run_final_ship_gate(df_or_bytes, s3_key, subject)`.
On violations the caller's upload MUST NOT happen: the gate writes the
rejected bytes to s3://dashboard-inputs/_quarantine/, emails Jenna +
Jessie a plain-language hold notice, and raises ShipGateError.

There is NO environment flag that downgrades this gate to warn-only.
The only way to bypass enforcement is the explicit `enforce=False`
function argument, which exists solely for the local ops override
(migration/local_override_profile.py) and for read-only audits. Every
external path (partner API, dashboard chatbot, queue worker, cut
engines) runs with enforce=True.
"""

from __future__ import annotations

import csv
import io
import os
import re
import threading
import time
from datetime import datetime, timezone

__all__ = [
    "ShipGateError",
    "run_final_ship_gate",
    "check_final_ship_invariants",
]

BUCKET = "dashboard-inputs"
QUARANTINE_PREFIX = "_quarantine/"
GENPOP_KEY = "Gen_Pop_2026.csv"
US_POP = 329_900_000
PANEL_DENOM = 10_000_000

HOLD_NOTICE_TO = ["jenna@crosswalknyc.com", "jessie@crosswalknyc.com"]
HOLD_NOTICE_FROM = "Crosswalk Ops <jenna@crosswalknyc.com>"

# The 9 canonical demo categories (both label spellings that appear in
# shipped files are accepted).
DEMO_CATS = {
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "PARENTAL_STATUS", "PARENTAL STATUS",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION",
}
META_CATS = {
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT",
    "INPUT_METADATA", "GENERAL",
}
FAN_CATS = {"AVID FAN", "CASUAL FAN"}

# Companion sport categories where a NON-subject row legitimately pins
# at 100 (Rule #3: the subject's team + league + conference + division
# rows are pinned alongside the subject). I1 skips these; I6/I7 still
# check their math.
COMPANION_SPORT_CATS = {
    "SPORTS TEAM", "MLB", "NBA", "NFL", "NHL", "MLS", "WNBA", "MILB",
    "EPL", "LA LIGA", "SERIE A", "LIGUE 1", "BUNDESLIGA", "CFB",
    "SOCCER", "AL", "NL", "AFC", "NFC", "AL/NL", "AFC/NFC",
    "WESTERN CONFERENCE", "EASTERN CONFERENCE",
}
_COMPANION_DIVISION_RE = re.compile(
    r"\b(EAST|WEST|NORTH|SOUTH|CENTRAL|PACIFIC|ATLANTIC|METROPOLITAN)\b"
)

# Kept in sync with scripts/_sample_size_jitter.py FORBIDDEN_LITERALS.
# Deliberately re-declared here (data constant, not logic) so this
# module never imports from the jitter helpers it is meant to audit.
FORBIDDEN_SAMPLE_LITERALS = {
    2001, 12345, 54321, 99999, 88888, 77777, 22222, 123456, 654321,
}

# Fallback snapshot of the canonical BRAND CATEGORY set, used only when
# bg-webapp/iq_rankers.py cannot be read at runtime. Source of truth
# stays iq_rankers.MASTER_CATEGORIES; this mirror follows
# .cursor/rules/canonical-brand-category.mdc.
_MASTER_CATEGORIES_FALLBACK = {
    # BRAND
    "ACCESSORIES", "ACTIVEWEAR", "AMUSEMENT PARKS", "APPAREL",
    "APPAREL/FOOTWEAR", "AUTOMOBILE", "B2B", "BANK", "BANKING",
    "BANKS", "BEAUTY",
    "BETTING", "BEVERAGE", "CASUAL DINING", "CPG", "CREDIT PROVIDERS",
    "CREDIT PROVIDER", "DIGITAL BANKING", "EVENTS", "FESTIVAL",
    "FOOTWEAR",
    "FRANCHISE", "GROCERY", "INTIMATES", "JEWELRY", "LOYALTY PROGRAMS",
    "MEMBERSHIP",
    "NON PROFIT/CHARITY", "PHARMA", "QSR", "RETAILERS", "SECURITY",
    "SHOPPING INTENT", "SWEEPSTAKES", "TECHNOLOGY/DEVICE", "TELECOM",
    "TICKETING", "TOY", "TRADING", "TRAVEL",
    "VENUE", "WHERE THEY SHOP", "WORKOUT FACILITY",
    # TALENT
    "ACTOR", "ATHLETE", "COMEDIAN", "INFLUENCER/CREATOR",
    "CREATOR/INFLUENCER", "EMERGING TALENT", "HOST/PERSONALITY",
    "MUSICIAN/BAND", "PODCASTER", "POLITICIAN", "POLITICS/ACTIVIST",
    "WRITER/DIRECTOR/AUTHOR/ARTIST",
    # CONTENT
    "GAME PLAYERS", "GAMES", "GAMES - PLAYERS", "MOVIE", "PODCAST",
    "VERTICAL SHORTS", "VIDEO GAME",
    # PLATFORMS
    "APP/PLATFORM", "BROADCAST/CABLE", "FAST CHANNEL", "FAST PLATFORM",
    "MEDIA", "MOVIE THEATER", "PLATFORMS", "SEARCH ENGINE",
    "SEARCH ENGINE/AI",
    "SOCIAL MEDIA", "STREAMING MUSIC", "STREAMING PLATFORM",
    "STREAMING VIDEO",
    "STREAMING/PLATFORM", "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST",
    "VMVPD/FAST", "VMVPD",
    # SPORT
    "MILB", "MLB", "NBA", "NFL", "SPORTS ORGANIZATIONS",
    "SPORTS ORGANIZATION", "SPORTS TEAM", "WNBA",
    # HEALTHCARE / TRENDS
    "HEALTHCARE", "TRENDS",
}

# JS-side aliases carried by shipped profiles but absent from the
# iq_rankers.py dict (they live only in the templates' JS list).
# Kept in the fallback so a re-upload of an existing profile is never
# held in fallback mode; the drift regression test
# (scripts/test_ship_gate_category_fallback.py) whitelists exactly
# this set as allowed extras.
_FALLBACK_ALIAS_EXTRAS = {"CREATOR/INFLUENCER"}

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


class ShipGateError(RuntimeError):
    """Raised when the final ship gate blocks an upload. Carries the
    structured violations plus the quarantine key (when written)."""

    def __init__(self, s3_key, violations, quarantine_key=None):
        self.s3_key = s3_key
        self.violations = violations or []
        self.quarantine_key = quarantine_key
        name = _display_name(s3_key)
        super().__init__(
            f"{name} was held before delivery: "
            f"{len(self.violations)} final quality check(s) did not "
            f"pass. The file was not published."
        )


# ---------------------------------------------------------------------------
# Parsing + coercion (self-contained on purpose)
# ---------------------------------------------------------------------------

def _display_name(s3_key):
    base = os.path.basename(str(s3_key or "").strip()) or "profile"
    if base.lower().endswith(".csv"):
        base = base[:-4]
    return base


def _norm_token(s):
    return _NON_ALNUM_RE.sub("", str(s or "").upper())


def _to_bytes(df_or_bytes):
    """Accept bytes, str, or a DataFrame-like (anything with .to_csv)."""
    if isinstance(df_or_bytes, bytes):
        return df_or_bytes
    if isinstance(df_or_bytes, str):
        return df_or_bytes.encode("utf-8")
    to_csv = getattr(df_or_bytes, "to_csv", None)
    if callable(to_csv):
        return str(to_csv(index=False)).encode("utf-8")
    raise TypeError(
        f"final_ship_gate expects bytes, str, or a frame with .to_csv; "
        f"got {type(df_or_bytes).__name__}"
    )


def _parse_rows(data):
    """Parse CSV bytes with the stdlib csv module. Returns
    (colmap, rows) where rows are dicts with the raw cell strings for
    the six canonical columns plus a 1-based data row number."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return None, []

    lower = [str(h or "").strip().lower() for h in header]

    def _find(pred):
        for i, h in enumerate(lower):
            if pred(h):
                return i
        return None

    colmap = {
        "cat": _find(lambda h: h == "column"),
        "val": _find(lambda h: h == "value"),
        "bp": _find(lambda h: h == "brand penetration (row)"
                    or h.startswith("brand penetration")),
        "cs": _find(lambda h: h == "category share"),
        "raw": _find(lambda h: h.startswith("original raw")),
        "proj": _find(lambda h: "projection" in h),
    }
    rows = []
    for n, cells in enumerate(reader, start=1):
        if not cells or all(not str(c).strip() for c in cells):
            continue

        def _cell(key):
            i = colmap.get(key)
            if i is None or i >= len(cells):
                return ""
            return str(cells[i])

        cat = _cell("cat").strip()
        rows.append({
            "n": n,
            "cat": cat,
            "cat_u": cat.upper(),
            "val": _cell("val").strip(),
            "bp_s": _cell("bp"),
            "cs_s": _cell("cs"),
            "raw_s": _cell("raw"),
            "proj_s": _cell("proj"),
        })
    return colmap, rows


def _num(s):
    """Lenient numeric coercion: tolerates '%', thousands commas, and
    stray spaces so chain math stays checkable even on artifact cells
    (I8 flags the artifact itself)."""
    t = str(s or "").strip()
    if not t:
        return None
    t = t.replace("%", "").replace(",", "").replace(" ", "")
    if not t or t.lower() in ("nan", "none", "null", "-"):
        return None
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def _decimals_shown(s):
    """Count decimal digits in the cell as written (e.g. '5.16' -> 2)."""
    t = str(s or "").strip().replace("%", "")
    m = re.match(r"^-?\d+\.(\d+)$", t)
    return len(m.group(1)) if m else 0


def _fmt_pct(v):
    return "-" if v is None else f"{v:.4f}%"


# ---------------------------------------------------------------------------
# Independent reference loads (Gen Pop baselines, canonical categories,
# hidden brands, bucket listing for parent resolution)
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_GENPOP_CACHE = {"map": None, "ts": 0.0}
_LISTING_CACHE = {"keys": None, "ts": 0.0}
_MASTER_CACHE = {"set": None}
_HIDDEN_CACHE = {"set": None}

_GENPOP_TTL = 3600.0
_LISTING_TTL = 120.0


def _s3(s3_client=None):
    if s3_client is not None:
        return s3_client
    import boto3
    return boto3.client("s3", region_name="us-east-2")


def _load_genpop_baselines(s3_client=None, verbose=True):
    """brand norm -> max BP across every Gen Pop category. Own fetch +
    own parse; never trusts the enforcers' Gen Pop plumbing. Returns
    None when Gen Pop is UNREACHABLE (I1 degrades to subject-only
    exemption with a loud log) and {} when it loads but is empty."""
    with _CACHE_LOCK:
        if (_GENPOP_CACHE["map"] is not None
                and time.time() - _GENPOP_CACHE["ts"] < _GENPOP_TTL):
            return _GENPOP_CACHE["map"]
    try:
        body = _s3(s3_client).get_object(
            Bucket=BUCKET, Key=GENPOP_KEY)["Body"].read()
        _, rows = _parse_rows(body)
        out = {}
        for r in rows:
            key = _norm_token(r["val"])
            if not key:
                continue
            bp = _num(r["bp_s"])
            if bp is None:
                continue
            if bp > out.get(key, -1.0):
                out[key] = bp
        with _CACHE_LOCK:
            _GENPOP_CACHE["map"] = out
            _GENPOP_CACHE["ts"] = time.time()
        return out
    except Exception as e:
        if verbose:
            print(f"[ship-gate] Gen Pop baseline load failed "
                  f"({type(e).__name__}: {e}); I1 falls back to "
                  f"subject-only exemptions")
        return None


def _load_master_categories(verbose=True):
    """Parse MASTER_CATEGORIES out of bg-webapp/iq_rankers.py with ast
    (no import of webapp code). Falls back to the embedded snapshot."""
    with _CACHE_LOCK:
        if _MASTER_CACHE["set"] is not None:
            return _MASTER_CACHE["set"]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "bg-webapp", "iq_rankers.py"),
        os.path.join(here, "iq_rankers.py"),
        os.path.join(here, "..", "iq_rankers.py"),
        "/root/finished_codes/bg-webapp/iq_rankers.py",
    ]
    cats = None
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            import ast
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                # Plain assign AND annotated assign (the live iq_rankers
                # declares `MASTER_CATEGORIES: dict[str, list[str]] = {...}`,
                # which is an AnnAssign; matching only Assign silently fell
                # back to the stale snapshot - found 2026-08-26).
                is_match = (
                    (isinstance(node, ast.Assign)
                     and any(getattr(t, "id", "") == "MASTER_CATEGORIES"
                             for t in node.targets))
                    or (isinstance(node, ast.AnnAssign)
                        and getattr(node.target, "id", "") ==
                        "MASTER_CATEGORIES" and node.value is not None)
                )
                if is_match:
                    parsed = ast.literal_eval(node.value)
                    flat = set()
                    if isinstance(parsed, dict):
                        for vals in parsed.values():
                            for v in (vals or []):
                                flat.add(str(v).strip().upper())
                    if flat:
                        cats = flat
                    break
            if cats:
                break
        except Exception as e:
            if verbose:
                print(f"[ship-gate] MASTER_CATEGORIES parse failed on "
                      f"{path}: {e}")
    if not cats:
        cats = set(_MASTER_CATEGORIES_FALLBACK)
    with _CACHE_LOCK:
        _MASTER_CACHE["set"] = cats
    return cats


def _load_hidden_norms(verbose=True):
    """Norm keys of all-hidden brand groups from
    reference/hostmap_hidden_brands.txt. The cache file is generated
    under norm-group semantics (a group with ANY visible spelling never
    appears), so a plain norm-membership test is exact."""
    with _CACHE_LOCK:
        if _HIDDEN_CACHE["set"] is not None:
            return _HIDDEN_CACHE["set"]
    try:
        from migration.hostmap_norm import norm_key
    except ImportError:
        try:
            from hostmap_norm import norm_key  # type: ignore
        except ImportError:
            norm_key = _norm_token
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "reference", "hostmap_hidden_brands.txt"),
        os.path.join(here, "reference", "hostmap_hidden_brands.txt"),
        "/root/finished_codes/reference/hostmap_hidden_brands.txt",
    ]
    hidden = set()
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    k = norm_key(line.strip())
                    if k:
                        hidden.add(k)
            break
        except Exception as e:
            if verbose:
                print(f"[ship-gate] hidden-brand list load failed on "
                      f"{path}: {e}")
    with _CACHE_LOCK:
        _HIDDEN_CACHE["set"] = hidden
    return hidden


def _list_root_csvs(s3_client=None, force=False):
    """Root-level CSV keys with LastModified (Delimiter='/' so backups
    and system files never enter parent resolution)."""
    with _CACHE_LOCK:
        if (not force and _LISTING_CACHE["keys"] is not None
                and time.time() - _LISTING_CACHE["ts"] < _LISTING_TTL):
            return _LISTING_CACHE["keys"]
    s3 = _s3(s3_client)
    keys = []
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Delimiter": "/"}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            k = obj.get("Key", "")
            if k.lower().endswith(".csv"):
                keys.append((k, obj.get("LastModified")))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    with _CACHE_LOCK:
        _LISTING_CACHE["keys"] = keys
        _LISTING_CACHE["ts"] = time.time()
    return keys


_ERA_RE = re.compile(r"\b(20\d{2})(\s*[/-]\s*20\d{2})?(\s+YTD)?\b")


def _era_token(cut_suffix):
    """Era/window token in a cut suffix ('2023', '2023/2024',
    '2026 YTD'), normalized for comparison. None when the cut isn't
    window-scoped."""
    m = _ERA_RE.search(str(cut_suffix or ""))
    if not m:
        return None
    return _norm_token(m.group(0))


def _era_parent_key(base, stem_norm, era_norm, listing):
    """Same-era parent for an era-scoped cut: '{Stem} - {Era} Total
    Universe', '{Stem} - {Era} TU', or bare '{Stem} - {Era}'. Never
    the cut itself."""
    base_norm = _norm_token(base)
    wanted = {stem_norm + era_norm + suffix
              for suffix in ("TOTALUNIVERSE", "TU", "")}
    for k, _lm in listing:
        kb = _display_name(k)
        if " - " not in kb:
            continue
        kb_norm = _norm_token(kb)
        if kb_norm != base_norm and kb_norm in wanted:
            return k
    return None


def _resolve_parent_tu(s3_key, s3_client=None, verbose=True):
    """For a cut named '{Subject} - {Cut}.csv', find the latest TU file
    for the same subject in the bucket root. TU keys either equal the
    subject stem or carry a trailing dated suffix (digits/underscores).
    Returns (parent_key, parent_bytes) or (None, None).

    Era/window-scoped cuts ('Vin Diesel - 2023 Avid Fan', 'Costco -
    2025 Avid Fan', 'Reba - 2023/2024 TU') answer to THEIR OWN era,
    never the current TU (skins rule: the window in the name IS the
    window; comparing a 2023-window cut against the 2026 TU is
    wrong-parent math). They resolve a same-era parent when one
    exists, otherwise they are standalone for subset purposes (no
    I4/I12 binding against a different-window TU)."""
    base = _display_name(s3_key)
    stem = base.split(" - ")[0].strip()
    stem_norm = _norm_token(stem)
    if not stem_norm:
        return None, None

    cut_suffix = base.split(" - ", 1)[1].strip() if " - " in base else ""
    era_norm = _era_token(cut_suffix)
    if era_norm:
        listing = _list_root_csvs(s3_client)
        pkey = _era_parent_key(base, stem_norm, era_norm, listing)
        if pkey is None:
            listing = _list_root_csvs(s3_client, force=True)
            pkey = _era_parent_key(base, stem_norm, era_norm, listing)
        if pkey is None:
            if verbose:
                print(f"[ship-gate] era-scoped cut '{base}' has no "
                      f"same-era parent; standalone (no subset "
                      f"binding)")
            return None, None
        try:
            body = _s3(s3_client).get_object(
                Bucket=BUCKET, Key=pkey)["Body"].read()
            return pkey, body
        except Exception as e:
            if verbose:
                print(f"[ship-gate] era parent fetch failed for "
                      f"{pkey}: {e}")
            return None, None

    def _candidates(listing):
        found = []
        for k, lm in listing:
            kb = _display_name(k)
            if " - " in kb:
                continue
            if _norm_token(kb).startswith("GENPOP"):
                continue
            kb_norm = _norm_token(kb)
            if kb_norm == stem_norm:
                found.append((k, lm))
                continue
            if kb_norm.startswith(stem_norm):
                # Dated TU pattern: '<STEM>_08_24_2026_22_57' norms to
                # '<STEMNORM>' + 12 digits. Require an all-digit tail
                # of at least 8 so 'REBA' never matches 'REBALANCE'.
                tail = kb_norm[len(stem_norm):]
                if tail.isdigit() and len(tail) >= 8:
                    found.append((k, lm))
        return found

    listing = _list_root_csvs(s3_client)
    cands = _candidates(listing)
    if not cands:
        # One forced refresh: the parent may have been written seconds
        # ago (TU and cut build in the same run).
        listing = _list_root_csvs(s3_client, force=True)
        cands = _candidates(listing)
    if not cands:
        return None, None

    def _build_ts(k, lm):
        # Prefer the build timestamp embedded in dated TU filenames
        # ('..._MM_DD_YYYY_HH_MM.csv') over S3 LastModified: in-place
        # corrections churn LastModified, so a stale TU touched by a
        # sweep can outrank the current build (Bethenny 2026-08-27:
        # the June TU beat the 08-24 parent after a credit-grid sweep
        # bumped its LastModified, producing false I4/I12 flags).
        m = re.search(r"(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})\.csv$", k)
        if m:
            mo, d, y, hh, mi = (int(x) for x in m.groups())
            try:
                return datetime(y, mo, d, hh, mi, tzinfo=timezone.utc)
            except ValueError:
                pass
        return lm

    cands.sort(
        key=lambda t: (_build_ts(t[0], t[1]) is not None,
                       _build_ts(t[0], t[1])),
        reverse=True)
    parent_key = cands[0][0]
    try:
        body = _s3(s3_client).get_object(
            Bucket=BUCKET, Key=parent_key)["Body"].read()
        return parent_key, body
    except Exception as e:
        if verbose:
            print(f"[ship-gate] parent fetch failed for {parent_key}: "
                  f"{e}")
        return None, None


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------

def _rows_for_cat(rows, cat_u):
    return [r for r in rows if r["cat_u"] == cat_u]


def _extract_sample(rows):
    """Sample size = SAMPLE SIZE row's Raw (fallback BRAND INPUT Raw)."""
    for cat in ("SAMPLE SIZE", "BRAND INPUT"):
        for r in _rows_for_cat(rows, cat):
            v = _num(r["raw_s"])
            if v is not None and v > 0:
                return int(round(v))
    return None


def _subject_forms(subject, s3_key, rows):
    """(full_norms, mono_norms). Full forms exempt anywhere; mononyms
    only when they are the sole subject representation in a category.
    Sources: the subject argument, the file basename's first segment,
    the SUBJECT row value, and the BRAND INPUT seed segments."""
    raw_forms = set()
    subj = str(subject or "").strip()
    if subj:
        raw_forms.add(subj)
        if " - " in subj:
            raw_forms.add(subj.split(" - ")[0].strip())
    base = _display_name(s3_key)
    if base:
        raw_forms.add(base.split(" - ")[0].strip())
        # Dated TU basenames: strip the trailing _MM_DD_YYYY_HH_MM.
        undated = re.sub(r"[_\s]\d{2}[_\s]\d{2}[_\s]\d{4}.*$", "",
                         base.split(" - ")[0]).replace("_", " ").strip()
        if undated:
            raw_forms.add(undated)
    for r in _rows_for_cat(rows, "SUBJECT"):
        if r["val"]:
            raw_forms.add(r["val"])
    for r in _rows_for_cat(rows, "BRAND INPUT"):
        for seg in str(r["val"] or "").split(","):
            seg = seg.strip()
            if seg and len(seg) >= 2:
                raw_forms.add(seg)

    primary = _norm_token(subj.split(" - ")[0] if subj else base)
    full, mono = set(), set()
    for f in raw_forms:
        n = _norm_token(f)
        if not n:
            continue
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", f) if t]
        if len(tokens) >= 2 or n == primary:
            full.add(n)
        else:
            mono.add(n)
    # Name tokens of the subject itself count as mononym forms
    # (e.g. 'BETHENNY' alone in a first-name-keyed category).
    for t in re.split(r"[^A-Za-z0-9]+", subj):
        if len(t) >= 3:
            mono.add(_norm_token(t))
    return full, mono


# ---------------------------------------------------------------------------
# Viewer-carriage sidecar (2026-08-26 Jenna JKL/Rosie mandate).
# The build-time research step caches its carriage facts at
# system/viewer_carriage/<NORM>.json; the gate reads that sidecar with
# its OWN fetch + parse (no import of migration.viewer_carriage, per
# this module's independence mandate) and validates that a
# consumption-scoped universe's carrying platforms jointly cover ~100%.
# ---------------------------------------------------------------------------

CARRIAGE_PREFIX = "system/viewer_carriage/"
_CARRIAGE_CACHE = {}
_CARRIAGE_TTL = 300.0
_CARRIAGE_CATS = {
    "STREAMING/PLATFORM", "STREAMING VIDEO",
    "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD/FAST", "VMVPD",
}
# Cheap pre-filter so the sidecar GET only happens for subjects whose
# name carries consumption vocabulary at all.
_CARRIAGE_VOCAB_RE = re.compile(
    r"(?i)\b(viewers?|watchers?|streamers?|bingers?|watched|streamed|"
    r"binged)\b")


def _carriage_row_match(platform, value):
    """Alias-aware carrier-vs-row match, self-contained: 'Hulu'
    matches 'Disney+/Hulu' (components split on '/', '&', ',', '|',
    ' + '); 'Disney+' matches a 'Disney' component."""
    cn = _norm_token(platform)
    rv = str(value or "").strip()
    if not cn or not rv:
        return False
    if _norm_token(rv) == cn:
        return True
    for p in re.split(r"\s*(?:/|&|,|\|)\s*|\s+\+\s+", rv):
        pn = _norm_token(p)
        if pn == cn:
            return True
        if cn.rstrip("+") and pn.rstrip("+") == cn.rstrip("+"):
            return True
    return False


def _load_carriage_doc(subject, s3_key, s3_client, verbose=True):
    """Cached carriage facts for this subject (stem before ' - '), or
    None. Own S3 fetch + JSON parse; fail-open on everything. Only
    enforceable docs (confident research with carriers) are returned."""
    stem = str(subject or _display_name(s3_key)).split(" - ")[0].strip()
    if not stem or not _CARRIAGE_VOCAB_RE.search(stem):
        return None
    n = _norm_token(stem)
    if not n:
        return None
    now = time.time()
    with _CACHE_LOCK:
        hit = _CARRIAGE_CACHE.get(n)
        if hit and now - hit[1] < _CARRIAGE_TTL:
            return hit[0]
    doc = None
    try:
        import json as _json
        body = _s3(s3_client).get_object(
            Bucket=BUCKET, Key=f"{CARRIAGE_PREFIX}{n}.json")["Body"].read()
        raw = _json.loads(body.decode("utf-8"))
        if (isinstance(raw, dict) and raw.get("consumption_scoped")
                and not raw.get("research_failed")
                and raw.get("confident", True)
                and isinstance(raw.get("carriers"), list)
                and raw.get("carriers")):
            doc = raw
    except Exception:
        doc = None
    with _CACHE_LOCK:
        _CARRIAGE_CACHE[n] = (doc, now)
    if doc and verbose:
        print(f"[ship-gate] viewer-carriage facts loaded for {stem!r}: "
              f"{', '.join(str(c.get('platform')) for c in doc['carriers'])}")
    return doc


def _is_cut_key(s3_key):
    return " - " in _display_name(s3_key)


def _is_avid_cut_key(s3_key):
    base = _display_name(s3_key)
    if " - " not in base:
        return False
    return "AVID" in base.split(" - ", 1)[1].upper()


def _skip_gate_reason(s3_key):
    """Files the gate does not apply to: baseline reference files and
    anything not in the bucket root (backups, system, quarantine)."""
    key = str(s3_key or "").strip()
    if "/" in key:
        return f"non-root key ({key.split('/')[0]}/)"
    if re.match(r"(?i)^gen[_\s]?pop", os.path.basename(key)):
        return "Gen Pop baseline file"
    return None


# ---------------------------------------------------------------------------
# The invariants (I1-I13)
# ---------------------------------------------------------------------------

def _v(code, name, where, value, plain):
    return {"code": code, "invariant": name, "where": where,
            "value": value, "plain": plain}


def _check_i1(rows, subject, s3_key, s3_client, verbose):
    out = []
    baselines = _load_genpop_baselines(s3_client, verbose=verbose)
    full, mono = _subject_forms(subject, s3_key, rows)
    # Viewer-carriage carve-out (2026-08-26 JKL/Rosie mandate): on a
    # consumption-scoped universe the CARRYING platform legitimately
    # reads ~100 in the streaming categories even though it is not the
    # subject. Verified against the build's cached carriage facts.
    carriage = _load_carriage_doc(subject, s3_key, s3_client, verbose)
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["cat_u"], []).append(r)
    for cat_u, cat_rows in by_cat.items():
        if (cat_u in DEMO_CATS or cat_u in META_CATS or cat_u in FAN_CATS
                or cat_u == "LOCATION"):
            continue
        if cat_u in ("AGE_OF_CHILDREN", "AGE OF CHILDREN"):
            # Demo-family readout ("No Kids" legitimately reads 100 on
            # adult universes); exempted here exactly as in I18.
            continue
        if (cat_u in COMPANION_SPORT_CATS
                or _COMPANION_DIVISION_RE.search(cat_u)):
            continue
        cat_has_full_subject = any(
            _norm_token(r["val"]) in full for r in cat_rows)
        for r in cat_rows:
            bp = _num(r["bp_s"])
            if bp is None or bp < 99.0:
                continue
            vn = _norm_token(r["val"])
            if vn in full:
                continue
            if vn in mono and not cat_has_full_subject:
                # Mononym standing alone for the subject in this
                # category (sole representation) is a subject pin.
                continue
            if carriage:
                _cmatch = [c for c in carriage["carriers"]
                           if _carriage_row_match(c.get("platform", ""),
                                                  r["val"])]
                if _cmatch and cat_u in _CARRIAGE_CATS:
                    # Verified carrier of a consumption-scoped universe.
                    continue
                if (_cmatch and cat_u == "BROADCAST/CABLE"
                        and any((c.get("kind") or "").strip().lower()
                                == "network_app" for c in _cmatch)):
                    # 2026-08-27 (Golden Girls Viewers hold): a
                    # verified NETWORK-APP carrier (the network's own
                    # app/site streams the title, e.g. abc.com full
                    # episodes) legitimately extends its near-100 read
                    # to the network's BROADCAST/CABLE row. Linear-only
                    # networks never match here - research only returns
                    # digital carriers, so a non-carrier cable row
                    # (Hallmark Channel airing the title in linear
                    # syndication) still violates.
                    continue
            # 2026-08-26 Jenna convention: the subject's own property
            # row and its owning / universe-defining platform pin at
            # exactly 100 in base and cuts - never an I1 violation.
            try:
                try:
                    from migration.self_property_coherence import (
                        must_pin_100 as _i1_pin,
                        is_owner_platform_row as _i1_owner,
                        is_subject_own as _i1_own,
                        is_principal_cast as _i1_cast,
                    )
                except ImportError:
                    from self_property_coherence import (  # type: ignore
                        must_pin_100 as _i1_pin,
                        is_owner_platform_row as _i1_owner,
                        is_subject_own as _i1_own,
                        is_principal_cast as _i1_cast,
                    )
                if (_i1_pin(subject, cat_u, r["val"])
                        or _i1_owner(subject, r["val"])
                        or _i1_own(subject, r["val"])
                        or _i1_cast(subject, cat_u, r["val"])):
                    continue
                # Cut-defining row: on a platform-scoped cut ("Reba
                # McEntire - Apple Music Fan") the named platform row
                # legitimately reads ~100.
                base = os.path.basename(str(s3_key or ""))
                if base.lower().endswith(".csv"):
                    base = base[:-4]
                if " - " in base:
                    cl = re.sub(r"[^A-Z0-9]", "",
                                base.split(" - ", 1)[1].upper())
                    vn2 = re.sub(r"[^A-Z0-9]", "", str(r["val"]).upper())
                    if len(vn2) >= 3 and (vn2 in cl or cl in vn2):
                        continue
                    # Cut scoped to a title whose carrier is in the
                    # owner map ("Reba McEntire - Happy's Place Fan"
                    # -> Peacock): strip the audience noun off the
                    # cut label and consult the map.
                    try:
                        from migration.self_property_coherence import (
                            OWNER_PLATFORM_MAP as _opm,
                            own_token_words as _otw,
                        )
                    except ImportError:
                        from self_property_coherence import (  # type: ignore
                            OWNER_PLATFORM_MAP as _opm,
                            own_token_words as _otw,
                        )
                    cl_tok = "".join(_otw(base.split(" - ", 1)[1]))
                    if vn2 in _opm.get(cl_tok, ()):
                        continue
            except Exception:
                pass
            if baselines is not None:
                base_bp = baselines.get(vn)
                if base_bp is not None and base_bp >= 30.0:
                    continue
                base_txt = ("not tracked" if base_bp is None
                            else f"{base_bp:.1f}%")
            else:
                base_txt = "unavailable"
            out.append(_v(
                "I1", "subject-only pins",
                f"{r['cat']} / {r['val']}", _fmt_pct(bp),
                f"{r['val']} shows {_fmt_pct(bp)} reach in {r['cat']}. "
                f"Only the profile subject can sit at a near-100% row; "
                f"this brand's general-population reach ({base_txt}) "
                f"does not support it.",
            ))
    return out


def _check_i2(rows, subject, verbose):
    out = []
    counts = {c: len(_rows_for_cat(rows, c))
              for c in ("BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY",
                        "SUBJECT")}
    for cat, n in counts.items():
        if n != 1:
            word = "missing" if n == 0 else f"present {n} times"
            out.append(_v(
                "I2", "metadata completeness", cat, str(n),
                f"The file's {cat} row is {word}; it must appear "
                f"exactly once.",
            ))

    ss = _rows_for_cat(rows, "SAMPLE SIZE")
    bi = _rows_for_cat(rows, "BRAND INPUT")
    sample = None
    if ss:
        ss_bp = _num(ss[0]["bp_s"])
        if ss_bp is None or abs(ss_bp - 100.0) > 1e-6:
            out.append(_v(
                "I2", "metadata completeness", "SAMPLE SIZE",
                _fmt_pct(ss_bp),
                f"The SAMPLE SIZE row reads {_fmt_pct(ss_bp)} instead "
                f"of 100%.",
            ))
        ss_raw = _num(ss[0]["raw_s"])
        if ss_raw is None or ss_raw <= 0:
            out.append(_v(
                "I2", "metadata completeness", "SAMPLE SIZE",
                str(ss[0]["raw_s"]),
                "The SAMPLE SIZE row carries no usable audience count.",
            ))
        else:
            sample = int(round(ss_raw))
    if bi and sample is not None:
        bi_raw = _num(bi[0]["raw_s"])
        if bi_raw is None or int(round(bi_raw)) != sample:
            out.append(_v(
                "I2", "metadata completeness", "BRAND INPUT",
                str(bi[0]["raw_s"]),
                f"The BRAND INPUT count ({bi[0]['raw_s'] or 'blank'}) "
                f"does not match the audience size ({sample:,}).",
            ))
    if sample is not None:
        if sample % 10 == 0:
            out.append(_v(
                "I2", "metadata completeness", "SAMPLE SIZE",
                f"{sample:,}",
                f"The audience size {sample:,} ends in a zero, which "
                f"is not a valid delivered count.",
            ))
        if sample in FORBIDDEN_SAMPLE_LITERALS:
            out.append(_v(
                "I2", "metadata completeness", "SAMPLE SIZE",
                f"{sample:,}",
                f"The audience size {sample:,} is a placeholder value "
                f"and cannot ship.",
            ))

    bc = _rows_for_cat(rows, "BRAND CATEGORY")
    if bc:
        val_u = str(bc[0]["val"] or "").strip().upper()
        canon = _load_master_categories(verbose=verbose)
        ok = (val_u in canon) or val_u.startswith("SERIES")
        if not val_u or not ok:
            out.append(_v(
                "I2", "metadata completeness", "BRAND CATEGORY",
                bc[0]["val"],
                f"The BRAND CATEGORY value {bc[0]['val']!r} is not one "
                f"of the approved category labels.",
            ))
    return out, sample


def _check_i3(rows, s3_key):
    out = []
    is_cut = _is_cut_key(s3_key)
    avid_rows = _rows_for_cat(rows, "AVID FAN")
    casual_rows = _rows_for_cat(rows, "CASUAL FAN")
    if is_cut:
        if avid_rows:
            out.append(_v(
                "I3", "TU/cut rows", "AVID FAN", str(len(avid_rows)),
                "This derived cut carries an AVID FAN row; cuts must "
                "not include one.",
            ))
    else:
        if len(avid_rows) != 1:
            word = ("missing" if not avid_rows
                    else f"present {len(avid_rows)} times")
            out.append(_v(
                "I3", "TU/cut rows", "AVID FAN", str(len(avid_rows)),
                f"This Total Universe file's AVID FAN row is {word}; "
                f"it must appear exactly once.",
            ))
        elif avid_rows:
            bp = _num(avid_rows[0]["bp_s"])
            if bp is None or not (3.0 <= bp <= 55.0):
                out.append(_v(
                    "I3", "TU/cut rows", "AVID FAN", _fmt_pct(bp),
                    f"The AVID FAN share reads {_fmt_pct(bp)}, outside "
                    f"the plausible 3% to 55% range.",
                ))
    if casual_rows:
        out.append(_v(
            "I3", "TU/cut rows", "CASUAL FAN", str(len(casual_rows)),
            "A CASUAL FAN row is present; that row was retired and "
            "must not ship on any file.",
        ))
    return out


def _check_i4(rows, sample, subject, s3_key, s3_client, verbose):
    out = []
    if not _is_cut_key(s3_key) or sample is None:
        return out
    try:
        parent_key, parent_body = _resolve_parent_tu(
            s3_key, s3_client, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"[ship-gate] I4 parent resolution errored: {e}; "
                  f"subset check skipped")
        return out
    if not parent_key:
        if verbose:
            print(f"[ship-gate] I4: no parent TU resolvable for "
                  f"{_display_name(s3_key)}; subset check skipped")
        return out
    _, parent_rows = _parse_rows(parent_body)
    parent_sample = _extract_sample(parent_rows)
    if not parent_sample:
        return out
    if sample >= parent_sample:
        out.append(_v(
            "I4", "subset sizing", "SAMPLE SIZE",
            f"{sample:,} vs {parent_sample:,}",
            f"This cut lists an audience of {sample:,}, which is not "
            f"smaller than its parent file "
            f"{_display_name(parent_key)} ({parent_sample:,}). A cut "
            f"must always be a strict subset of its parent.",
        ))

    if _is_avid_cut_key(s3_key):
        full, mono = _subject_forms(subject, s3_key, rows)
        parent_bp = {}
        for r in parent_rows:
            cu = r["cat_u"]
            if (cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS
                    or cu == "LOCATION"):
                continue
            bp = _num(r["bp_s"])
            if bp is not None:
                parent_bp[(cu, _norm_token(r["val"]))] = bp
        n_flagged = 0
        for r in rows:
            cu = r["cat_u"]
            if (cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS
                    or cu == "LOCATION"):
                continue
            bp = _num(r["bp_s"])
            if bp is None or bp < 1.0:
                continue
            vn = _norm_token(r["val"])
            if vn in full or vn in mono:
                continue
            pbp = parent_bp.get((cu, vn))
            # Per-brand coherence blocks only on order-of-magnitude
            # impossibilities (parent reach >= 1% and the cut's count
            # more than 10x the parent's count). The row-by-row engine
            # legitimately reads an avid audience far above the parent
            # average on shared brands, and a sub-threshold gap usually
            # traces to a parent under-read rather than a cut defect
            # (dry-run 2026-08-24: 11 such rows on a same-night
            # known-good avid file). Sizing above stays strict.
            if pbp is None or pbp < 1.0:
                continue
            if sample * bp > parent_sample * pbp * 10.0:
                n_flagged += 1
                if n_flagged <= 25:
                    out.append(_v(
                        "I4", "subset sizing",
                        f"{r['cat']} / {r['val']}",
                        f"cut {_fmt_pct(bp)} vs parent {_fmt_pct(pbp)}",
                        f"{r['val']} in {r['cat']}: the cut's audience "
                        f"for this brand ({sample:,} x {_fmt_pct(bp)}) "
                        f"is more than ten times what the parent file "
                        f"carries ({parent_sample:,} x {_fmt_pct(pbp)}). "
                        f"A subset cannot out-count its parent.",
                    ))
        if n_flagged > 25:
            out.append(_v(
                "I4", "subset sizing", "(additional rows)",
                str(n_flagged - 25),
                f"{n_flagged - 25} additional row(s) exceed the parent "
                f"file's counts the same way.",
            ))
    return out


def _check_i5(rows):
    out = []
    sums = {}
    for r in rows:
        cu = r["cat_u"]
        if cu in DEMO_CATS or cu == "LOCATION":
            bp = _num(r["bp_s"])
            if bp is None:
                continue
            sums.setdefault(cu, 0.0)
            sums[cu] += bp
            if bp < 0:
                out.append(_v(
                    "I5", "demo sums", f"{r['cat']} / {r['val']}",
                    _fmt_pct(bp),
                    f"{r['val']} in {r['cat']} carries a negative "
                    f"share.",
                ))
    for cu, total in sums.items():
        tol = 1.5 if cu == "LOCATION" else 0.5
        if abs(total - 100.0) > tol:
            out.append(_v(
                "I5", "demo sums", cu, f"{total:.2f}%",
                f"{cu} rows total {total:.2f}% instead of 100%.",
            ))
    return out


def _check_i6(rows, sample):
    out = []
    if sample is None:
        return out
    # International frames (Omaze precedent): projections scale to the
    # file's own country universe (SAMPLE SIZE row Proj at BP=100), not
    # the US 10M-panel chain. Detection is content-signature based and
    # conservative - US files keep the exact chain below.
    intl_proj_base = None
    try:
        from migration.international_profiles import (
            detect_country_from_pairs,
        )
        if detect_country_from_pairs([(r["cat"], r["val"]) for r in rows]):
            for r in rows:
                if r["cat_u"] in ("SAMPLE SIZE", "BRAND INPUT"):
                    _bp0 = _num(r["bp_s"])
                    _pj0 = _num(r["proj_s"])
                    if _bp0 and _pj0 and _bp0 > 0:
                        intl_proj_base = _pj0 / (_bp0 / 100.0)
                        break
    except Exception:
        intl_proj_base = None
    n_flagged = 0
    for r in rows:
        cu = r["cat_u"]
        # BRAND INPUT is exempt: on persona-style profiles its BP is an
        # elevated value while Raw stays equal to the sample (Rule #3
        # persona carve-out). Its Raw==sample is asserted by I2.
        if cu in ("BRAND INPUT", "BRAND CATEGORY", "INPUT_METADATA"):
            continue
        bp = _num(r["bp_s"])
        raw = _num(r["raw_s"])
        proj = _num(r["proj_s"])
        if bp is None and raw is None and proj is None:
            continue
        if bp is None or raw is None or proj is None:
            n_flagged += 1
            if n_flagged <= 25:
                out.append(_v(
                    "I6", "chain math", f"{r['cat']} / {r['val']}",
                    f"bp={r['bp_s']!r} raw={r['raw_s']!r} "
                    f"proj={r['proj_s']!r}",
                    f"{r['val']} in {r['cat']} is missing one of its "
                    f"three linked numbers.",
                ))
            continue
        want_raw = round(bp / 100.0 * sample)
        if abs(raw - want_raw) > 1:
            n_flagged += 1
            if n_flagged <= 25:
                out.append(_v(
                    "I6", "chain math", f"{r['cat']} / {r['val']}",
                    f"raw={raw:,.0f} expected={want_raw:,}",
                    f"{r['val']} in {r['cat']}: the raw count "
                    f"{raw:,.0f} does not match {_fmt_pct(bp)} of the "
                    f"{sample:,} audience ({want_raw:,}).",
                ))
            continue
        # Tolerance 18: shipped projections are computed from the
        # unrounded raw value while the Raw cell stores the rounded
        # integer; the 10M -> 329.9M scale-up multiplies as much as
        # 0.5 of raw rounding into ~16.5 projection units (dry-run
        # 2026-08-24 measured deltas of 6-11 on same-night files).
        if intl_proj_base:
            want_proj = round(bp / 100.0 * intl_proj_base)
        else:
            want_proj = round(raw / PANEL_DENOM * US_POP)
        if abs(proj - want_proj) > 18:
            n_flagged += 1
            if n_flagged <= 25:
                out.append(_v(
                    "I6", "chain math", f"{r['cat']} / {r['val']}",
                    f"proj={proj:,.0f} expected={want_proj:,}",
                    f"{r['val']} in {r['cat']}: the projected audience "
                    f"{proj:,.0f} does not follow from its reach "
                    f"({want_proj:,} expected).",
                ))
    if n_flagged > 25:
        out.append(_v(
            "I6", "chain math", "(additional rows)",
            str(n_flagged - 25),
            f"{n_flagged - 25} additional row(s) fail the same "
            f"arithmetic.",
        ))
    return out


def _check_i7(rows):
    out = []
    by_cat = {}
    for r in rows:
        cu = r["cat_u"]
        if cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS:
            continue
        by_cat.setdefault(cu, []).append(r)
    for cu, cat_rows in by_cat.items():
        if cu in ("NUMBER_OF_CHILDREN", "NUMBER OF CHILDREN",
                  "AGE_OF_CHILDREN", "AGE OF CHILDREN"):
            # Legacy demo-family categories: partial bucket sets by
            # construction, shares mirror reach and cannot sum to 100.
            continue
        cs_vals = [_num(r["cs_s"]) for r in cat_rows]
        populated = [v for v in cs_vals if v is not None]
        if len(cat_rows) < 2 or len(populated) < max(2, len(cat_rows) // 2):
            continue
        total = sum(populated)
        if total == 0.0 and all((_num(r["bp_s"]) or 0.0) == 0.0
                                for r in cat_rows):
            # All-zero category (e.g. every nonzero row was a hidden
            # brand): there is no share to refresh.
            continue
        if not (85.0 <= total <= 115.0):
            out.append(_v(
                "I7", "share coherence", cu, f"{total:.1f}",
                f"{cu}: the Category Share column totals {total:.1f} "
                f"instead of about 100, which means the shares were "
                f"not refreshed after the last edit.",
            ))
    return out


def _check_i8(rows):
    out = []
    n_artifacts = 0
    n_2dp = 0
    sample_2dp = []
    for r in rows:
        cu = r["cat_u"]
        # Reach and share cells legitimately ship with one trailing
        # percent sign (the delivered format is f"{bp:.4f}%"); only a
        # misplaced or repeated sign, a comma, or an internal space is
        # an artifact there. Raw counts and projections must be bare
        # digits.
        for label, cell, pct_ok in (("reach", r["bp_s"], True),
                                    ("share", r["cs_s"], True),
                                    ("raw count", r["raw_s"], False),
                                    ("projection", r["proj_s"], False)):
            s = str(cell or "").strip()
            if not s:
                continue
            core = s[:-1] if (pct_ok and s.endswith("%")) else s
            if "%" in core or "," in core or " " in core:
                n_artifacts += 1
                if n_artifacts <= 25:
                    out.append(_v(
                        "I8", "numeric artifacts",
                        f"{r['cat']} / {r['val']}", repr(s),
                        f"{r['val']} in {r['cat']}: the {label} value "
                        f"{s!r} contains formatting characters that "
                        f"break downstream parsing.",
                    ))
        # 4dp check: a flood of exact-2dp reach values is the shipped
        # defect signature (values are jittered off 2dp boundaries
        # everywhere except demos / LOCATION / metadata).
        if (cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS
                or cu == "LOCATION"):
            continue
        bp = _num(r["bp_s"])
        if bp is None or bp in (0.0, 100.0):
            continue
        if _decimals_shown(r["bp_s"]) <= 2 and "." in str(r["bp_s"]):
            n_2dp += 1
            if len(sample_2dp) < 5:
                sample_2dp.append(f"{r['cat']}/{r['val']}={r['bp_s']}")
    if n_artifacts > 25:
        out.append(_v(
            "I8", "numeric artifacts", "(additional cells)",
            str(n_artifacts - 25),
            f"{n_artifacts - 25} additional cell(s) carry the same "
            f"formatting characters.",
        ))
    if n_2dp >= 10:
        out.append(_v(
            "I8", "numeric artifacts", "(precision)", str(n_2dp),
            f"{n_2dp} rows carry reach values rounded to 2 decimal "
            f"places (e.g. {'; '.join(sample_2dp)}); delivered values "
            f"carry 4.",
        ))
    return out


def _check_i9(rows, verbose):
    out = []
    hidden = _load_hidden_norms(verbose=verbose)
    if not hidden:
        return out
    try:
        from migration.hostmap_norm import norm_key
    except ImportError:
        try:
            from hostmap_norm import norm_key  # type: ignore
        except ImportError:
            norm_key = _norm_token
    n_flagged = 0
    for r in rows:
        cu = r["cat_u"]
        if cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS:
            continue
        if norm_key(r["val"]) in hidden:
            n_flagged += 1
            if n_flagged <= 25:
                out.append(_v(
                    "I9", "no hidden brands",
                    f"{r['cat']} / {r['val']}", r["val"],
                    f"{r['val']} in {r['cat']} is on the "
                    f"do-not-publish brand list.",
                ))
    if n_flagged > 25:
        out.append(_v(
            "I9", "no hidden brands", "(additional rows)",
            str(n_flagged - 25),
            f"{n_flagged - 25} additional row(s) are on the "
            f"do-not-publish brand list.",
        ))
    return out


def _check_i10(rows, s3_key):
    """Degenerate one-bucket demo tripwire, TU files only (2026-08-25,
    MS NOW class). A canonical demo category on a Total Universe that
    concentrates >= 99 in one bucket while another bucket sits at
    exactly 0.0000 is the signature of a cut shipped as a TU (a TU's
    demos must describe the full universe). Deliberately narrow and
    offline-deterministic: the reasoned persona-vs-demo review lives in
    migration/demo_plausibility_gate.py; this is the dumb tripwire
    underneath it. Cuts skip entirely - gender / geo cuts legitimately
    pin one bucket to ~99.99 (with the rest jittered 0.005-0.05, never
    exactly zero). The sum-to-100 half of the demo contract stays I5."""
    out = []
    if _is_cut_key(s3_key):
        return out
    by_cat = {}
    for r in rows:
        cu = r["cat_u"]
        if cu not in DEMO_CATS or cu == "LOCATION":
            continue
        bp = _num(r["bp_s"])
        if bp is None:
            continue
        by_cat.setdefault(cu, []).append((r, bp))
    for cu, pairs in by_cat.items():
        if len(pairs) < 2:
            continue
        hi = [(r, bp) for r, bp in pairs if bp >= 99.0]
        zero = [(r, bp) for r, bp in pairs if abs(bp) < 0.00005]
        if hi and zero:
            r_hi, bp_hi = hi[0]
            out.append(_v(
                "I10", "degenerate demos",
                f"{r_hi['cat']} / {r_hi['val']}", _fmt_pct(bp_hi),
                f"{r_hi['cat']} on this Total Universe file "
                f"concentrates {_fmt_pct(bp_hi)} in {r_hi['val']} while "
                f"{len(zero)} other bucket(s) sit at exactly 0.0000%. A "
                f"Total Universe must describe the full audience; a "
                f"one-bucket demographic belongs on a derived cut, not "
                f"a TU.",
            ))

    # Structural bracket-crater tripwire (2026-08-27 YMCA income
    # crater): an ORDERED demo bracket (INCOME, AGE) below 1% while
    # BOTH ordered neighbors sit above 8% is the signature of a
    # destroyed bucket (orphan-dropped label -> epsilon back-fill),
    # never a real audience shape on a TU. The repair lives in
    # post_generation_enforcers.repair_demo_bracket_craters; this is
    # the dumb backstop for writes that bypass the enforcer chain.
    # Pinned categories (any bucket >= 85) are skipped - those belong
    # to income-/age-pinned cuts, and cuts skip this check anyway.
    try:
        from migration.canonical_demos import canonical_value
        from migration.post_generation_enforcers import (
            _CRATER_ORDERED_CATS, _CRATER_BP, _CRATER_NEIGHBOR_BP,
            _CRATER_PIN_GUARD,
        )
    except ImportError:
        return out
    for cu, ordered_buckets in _CRATER_ORDERED_CATS.items():
        pairs = by_cat.get(cu) or []
        if len(pairs) < 3:
            continue
        if any(bp >= _CRATER_PIN_GUARD for _, bp in pairs):
            continue
        slot_by_bucket = {b: i for i, b in enumerate(ordered_buckets)}
        seq = [None] * len(ordered_buckets)
        for r, bp in pairs:
            canon = canonical_value(cu, r["val"])
            if isinstance(canon, str) and canon in slot_by_bucket \
                    and seq[slot_by_bucket[canon]] is None:
                seq[slot_by_bucket[canon]] = (r, bp)
        present = [p for p in seq if p is not None]
        if len(present) < 3:
            continue
        for k in range(1, len(present) - 1):
            r, bp = present[k]
            _, bp_lo = present[k - 1]
            _, bp_hi = present[k + 1]
            if (bp < _CRATER_BP and bp_lo > _CRATER_NEIGHBOR_BP
                    and bp_hi > _CRATER_NEIGHBOR_BP):
                out.append(_v(
                    "I10", "degenerate demos",
                    f"{r['cat']} / {r['val']}", _fmt_pct(bp),
                    f"{r['cat']} bracket {r['val']} sits at "
                    f"{_fmt_pct(bp)} while its ordered neighbors sit at "
                    f"{_fmt_pct(bp_lo)} and {_fmt_pct(bp_hi)}. A hollow "
                    f"interior bracket between two heavy neighbors on a "
                    f"Total Universe is a destroyed bucket, not an "
                    f"audience shape.",
                ))
    return out


def _check_i11(rows, subject, s3_key):
    """Reach above 100% is impossible, everywhere (2026-08-25, partner
    finding: Bethenny refresh shipped CPG HEINZ at 100.965% while the
    deployed hard-ceiling enforcer crash-skipped on a str-dtype frame).

    I1 only covers non-exempt categories and waves through rows whose
    Gen Pop baseline is >= 30, so a >100 row in a demo bucket, a
    companion sport category, or on a high-baseline brand could still
    ship. This invariant is the unconditional backstop: ANY row above
    100.0 blocks, with exactly two carve-outs:

      - metadata rows (META_CATS): their =100 contract is I2's job;
      - subject self-pin rows within float noise of 100 (<= 100.005):
        the pin itself is legitimate, only real overflow blocks.

    Tolerance is float-repr only (1e-6); a 4dp cell reading 100.0001
    blocks, because a client reading the file sees a number above 100.
    """
    out = []
    full, mono = _subject_forms(subject, s3_key, rows)
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["cat_u"], []).append(r)
    for cat_u, cat_rows in by_cat.items():
        if cat_u in META_CATS:
            continue
        cat_has_full_subject = any(
            _norm_token(r["val"]) in full for r in cat_rows)
        for r in cat_rows:
            bp = _num(r["bp_s"])
            if bp is None or bp <= 100.0 + 1e-6:
                continue
            vn = _norm_token(r["val"])
            is_subject = (vn in full
                          or (vn in mono and not cat_has_full_subject))
            if is_subject and bp <= 100.005:
                continue
            out.append(_v(
                "I11", "reach above 100%",
                f"{r['cat']} / {r['val']}", _fmt_pct(bp),
                f"{r['val']} shows {_fmt_pct(bp)} reach in {r['cat']}. "
                f"No row can reach more than 100% of the audience; "
                f"this value is impossible and the file cannot ship.",
            ))
    return out


def _check_i12(rows, sample, subject, s3_key, s3_client, verbose):
    """Avid subset Raw ceiling (2026-08-25 partner finding: BETHENNY
    FRANKEL - Avid Fan shipped Real Housewives of New York at 7,509
    panelists vs the parent TU's 2,236, a 3.4x impossibility, and 13
    more rows like it under I4's 10x blocking threshold).

    The avid tier is a strict subset of its parent audience: its
    penetration may exceed the parent's, its panelist COUNT may not.
    For every shared non-exempt (category, brand) row:

        round(cut_bp / 100 x cut_sample)
            <= round(parent_bp / 100 x parent_sample)

    Exempt: metadata rows, demographic categories, LOCATION, fan
    anchor rows, and subject self-pin forms (their 100-vs-100 case is
    safe by sizing, which I4 enforces). Fail-open on infrastructure:
    an unresolvable or unfetchable parent skips with a log line. The
    whole-file sizing defect (cut sample >= parent sample) stays I4's
    verdict; per-row raw comparisons are meaningless there and are
    skipped."""
    out = []
    if sample is None or not _is_avid_cut_key(s3_key):
        return out
    try:
        parent_key, parent_body = _resolve_parent_tu(
            s3_key, s3_client, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"[ship-gate] I12 parent resolution errored: {e}; "
                  f"subset raw check skipped")
        return out
    if not parent_key:
        if verbose:
            print(f"[ship-gate] I12: no parent TU resolvable for "
                  f"{_display_name(s3_key)}; subset raw check skipped")
        return out
    _, parent_rows = _parse_rows(parent_body)
    parent_sample = _extract_sample(parent_rows)
    if not parent_sample or sample >= parent_sample:
        return out

    full, mono = _subject_forms(subject, s3_key, rows)
    parent_bp = {}
    for r in parent_rows:
        cu = r["cat_u"]
        # LOCATION/DMA/REGION participate as of 2026-08-28: a cut can
        # never out-count its parent in a market either (Primetime
        # Movie avid shipped Spokane Wa 15 raw vs parent 13). Demo
        # categories stay exempt (shares renormalize to 100).
        if cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS:
            continue
        bp = _num(r["bp_s"])
        if bp is None:
            continue
        k = (cu, _norm_token(r["val"]))
        if bp > parent_bp.get(k, -1.0):
            parent_bp[k] = bp

    n_flagged = 0
    for r in rows:
        cu = r["cat_u"]
        if cu in DEMO_CATS or cu in META_CATS or cu in FAN_CATS:
            continue
        bp = _num(r["bp_s"])
        if bp is None:
            continue
        vn = _norm_token(r["val"])
        if vn in full or vn in mono:
            continue
        pbp = parent_bp.get((cu, vn))
        if pbp is None:
            continue
        cut_raw = round(sample * bp / 100.0)
        # Raw counts floor at 0: corrupt parents can carry negative BP
        # rows; the subset comparison reads those as 0 panelists so a
        # clean cut row at 0 is never blocked by a parent defect.
        parent_raw = max(0, round(parent_sample * pbp / 100.0))
        if cut_raw > parent_raw:
            n_flagged += 1
            if n_flagged <= 25:
                out.append(_v(
                    "I12", "avid subset raws",
                    f"{r['cat']} / {r['val']}",
                    f"cut {cut_raw:,} vs parent {parent_raw:,}",
                    f"{r['val']} in {r['cat']}: this avid tier counts "
                    f"{cut_raw:,} panelists ({_fmt_pct(bp)} of "
                    f"{sample:,}) but the parent file "
                    f"{_display_name(parent_key)} counts only "
                    f"{parent_raw:,} ({_fmt_pct(pbp)} of "
                    f"{parent_sample:,}). A subset can never hold "
                    f"more panelists than its parent audience.",
                ))
    if n_flagged > 25:
        out.append(_v(
            "I12", "avid subset raws", "(additional rows)",
            str(n_flagged - 25),
            f"{n_flagged - 25} additional row(s) out-count the parent "
            f"file the same way.",
        ))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _check_i13(rows, subject, s3_key, s3_client, verbose):
    """Viewer-carriage constraint (2026-08-26 Jenna, verbatim: "you can
    ony watch it online on disney+ or hulu so Disney+/Hulu should have
    been 100%"). A consumption-scoped universe's carrying platforms
    must jointly cover ~100% of the audience in the streaming
    categories.

    Applies to TU files only (cuts inherit the parent's rows). The
    carriage facts come from the build's cached research sidecar; an
    absent / failed / unconfident sidecar skips the check entirely
    (never block a build on research). Missing carrier ROWS also skip
    with a log line - row coverage is a Gen Pop question, not a
    carriage-math violation.

    Violations (all deterministically fixable by
    enforce_viewer_carriage_constraint, so profile_writer's autofix
    pass remediates before quarantine):
      * single carrier row below 99.0;
      * multi-carrier union sum below 99.0;
      * two carrier rows sharing the same 4dp value;
      * the same carrier differing across STREAMING/PLATFORM and
        STREAMING VIDEO by more than 0.02.
    """
    out = []
    if _is_cut_key(s3_key):
        return out
    doc = _load_carriage_doc(subject, s3_key, s3_client, verbose)
    if not doc:
        return out
    carriers = doc.get("carriers") or []
    per_cat_vals = {}
    for cat_u in ("STREAMING/PLATFORM", "STREAMING VIDEO"):
        cat_rows = _rows_for_cat(rows, cat_u)
        if not cat_rows:
            continue
        matched = {}
        for r in cat_rows:
            if any(_carriage_row_match(c.get("platform", ""), r["val"])
                   for c in carriers):
                key = _norm_token(r["val"])
                if key not in matched:
                    matched[key] = (r, _num(r["bp_s"]))
        if not matched:
            if verbose:
                print(f"[ship-gate] I13: no carrier row present in "
                      f"{cat_u} for {_display_name(s3_key)}; carriage "
                      f"math not checkable in this category")
            continue
        per_cat_vals[cat_u] = {k: bp for k, (r, bp) in matched.items()}
        vals = [(r, bp) for r, bp in matched.values() if bp is not None]
        if not vals:
            continue
        total = sum(bp for _, bp in vals)
        if len(vals) == 1:
            r, bp = vals[0]
            if bp < 99.0:
                out.append(_v(
                    "I13", "viewer carriage",
                    f"{r['cat']} / {r['val']}", _fmt_pct(bp),
                    f"{r['val']} is the only service carrying this "
                    f"title, so on a viewers universe its row must "
                    f"read ~100%; it shows {_fmt_pct(bp)}.",
                ))
        else:
            if total < 99.0:
                names = ", ".join(r["val"] for r, _ in vals)
                out.append(_v(
                    "I13", "viewer carriage",
                    f"{cat_u} / union({names})", _fmt_pct(total),
                    f"The services carrying this title ({names}) sum "
                    f"to {_fmt_pct(total)} in {cat_u}; on a viewers "
                    f"universe their union must cover ~100%.",
                ))
            seen4 = {}
            for r, bp in vals:
                k4 = f"{bp:.4f}"
                if k4 in seen4:
                    out.append(_v(
                        "I13", "viewer carriage",
                        f"{cat_u} / {seen4[k4]} = {r['val']}", k4,
                        f"Two carrier rows ({seen4[k4]} and {r['val']}) "
                        f"share the identical value {k4}% in {cat_u}; "
                        f"carriers must carry distinct values.",
                    ))
                else:
                    seen4[k4] = r["val"]
    # Alias consistency across the two streaming spellings.
    sp = per_cat_vals.get("STREAMING/PLATFORM") or {}
    sv = per_cat_vals.get("STREAMING VIDEO") or {}
    for k in set(sp) & set(sv):
        a, b = sp.get(k), sv.get(k)
        if a is not None and b is not None and abs(a - b) > 0.02:
            out.append(_v(
                "I13", "viewer carriage",
                f"STREAMING/PLATFORM vs STREAMING VIDEO / {k}",
                f"{_fmt_pct(a)} vs {_fmt_pct(b)}",
                f"The same carrier reads {_fmt_pct(a)} in "
                f"STREAMING/PLATFORM but {_fmt_pct(b)} in STREAMING "
                f"VIDEO; alias rows must stay consistent.",
            ))
    return out


def _check_i14(rows, s3_key):
    """Seeded fractional-part ladders (2026-08-26 Liz QA: BETHENNY
    FRANKEL - Avid Fan shipped 15 TALENT rows all ending .8912
    (67.8912 / 55.8912 / ... / 3.8912), 16 at .8234, 76 rows at .2847
    file-wide with unit-step ladders in AMUSEMENT PARKS and
    HOST/PERSONALITY). Many rows sharing one 4dp fractional part at
    integer-stepped values is a generation artifact, not data.

    Detection + thresholds live in migration/fractional_ladders.py
    (shared with the dejitter enforcer and profile_writer's autofix so
    the three can never drift): a category group fires at
    6 + cat_rows//1000 shared-suffix rows, a file-wide group at
    20 + in_scope_rows//2000 (organic max observed 3-4 / 9-10; the
    defect file hit 16 / 233).

    Deterministically fixable by dejitter_fractional_ladders
    (downward-only per-row re-salt), so profile_writer's autofix pass
    remediates before quarantine.
    """
    out = []
    try:
        from migration.fractional_ladders import (
            detect_fractional_ladders, ladder_in_scope,
        )
    except ImportError:
        from fractional_ladders import (  # type: ignore
            detect_fractional_ladders, ladder_in_scope,
        )
    triples = []
    for r in rows:
        v = _num(r["bp_s"])
        if v is None:
            continue
        if ladder_in_scope(r["cat_u"], v):
            triples.append((r["n"], r["cat_u"], v))
    if not triples:
        return out
    det = detect_fractional_ladders(triples)
    for cat_u, sfx, n, thr in det["percat_groups"][:20]:
        out.append(_v(
            "I14", "fractional-part ladder",
            f"{cat_u} / .{sfx}", f"{n} rows",
            f"{n} rows in {cat_u} share the identical 4-decimal "
            f"fractional part .{sfx} (threshold {thr}); integer-stepped "
            f"values with one shared suffix are a generation artifact.",
        ))
    for sfx, n, thr in det["filewide_groups"][:10]:
        out.append(_v(
            "I14", "fractional-part ladder",
            f"(file-wide) / .{sfx}", f"{n} rows",
            f"{n} rows across the file share the identical 4-decimal "
            f"fractional part .{sfx} (threshold {thr}); one suffix "
            f"repeated at this scale is a generation artifact.",
        ))
    return out


def _check_i15(rows, subject, s3_key):
    """TALENT umbrella self-inclusion (2026-08-26 Liz QA DEFECT 2:
    BETHENNY FRANKEL - Avid Fan carried a 490-row TALENT grid with no
    Bethenny row; she self-pinned only in HOST/PERSONALITY). A
    talent-archetype subject (BRAND CATEGORY in the TALENT family) must
    appear in the TALENT grid at exactly 100 whenever the profile
    carries one.

    Deterministically fixable by enforce_native_cluster_self_pin (the
    archetype clusters with TALENT in NATIVE_CLUSTERS), so
    profile_writer's autofix pass remediates before quarantine.
    """
    out = []
    talent_archetypes = {
        "ACTOR", "ATHLETE", "COMEDIAN", "INFLUENCER/CREATOR",
        "CREATOR/INFLUENCER", "EMERGING TALENT", "HOST/PERSONALITY",
        "MUSICIAN/BAND", "PODCASTER", "POLITICS/ACTIVIST",
        "WRITER/DIRECTOR/AUTHOR/ARTIST",
    }
    bc = ""
    for r in _rows_for_cat(rows, "BRAND CATEGORY"):
        bc = str(r["val"] or "").strip().upper()
        break
    if bc not in talent_archetypes:
        return out
    talent_rows = _rows_for_cat(rows, "TALENT")
    if not talent_rows:
        return out          # no TALENT grid in this profile: nothing to pin
    full, _mono = _subject_forms(subject, s3_key, rows)
    self_rows = [r for r in talent_rows if _norm_token(r["val"]) in full]
    if not self_rows:
        out.append(_v(
            "I15", "TALENT self-inclusion",
            f"TALENT / {subject}", "absent",
            f"The subject is a {bc} but has no row in the "
            f"{len(talent_rows)}-row TALENT grid; talent subjects must "
            f"self-include in TALENT at 100%.",
        ))
        return out
    for r in self_rows:
        bp = _num(r["bp_s"])
        if bp is None or abs(bp - 100.0) > 0.0001:
            out.append(_v(
                "I15", "TALENT self-inclusion",
                f"TALENT / {r['val']}", _fmt_pct(bp),
                f"The subject's own TALENT row reads {_fmt_pct(bp)}; a "
                f"talent subject must self-include in TALENT at exactly "
                f"100%.",
            ))
    return out


def _spc():
    """Lazy shared self-property helpers (2026-08-26 Paw Patrol). None
    tuple when the module is unavailable (checks fail-open via _safe)."""
    try:
        try:
            from migration.self_property_coherence import (
                is_subject_own, check_self_property_coherence,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                is_subject_own, check_self_property_coherence,
            )
        return is_subject_own, check_self_property_coherence
    except Exception:
        return None, None


def _check_i16(rows, subject, s3_key):
    """Self-property coherence (2026-08-26 Liz QA, Paw Patrol: base
    shipped FRANCHISE PAW PATROL 82.7367 alongside TOYS/GAMES PAW
    PATROL at 6.1959 - a preschool-parent viewer base at 83% franchise
    engagement cannot sit at 6% on the property's own toys). On a
    subject with an own-FRANCHISE anchor >= 40, every own merch/games/
    media row must read >= 14% of that anchor."""
    out = []
    is_own, check_spc = _spc()
    if check_spc is None:
        return out
    items = [(r["cat_u"], r["val"], _num(r["bp_s"])) for r in rows]
    anchor_bp, viols = check_spc(items, subject)
    for v in viols:
        out.append(_v(
            "I16", "self-property coherence",
            f"{v['cat']} / {v['val']}", _fmt_pct(v["bp"]),
            f"{v['val']} reads {_fmt_pct(v['bp'])} in {v['cat']} while "
            f"the subject's own FRANCHISE row reads "
            f"{_fmt_pct(anchor_bp)}. An audience this engaged with the "
            f"franchise cannot sit at noise level on the property's "
            f"own {v['cat'].lower()} row (floor {_fmt_pct(v['floor'])}).",
        ))
    return out


def _check_i17(rows, sample, subject, s3_key, s3_client, verbose):
    """Own-property pin (2026-08-26 Jenna convention correction,
    verbatim: "if it is its own property it should be 100%"). The
    subject's own property row (FRANCHISE 'Paw Patrol') and the
    owning / universe-defining platform row (Paramount+ on a Paw
    Patrol universe, Apple TV+ on an Apple TV+-scoped universe) read
    exactly 100.0000 in the base file AND every derived cut. This
    REPLACES the earlier avid-direction form (avid >= parent on own
    rows): a pinned row needs no direction check. Runs on every file,
    base and cuts alike; no parent resolution needed."""
    out = []
    try:
        try:
            from migration.self_property_coherence import must_pin_100
        except ImportError:
            from self_property_coherence import (  # type: ignore
                must_pin_100,
            )
    except Exception:
        return out
    for r in rows:
        cu = r["cat_u"]
        if cu in META_CATS or cu in DEMO_CATS or cu in FAN_CATS:
            continue
        bp = _num(r["bp_s"])
        if bp is None or abs(bp - 100.0) <= 0.00005:
            continue
        if must_pin_100(subject, cu, r["val"]):
            out.append(_v(
                "I17", "own-property pin",
                f"{r['cat']} / {r['val']}", _fmt_pct(bp),
                f"{r['val']} in {r['cat']} is the subject's own "
                f"property (or its owning / universe-defining "
                f"platform) and must read exactly 100.0000 in the "
                f"base file and every derived cut; it reads "
                f"{_fmt_pct(bp)}.",
            ))
    return out


def _check_i18(rows, subject, s3_key):
    """Exact-100 non-subject pin (2026-08-26 Liz QA, Paw Patrol:
    Paramount+ shipped at exactly 100.0000 in STREAMING/PLATFORM on a
    universe defined across Netflix/Amazon/Philo/Fubo).     Exact 100
    belongs to metadata rows, the subject's own self-pin family
    (companion sports pins included), and - per the 2026-08-26 Jenna
    convention correction - the owning / universe-defining platform
    row on viewer/subscriber universes (Paramount+ on Paw Patrol,
    Apple TV+ on an Apple TV+-scoped universe, the cut-defining
    platform of a platform cut, the single carrier of a one-platform
    universe); any other row at 100.0000 is an impossible
    universal-reach claim plus a messy-value violation."""
    out = []
    is_own, _ = _spc()
    try:
        try:
            from migration.self_property_coherence import (
                exact_100_exempt as _e100,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                exact_100_exempt as _e100,
            )
    except Exception:
        _e100 = None
    # Cut label (the ' - ' suffix of the deliverable name) and the
    # count of platform domains in BRAND INPUT feed the exemption.
    _base = str(s3_key or "").rsplit("/", 1)[-1]
    _base = _base[:-4] if _base.lower().endswith(".csv") else _base
    cut_label = (_base.split(" - ", 1)[1].strip()
                 if " - " in _base else None)
    carrier_domains = []
    try:
        try:
            from migration.viewer_carriage import PLATFORM_DOMAINS
        except ImportError:
            from viewer_carriage import PLATFORM_DOMAINS  # type: ignore
        for r in rows:
            if r["cat_u"] == "BRAND INPUT":
                bi = str(r["val"] or "").lower()
                carrier_domains = [d for d in PLATFORM_DOMAINS if d in bi]
                break
    except Exception:
        carrier_domains = []
    full, mono = _subject_forms(subject, s3_key, rows)
    for r in rows:
        cu = r["cat_u"]
        if (cu in META_CATS or cu in DEMO_CATS or cu in FAN_CATS
                or cu in COMPANION_SPORT_CATS
                or _COMPANION_DIVISION_RE.search(cu)):
            continue
        # Demo-shaped bucket categories outside the canonical 9: a
        # 100.0000 bucket there is a renormalization defect for the
        # demo tooling, not an I18 reach pin (corpus scan 2026-08-26
        # surfaced AGE_OF_CHILDREN / No Kids across content files).
        if cu in {"AGE_OF_CHILDREN", "AGE OF CHILDREN"}:
            continue
        bp = _num(r["bp_s"])
        if bp is None or abs(bp - 100.0) > 0.00005:
            continue
        vn = _norm_token(r["val"])
        if vn in full or vn in mono:
            continue
        if is_own is not None and is_own(subject, r["val"]):
            continue
        if _e100 is not None and _e100(subject, cu, r["val"],
                                       cut_label=cut_label,
                                       carrier_domains=carrier_domains):
            continue
        out.append(_v(
            "I18", "exact-100 non-subject pin",
            f"{r['cat']} / {r['val']}", "100.0000",
            f"{r['val']} in {r['cat']} reads exactly 100.0000% but is "
            f"not the subject's own row. Universal reach on a "
            f"non-subject row is not a plausible measured value.",
        ))
    return out


_I19_URLISH_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/|$)")


def _check_i19(rows, subject, s3_key):
    """BRAND INPUT landing page (2026-08-26 Liz QA, Paw Patrol:
    fubo.tv/welcome shipped as a clickstream slug - every Fubo visitor
    qualifies into the universe, violating rule 4c-i case 4). NOT
    auto-fixable: replacing the slug requires researching the specific
    title URL, so violations quarantine with a clear reason."""
    out = []
    try:
        try:
            from migration.viewer_carriage import is_generic_landing_url
        except ImportError:
            from viewer_carriage import (  # type: ignore
                is_generic_landing_url,
            )
    except Exception:
        return out
    # Subject-owns-platform exemption (2026-08-27 Liz batch, Netflix
    # SVOD/AVOD Subscribers): on a PLATFORM-scoped universe (the subject
    # IS that platform's subscriber/member/user base), the platform's
    # own domain slugs are the correct clickstream identification -
    # "every visitor qualifies" is the definition, not the defect. A
    # TITLE universe on a carrier stays flagged (Paw Patrol viewers vs
    # fubo.tv/welcome: 'fubo' is not in the subject).
    subj_norm = "".join(ch for ch in str(subject or "").lower()
                        if ch.isalnum())

    def _subject_owns(token):
        dom = str(token).lower().strip()
        for pre in ("https://", "http://", "www."):
            if dom.startswith(pre):
                dom = dom[len(pre):]
        dom = dom.split("/")[0].split(".")[0]
        dom = "".join(ch for ch in dom if ch.isalnum())
        return len(dom) >= 4 and dom in subj_norm

    for r in rows:
        if r["cat_u"] != "BRAND INPUT":
            continue
        toks = [t.strip() for t in str(r["val"] or "").split(",")
                if t.strip()]
        urlish = [t for t in toks
                  if "/" in t or _I19_URLISH_RE.match(t)]
        if not urlish:
            continue
        for t in urlish:
            if _subject_owns(t):
                continue
            # Tokens WITH a path are real URLs: the generic check
            # applies to any domain. Dotted no-path tokens are usually
            # brand-name variants (PAW.Patrol, Samsung.Tv - rule 4c-i
            # case 2), so those only flag on a known platform domain
            # (netflix.com bare IS the defect; a variant is not).
            if is_generic_landing_url(
                    t, require_platform_domain=("/" not in t)):
                out.append(_v(
                    "I19", "BRAND INPUT landing page", "BRAND INPUT",
                    t,
                    f"BRAND INPUT carries {t!r}, a generic platform "
                    f"landing page: every visitor of that platform "
                    f"would qualify into this universe. The slug must "
                    f"be the specific title path on that platform (or "
                    f"the platform dropped if no title page exists); "
                    f"this needs research judgment, not an automatic "
                    f"rewrite.",
                ))
    return out


def _check_i20(rows, s3_key):
    """Top-cluster convergence (2026-08-27 Liz batch: YMCA base shipped
    Netflix 39.5742 / Prime 39.4742 / Disney+/Hulu 39.4612 and Toca
    base had FOUR platforms within 0.11pp; Prime sat at Netflix minus
    exactly 0.1000 - the retired fixed peer-cap increment). Independent
    brands do not tie: 3+ category leaders inside ~0.15pp at meaningful
    levels is a mechanical signature.

    Thresholds live in migration/post_generation_enforcers
    (CONVERGENCE_*), shared with the enforcer-chain re-spread and the
    vetting prescan so the three can never drift. Deterministically
    fixable by respread_top_cluster_convergence (salted downward
    descent, order preserved), so profile_writer's autofix pass
    remediates before quarantine.
    """
    out = []
    try:
        try:
            from migration.post_generation_enforcers import (
                CONVERGENCE_MIN_CLUSTER, CONVERGENCE_EPS_PP,
                CONVERGENCE_MIN_LEVEL, _CONVERGENCE_EXEMPT_CATS,
            )
        except ImportError:
            from post_generation_enforcers import (  # type: ignore
                CONVERGENCE_MIN_CLUSTER, CONVERGENCE_EPS_PP,
                CONVERGENCE_MIN_LEVEL, _CONVERGENCE_EXEMPT_CATS,
            )
    except Exception:
        return out
    by_cat = {}
    for r in rows:
        cat = r["cat_u"]
        if not cat or cat in _CONVERGENCE_EXEMPT_CATS:
            continue
        v = _num(r["bp_s"])
        if v is None or v >= 95.0 or v <= 0.0001:
            continue
        by_cat.setdefault(cat, []).append((str(r["val"] or "").strip(), v))
    for cat, vals in by_cat.items():
        if len(vals) < CONVERGENCE_MIN_CLUSTER:
            continue
        vals.sort(key=lambda t: -t[1])
        top = vals[0][1]
        if top < CONVERGENCE_MIN_LEVEL:
            continue
        k = 1
        while k < len(vals) and (top - vals[k][1]) <= CONVERGENCE_EPS_PP:
            k += 1
        if k >= CONVERGENCE_MIN_CLUSTER:
            cluster_s = ", ".join(f"{b} {v:.4f}" for b, v in vals[:k][:6])
            out.append(_v(
                "I20", "top-cluster convergence",
                f"{cat} (top {k})", f"{top - vals[k - 1][1]:.4f}pp spread",
                f"The top {k} rows in {cat} sit within "
                f"{CONVERGENCE_EPS_PP}pp of each other ({cluster_s}); "
                f"independent brands do not tie, so a converged leader "
                f"cluster is a mechanical artifact.",
            ))
    return out


def check_final_ship_invariants(df_or_bytes, s3_key, subject, *,
                                s3_client=None, verbose=True):
    """Run all invariants read-only. Returns (violations, meta).

    Never raises on violations; internal errors in a single invariant
    degrade to a loud log + skip so an unrelated infrastructure hiccup
    cannot wedge every upload (violations that ARE detected still
    block via run_final_ship_gate)."""
    skip = _skip_gate_reason(s3_key)
    if skip:
        if verbose:
            print(f"[ship-gate] skipped for {s3_key!r}: {skip}")
        return [], {"skipped": skip}

    data = _to_bytes(df_or_bytes)
    colmap, rows = _parse_rows(data)
    if colmap is None or not rows:
        return [_v("I2", "metadata completeness", "(file)", "empty",
                   "The file parsed to zero data rows.")], {}
    missing_cols = [k for k in ("cat", "val", "bp") if colmap.get(k) is None]
    if missing_cols:
        return [_v("I2", "metadata completeness", "(file)",
                   ",".join(missing_cols),
                   "The file is missing one of its core columns "
                   "(Column / Value / Brand Penetration).")], {}

    violations = []

    def _safe(label, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"[ship-gate] {label} internal error "
                  f"({type(e).__name__}: {e}); check skipped")
            return []

    i2_out = _safe("I2", _check_i2, rows, subject, verbose)
    if isinstance(i2_out, tuple):
        i2_violations, sample = i2_out
    else:
        i2_violations, sample = i2_out, _extract_sample(rows)
    violations += _safe("I1", _check_i1, rows, subject, s3_key,
                        s3_client, verbose)
    violations += i2_violations
    violations += _safe("I3", _check_i3, rows, s3_key)
    violations += _safe("I4", _check_i4, rows, sample, subject, s3_key,
                        s3_client, verbose)
    violations += _safe("I5", _check_i5, rows)
    violations += _safe("I6", _check_i6, rows, sample)
    violations += _safe("I7", _check_i7, rows)
    violations += _safe("I8", _check_i8, rows)
    violations += _safe("I9", _check_i9, rows, verbose)
    violations += _safe("I10", _check_i10, rows, s3_key)
    violations += _safe("I11", _check_i11, rows, subject, s3_key)
    violations += _safe("I12", _check_i12, rows, sample, subject, s3_key,
                        s3_client, verbose)
    violations += _safe("I13", _check_i13, rows, subject, s3_key,
                        s3_client, verbose)
    violations += _safe("I14", _check_i14, rows, s3_key)
    violations += _safe("I15", _check_i15, rows, subject, s3_key)
    violations += _safe("I16", _check_i16, rows, subject, s3_key)
    violations += _safe("I17", _check_i17, rows, sample, subject, s3_key,
                        s3_client, verbose)
    violations += _safe("I18", _check_i18, rows, subject, s3_key)
    violations += _safe("I19", _check_i19, rows, subject, s3_key)
    violations += _safe("I20", _check_i20, rows, s3_key)

    meta = {"n_rows": len(rows), "sample": sample,
            "is_cut": _is_cut_key(s3_key)}
    return violations, meta


def _quarantine_rejected(data, s3_key, s3_client, verbose):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(str(s3_key or "profile.csv"))
    if base.lower().endswith(".csv"):
        base = base[:-4]
    qkey = f"{QUARANTINE_PREFIX}{base}.rejected_{ts}.csv"
    try:
        _s3(s3_client).put_object(
            Bucket=BUCKET, Key=qkey, Body=data, ContentType="text/csv",
        )
        if verbose:
            print(f"[ship-gate] rejected copy saved to "
                  f"s3://{BUCKET}/{qkey}")
        return qkey
    except Exception as e:
        print(f"[ship-gate] quarantine write failed for {s3_key}: {e}")
        return None


def _email_hold_notice(s3_key, violations, quarantine_key, verbose):
    name = _display_name(s3_key)
    lines = [
        f"The file {name} was held before delivery because "
        f"{len(violations)} final quality check(s) did not pass.",
        "",
        "It was NOT published to the dashboard.",
    ]
    if quarantine_key:
        lines.append(f"A copy is saved for review at {quarantine_key}.")
    lines.append("")
    lines.append("What was found:")
    for i, v in enumerate(violations[:40], start=1):
        lines.append(f"  {i}. {v.get('plain') or v.get('where')}")
    if len(violations) > 40:
        lines.append(f"  ... and {len(violations) - 40} more.")
    lines += [
        "",
        "Next step: fix the source and rerun the build. The file will "
        "publish automatically once every check passes.",
    ]
    payload = {
        "subject_line": f"Profile held before delivery: {name}",
        "body": "\n".join(lines),
        "to": list(HOLD_NOTICE_TO),
        "source": HOLD_NOTICE_FROM,
    }
    # Debounced delivery (Jenna 2026-08-27: "yes I only want real
    # emails not gate blocks, just if the final cannnot ship"). The
    # notice is recorded as pending; a gate-green republish of the same
    # deliverable cancels it silently, and it only emails if the hold
    # outlives the debounce window or the run turns terminal. On any
    # recording failure the notice sends immediately (fail-safe).
    try:
        try:
            from migration.hold_notice_debounce import record_pending
        except ImportError:
            from hold_notice_debounce import record_pending  # type: ignore
        disposition = record_pending(
            s3_key, "ship_gate", payload,
            quarantine_key=quarantine_key, n_findings=len(violations),
            verbose=verbose,
        )
        if verbose:
            print(f"[ship-gate] hold notice {disposition} "
                  f"(debounced delivery)")
        return
    except Exception as e:
        print(f"[ship-gate] hold-notice debounce unavailable "
              f"({type(e).__name__}: {e}); sending immediately")
    try:
        import boto3
        ses = boto3.client("ses", region_name="us-east-2")
        ses.send_email(
            Source=HOLD_NOTICE_FROM,
            Destination={"ToAddresses": HOLD_NOTICE_TO},
            Message={
                "Subject": {"Data": payload["subject_line"]},
                "Body": {"Text": {"Data": payload["body"]}},
            },
        )
        if verbose:
            print(f"[ship-gate] hold notice emailed to "
                  f"{', '.join(HOLD_NOTICE_TO)}")
    except Exception as e:
        print(f"[ship-gate] hold notice email failed: {e}")


def run_final_ship_gate(df_or_bytes, s3_key, subject, *, enforce=True,
                        s3_client=None, quarantine=True, send_email=True,
                        verbose=True):
    """Terminal gate. Returns (ok, violations).

    enforce=True (the default on EVERY external path): violations
    quarantine the rejected bytes, record a debounced hold notice
    (migration/hold_notice_debounce: emails only if the hold outlives
    the window or the run turns terminal; a gate-green republish
    cancels it silently), and raise ShipGateError so the caller cannot
    upload. There is deliberately
    no environment-variable downgrade; enforce=False exists only for
    the local ops override (migration/local_override_profile.py) and
    read-only audits, and must be passed explicitly by the caller.
    """
    data = _to_bytes(df_or_bytes)
    violations, meta = check_final_ship_invariants(
        data, s3_key, subject, s3_client=s3_client, verbose=verbose,
    )
    if meta.get("skipped"):
        return True, []
    if not violations:
        if verbose:
            print(f"[ship-gate] PASS {_display_name(s3_key)} "
                  f"({meta.get('n_rows', 0):,} rows, "
                  f"sample={meta.get('sample')})")
        return True, []

    print(f"[ship-gate] {'BLOCKED' if enforce else 'violations (report-only)'}"
          f" {_display_name(s3_key)}: {len(violations)} violation(s)")
    for v in violations[:15]:
        print(f"[ship-gate]   {v['code']} {v['where']}: {v['value']}")
    if len(violations) > 15:
        print(f"[ship-gate]   ... and {len(violations) - 15} more")

    if not enforce:
        return False, violations

    qkey = None
    if quarantine:
        qkey = _quarantine_rejected(data, s3_key, s3_client, verbose)
    if send_email:
        _email_hold_notice(s3_key, violations, qkey, verbose)
    raise ShipGateError(s3_key, violations, quarantine_key=qkey)


if __name__ == "__main__":
    # Read-only audit CLI: python3 -m migration.final_ship_gate KEY ...
    import sys as _sys
    keys = [a for a in _sys.argv[1:] if not a.startswith("-")]
    if not keys:
        print("usage: final_ship_gate.py <s3_key> [<s3_key> ...]")
        raise SystemExit(2)
    s3c = _s3(None)
    n_bad = 0
    for key in keys:
        body = s3c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        subj = _display_name(key).split(" - ")[0]
        subj = re.sub(r"[_\s]\d{2}[_\s]\d{2}[_\s]\d{4}.*$", "",
                      subj).replace("_", " ").strip()
        ok, viols = run_final_ship_gate(
            body, key, subj, enforce=False, s3_client=s3c, verbose=True,
        )
        if not ok:
            n_bad += 1
            for v in viols:
                print(f"    {v['code']} | {v['where']} | {v['value']}")
    print(f"[ship-gate] audit done: {len(keys) - n_bad}/{len(keys)} clean")
    raise SystemExit(1 if n_bad else 0)
