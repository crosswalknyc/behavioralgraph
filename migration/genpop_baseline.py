"""Gen Pop baseline columns for profile CSVs (Jenna 2026-08-22).

Mandate: "have it add the genpop value and index against genpop to each
csv output so that the raw file has it."

Every profile CSV gains two terminal columns at write time:

  * ``Gen Pop Penetration``: the Gen Pop baseline penetration for the
    row's (category, brand), copied verbatim from the current
    s3://dashboard-inputs/Gen_Pop_2026.csv cell.
  * ``Index vs Gen Pop``: profile BP / Gen Pop BP * 100, one decimal,
    no percent sign. 100 = gen pop.

Rows with no Gen Pop match (metadata rows like BRAND INPUT / SAMPLE
SIZE / BRAND CATEGORY / SUBJECT, and unmatched brands) keep BOTH cells
blank. Never 0, never a fabricated baseline.

The columns are appended AFTER every enforcer, safety net, polish pass,
and gate has run, immediately before serialization, so no enforcer or
dataframe transform ever sees the extra columns mid-flight.
``strip_genpop_columns`` removes them from any INPUT df (re-writes of
already-retrofitted files, cut synthesis reading a parent that has
them) for the same reason.

Matching semantics mirror bg-webapp/prometheus_analysis.py
``load_genpop_map``: category normalized with whitespace/underscore
collapse + uppercase, brand lowercased with all non-alphanumerics
stripped. Do not invent new matching; keep these twins in sync.

This module lives in both repos (migration/genpop_baseline.py in the
parent and bg-webapp/migration/genpop_baseline.py in the submodule) so
BG.py, the queue worker, the cut synthesizers, and the webapp twin all
import the same implementation. Edit both copies together.
"""
from __future__ import annotations

import io
import re
import threading
import time
import unicodedata

import pandas as pd

BUCKET = "dashboard-inputs"
GENPOP_KEY = "Gen_Pop_2026.csv"
GENPOP_TTL_S = 3600

GENPOP_PEN_COL = "Gen Pop Penetration"
GENPOP_IDX_COL = "Index vs Gen Pop"
GENPOP_COLS = (GENPOP_PEN_COL, GENPOP_IDX_COL)

CAT_COL = "Column"
VAL_COL = "Value"
BP_COL = "Brand Penetration (Row)"

METADATA_COLS = {
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT",
    "INPUT_METADATA", "INPUT METADATA",
}

_cache = {"ts": 0.0, "map": None}
_lock = threading.Lock()


# Canonical alias pairs that name the SAME grid with a different
# spelling (both live in MASTER_CATEGORIES as aliases). Folded so a
# profile written with one spelling still matches Gen Pop rows stored
# under the other (2026-08-26 Bethenny avid: 7 INFLUENCER/CREATOR rows
# missed Gen Pop's CREATOR/INFLUENCER entries and shipped index-less).
_CAT_ALIASES = {
    "INFLUENCER/CREATOR": "CREATOR/INFLUENCER",
    "VIRTUAL MVPD/FAST": "VIRTUAL MVPD FAST",
    "VMVPD/FAST": "VIRTUAL MVPD FAST",
    # 2026-08-28 (partner findings): profiles ship these spellings but
    # Gen Pop stores the canonical right-hand names; without the fold
    # whole columns ship index-less (Holley STREAMING MUSIC x14, AMASS
    # APP/PLATFORM x7).
    "STREAMING MUSIC": "STREAMING/MUSIC",
    "APP/PLATFORM": "APP/PLATFORM USAGE",
}

# Mirror-group fallbacks (2026-08-28, partner findings 6.1/6.2): when a
# profile row's own category has no Gen Pop entry for the brand, fall
# back to the brand's row in a mirror-coherent companion category. Per
# rule #3b the purchase-family mirror keeps those values identical, so
# the fallback IS the brand's Gen Pop reach, not a proxy. This is how
# parts retailers ranked in an AUTOMOBILE ladder (AutoZone, NAPA,
# Summit Racing - hostmap section 'Where They Shop') and beverage
# challengers in a BEVERAGE ladder pick up denominators. Ordered by
# preference; first hit wins.
_CAT_FALLBACKS = {
    "BEVERAGE": ("CPG", "MOST PURCHASED BRANDS"),
    "AUTOMOBILE": ("WHERE THEY SHOP",),
    "CASUAL DINING": ("QSR", "WHERE THEY DINE"),
}


