"""Cross-file constant detection (2026-08-27, Liz batch escalation).

The defect class: a single brand lands inside a tight index window on
MANY same-day files whose audiences have nothing in common. Liz's
concrete instance was Visa at index 68.1-70.2 on all seven of the
day's avid files (YMCA members, Netflix subscribers, Dominican reality
viewers, preschool-app buyers, Golden Girls fans) while the same
brand's base-file indexes ranged 69-105. Independent audiences cannot
agree to a within-2-point window by behavior; a constant like that is
mechanical (shared fallback target, un-salted benchmark fix, or a
formatter) and must be caught BEFORE ship, not by the human vetter.

Mechanism, two halves:

1. ``record_ship(df, subject, s3_key)`` - called after a profile CSV
   ships to the dashboard root. Extracts the watchlist brands' BPs and
   writes ONE small JSON object per shipped file under
   ``system/ship_brand_ledger/YYYYMMDD/<basename>.json``. One object
   per file means concurrent workers never contend on a shared blob
   (no read-modify-write race). Fail-safe: never raises.

2. ``scan_frame_for_constants(df, subject, s3_key, cols, genpop_map)``
   - called from the pre-ship vetting prescan on the INCOMING frame.
   Loads the trailing window of ledger objects (today + yesterday,
   UTC), and for each watchlist brand present in the incoming frame
   checks how many distinct-subject files already shipped the same
   brand inside a tight window around the incoming value (index points
   when a Gen Pop baseline exists for both sides, raw BP otherwise).
   >= MIN_PEERS distinct subjects inside the window = a nomination the
   vetting reasoner must address (unaddressed nominations downgrade a
   PASS to BORDERLINE per the prescan contract).

The watchlist is the set of ubiquitous brands where every audience
plausibly carries a row, so cross-file agreement is measurable and a
constant is meaningful. Long-tail brands (absent from most files)
can't form the pattern and would only add noise.
"""

import datetime as _dt
import json
import os
import re

BUCKET = "dashboard-inputs"
LEDGER_PREFIX = "system/ship_brand_ledger/"

# Tight-window thresholds. Liz's Visa spread was 2.1 index points
# across seven files; organic same-brand spread across unrelated
# audiences runs tens of points (her base files: 69-105).
INDEX_EPS = 2.5       # index points, when both sides have a baseline
BP_EPS = 0.60         # raw pp fallback when a baseline is missing
MIN_PEERS = 3         # distinct prior subjects inside the window
WINDOW_DAYS = 2       # today + yesterday (UTC)
MAX_FLAGS = 8
MAX_LEDGER_OBJECTS = 400   # hard cap on objects read per scan

# Ubiquitous brands: present on effectively every profile, so a
# cross-file constant is detectable and meaningful.
WATCHLIST = (
    "VISA", "MASTERCARD", "AMERICAN EXPRESS", "DISCOVER", "CAPITAL ONE",
    "CHASE", "PAYPAL",
    "GOOGLE", "YOUTUBE", "FACEBOOK", "INSTAGRAM", "TIKTOK",
    "NETFLIX", "AMAZON PRIME VIDEO", "DISNEY+/HULU", "MAX", "PEACOCK",
    "AMAZON", "WALMART", "TARGET",
    "MCDONALDS", "STARBUCKS",
    "SPOTIFY", "APPLE MUSIC",
)


def _norm(s):
    return re.sub(r"[^A-Z0-9]+", "", str(s or "").upper())


_WATCH_NORM = {_norm(b): b for b in WATCHLIST}

# Categories whose rows are never brand penetrations.
_SKIP_CATS = {
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "PARENTAL_STATUS", "PARENTAL STATUS", "RELATIONSHIP",
    "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION", "LOCATION",
    "AGE_OF_CHILDREN", "AGE OF CHILDREN",
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT",
    "INPUT_METADATA", "INPUT METADATA", "AVID FAN", "CASUAL FAN",
}


def _s3(s3_client=None):
    if s3_client is not None:
        return s3_client
    import boto3
    return boto3.client("s3", region_name="us-east-2")


def _to_float(v):
    try:
        f = float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _detect_cols(df):
    low = {str(c).strip().lower(): c for c in df.columns}
    cat = low.get("column")
    val = low.get("value")
    bp = None
    for lc, c in low.items():
        if "brand penetration" in lc:
            bp = c
            break
    return cat, val, bp


def _extract_watchlist(df, cat_col, val_col, bp_col):
    """{brand_display: {"category": str, "bp": float}} - first hit per
    brand outside the demo/meta grids (mirror rule 3b makes duplicates
    equal-valued, so first hit is representative)."""
    out = {}
    for _, row in df.iterrows():
        cat = str(row.get(cat_col) or "").strip()
        if not cat or cat.upper() in _SKIP_CATS:
            continue
        bn = _norm(row.get(val_col))
        disp = _WATCH_NORM.get(bn)
        if disp is None or disp in out:
            continue
        bp = _to_float(row.get(bp_col))
        if bp is None or bp <= 0 or bp >= 99.5:
            continue  # self-pins / empty cells carry no signal
        out[disp] = {"category": cat, "bp": round(bp, 4)}
        if len(out) == len(WATCHLIST):
            break
    return out


def _ledger_key(s3_key, when=None):
    day = (when or _dt.datetime.utcnow()).strftime("%Y%m%d")
    base = os.path.basename(str(s3_key or "").strip()) or "unknown.csv"
    return f"{LEDGER_PREFIX}{day}/{base}.json"