def _norm_cat(c):
    n = re.sub(r"[_\s]+", " ", str(c or "").strip().upper())
    return _CAT_ALIASES.get(n, n)


def _norm_brand(b):
    # Accent fold first (2026-08-27): 'Timothée' must match 'Timothee'
    # instead of silently dropping the accented letter. Mirrors
    # migration/hostmap_norm.norm_key.
    s = unicodedata.normalize("NFKD", str(b or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _parse_bp(v):
    try:
        f = float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN cell read back as float nan or the string 'nan'
        return None
    return f


def load_genpop_map(s3_client=None, *, force=False):
    """(cat_norm, brand_norm) -> (bp_float, verbatim_cell_str).

    Reads the CURRENT Gen Pop file from S3 once and caches it in-process
    for an hour, so a batch run loads it once per run, not per row or
    per file.
    """
    with _lock:
        if (not force and _cache["map"] is not None
                and time.time() - _cache["ts"] < GENPOP_TTL_S):
            return _cache["map"]
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3", region_name="us-east-2")
    obj = s3_client.get_object(Bucket=BUCKET, Key=GENPOP_KEY)
    df = pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=str,
                     keep_default_na=False)
    gp = {}
    for _, row in df.iterrows():
        cat = _norm_cat(row.get(CAT_COL))
        if not cat or cat in METADATA_COLS:
            continue
        raw = str(row.get(BP_COL) or "").strip()
        v = _parse_bp(raw)
        if v is None:
            continue
        gp[(cat, _norm_brand(row.get(VAL_COL)))] = (v, raw)
    with _lock:
        _cache["map"] = gp
        _cache["ts"] = time.time()
    return gp


def strip_genpop_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the two baseline columns if present. Call this wherever a
    profile CSV is read back as INPUT (cut synthesis parents, re-writes)
    so no downstream transform sees unexpected columns."""
    drop = [c for c in df.columns if str(c).strip() in GENPOP_COLS]
    return df.drop(columns=drop) if drop else df


def append_genpop_columns(df: pd.DataFrame, genpop_map=None,
                          s3_client=None, verbose=True) -> pd.DataFrame:
    """Append the two baseline columns as the LAST columns of df.

    Never raises: on any failure the df comes back unchanged (minus any
    pre-existing copies of the two columns). Existing cell values are
    untouched; this only adds columns.
    """
    try:
        df = strip_genpop_columns(df)
        if genpop_map is None:
            genpop_map = load_genpop_map(s3_client)
        if not genpop_map:
            if verbose:
                print("  [genpop_baseline] empty Gen Pop map; "
                      "baseline columns skipped")
            return df
        pens, idxs = [], []
        for _, row in df.iterrows():
            cat = _norm_cat(row.get(CAT_COL))
            if not cat or cat in METADATA_COLS:
                pens.append("")
                idxs.append("")
                continue
            bn = _norm_brand(row.get(VAL_COL))
            hit = genpop_map.get((cat, bn))
            if hit is None:
                for fb_cat in _CAT_FALLBACKS.get(cat, ()):
                    hit = genpop_map.get((_norm_cat(fb_cat), bn))
                    if hit is not None:
                        break
            if hit is None:
                pens.append("")
                idxs.append("")
                continue
            gp_v, gp_raw = hit
            pens.append(gp_raw)
            bp = _parse_bp(row.get(BP_COL))
            if bp is not None and gp_v and gp_v > 0:
                idxs.append(f"{(bp / gp_v * 100.0):.1f}")
            else:
                idxs.append("")
        df = df.copy()
        df[GENPOP_PEN_COL] = pens
        df[GENPOP_IDX_COL] = idxs
        if verbose:
            n = sum(1 for p in pens if p != "")
            print(f"  [genpop_baseline] appended Gen Pop baseline: "
                  f"{n}/{len(pens)} rows matched")
        return df
    except Exception as e:
        print(f"  [genpop_baseline] append failed (non-fatal, "
              f"shipping without baseline columns): {e}")
        return df


__all__ = [
    "GENPOP_PEN_COL", "GENPOP_IDX_COL", "GENPOP_COLS",
    "load_genpop_map", "strip_genpop_columns", "append_genpop_columns",
]