def record_ship(df, subject, s3_key, s3_client=None):
    """Write this file's watchlist brand values to the ship ledger.

    One small object per shipped file; concurrent-safe by construction.
    Never raises (ledger writes must not block a ship).
    """
    try:
        key = str(s3_key or "")
        base = os.path.basename(key)
        if (not base.lower().endswith(".csv") or "/" in key.strip("/")
                and not key.startswith(base)):
            # Only dashboard-root profile CSVs belong in the ledger
            # (skip _backups/, system/, Gen Pop, quarantine copies).
            if key != base:
                return False
        if base.lower().startswith("gen_pop"):
            return False
        cat_col, val_col, bp_col = _detect_cols(df)
        if not (cat_col and val_col and bp_col):
            return False
        brands = _extract_watchlist(df, cat_col, val_col, bp_col)
        if not brands:
            return False
        body = json.dumps({
            "subject": str(subject or ""),
            "s3_key": base,
            "ts": _dt.datetime.utcnow().isoformat() + "Z",
            "brands": brands,
        }, ensure_ascii=True).encode("utf-8")
        _s3(s3_client).put_object(
            Bucket=BUCKET, Key=_ledger_key(base),
            Body=body, ContentType="application/json")
        return True
    except Exception:
        return False


def _load_window(s3, self_base, subject_norm):
    """All ledger entries in the trailing window, excluding this file
    itself and any file for the SAME subject (a subject's own TU/avid
    pair agreeing with itself is expected, not a constant)."""
    entries = []
    now = _dt.datetime.utcnow()
    seen = 0
    for d in range(WINDOW_DAYS):
        day = (now - _dt.timedelta(days=d)).strftime("%Y%m%d")
        prefix = f"{LEDGER_PREFIX}{day}/"
        try:
            paginator = s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=BUCKET, Prefix=prefix)
            keys = [o["Key"] for page in pages
                    for o in page.get("Contents", [])]
        except Exception:
            continue
        for k in keys:
            if seen >= MAX_LEDGER_OBJECTS:
                return entries
            seen += 1
            if os.path.basename(k) == f"{self_base}.json":
                continue
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=k)
                rec = json.loads(obj["Body"].read())
            except Exception:
                continue
            subj = str(rec.get("subject") or "")
            # ' - ' cut suffix folds into the parent subject so a
            # TU + its own cuts never count as independent peers.
            root = _norm(subj.split(" - ")[0])
            if not root or root == subject_norm:
                continue
            brands = rec.get("brands")
            if isinstance(brands, dict) and brands:
                entries.append((subj, root, brands))
    return entries


def scan_frame_for_constants(df, subject, s3_key, cols=None,
                             genpop_map=None, s3_client=None):
    """Nominations for the vetting prescan: watchlist brands whose
    incoming value sits inside a tight window that >= MIN_PEERS other
    distinct-subject files already shipped in the trailing window.

    Returns a list of dicts (possibly empty). Never raises.
    """
    try:
        if cols is not None:
            cat_col = cols.get("cat")
            val_col = cols.get("val")
            bp_col = cols.get("bp")
        else:
            cat_col = val_col = bp_col = None
        if not (cat_col and val_col and bp_col):
            cat_col, val_col, bp_col = _detect_cols(df)
        if not (cat_col and val_col and bp_col):
            return []
        incoming = _extract_watchlist(df, cat_col, val_col, bp_col)
        if not incoming:
            return []
        s3 = _s3(s3_client)
        self_base = os.path.basename(str(s3_key or "").strip())
        subject_norm = _norm(str(subject or "").split(" - ")[0])
        window = _load_window(s3, self_base, subject_norm)
        if len(window) < MIN_PEERS:
            return []

        def _index_of(brand_disp, cat, bp):
            if genpop_map is None:
                return None
            try:
                try:
                    from migration.genpop_baseline import (
                        _norm_brand, _norm_cat)
                except ImportError:
                    from genpop_baseline import (  # type: ignore
                        _norm_brand, _norm_cat)
                hit = genpop_map.get(
                    (_norm_cat(cat), _norm_brand(brand_disp)))
                if hit and hit[0]:
                    return bp / hit[0] * 100.0
            except Exception:
                return None
            return None

        flags = []
        for disp, info in incoming.items():
            bp_in = info["bp"]
            idx_in = _index_of(disp, info["category"], bp_in)
            peers = {}
            for subj, root, brands in window:
                b = brands.get(disp)
                if not isinstance(b, dict):
                    continue
                bp_p = _to_float(b.get("bp"))
                if bp_p is None:
                    continue
                if idx_in is not None:
                    idx_p = _index_of(disp, b.get("category")
                                      or info["category"], bp_p)
                    close = (idx_p is not None
                             and abs(idx_p - idx_in) <= INDEX_EPS)
                else:
                    close = abs(bp_p - bp_in) <= BP_EPS
                if close and root not in peers:
                    peers[root] = {"subject": subj, "bp": round(bp_p, 4)}
            if len(peers) >= MIN_PEERS:
                flags.append({
                    "brand": disp,
                    "category": info["category"],
                    "bp": bp_in,
                    "index": round(idx_in, 1) if idx_in is not None
                    else None,
                    "n_peer_files": len(peers),
                    "peer_examples": list(peers.values())[:4],
                    "window_days": WINDOW_DAYS,
                })
            if len(flags) >= MAX_FLAGS:
                break
        return flags
    except Exception:
        return []
