#!/usr/bin/env python3
"""Demographic plausibility gate - persona-reasoned review of the
sum-to-100 demo categories on every fresh Total Universe build.

Why this module exists (Jenna directive 2026-08-25, MS NOW incident):
the MS NOW Total Universe shipped with an INCOME distribution at or
below gen pop for an audience that is publicly documented (Pew,
MRI-Simmons) as older, heavily college-educated, and indexing well
above gen pop on $75K+ / $100K+ household income. Every deterministic
check passed - the buckets summed to 100, the chain math held - but
nothing in the pipeline asked "does this SHAPE make sense for THIS
audience?". Jenna caught it by eye on the dashboard. This gate makes
the pipeline smart enough to catch that class of error itself.

What it does
------------
ONE Claude call per fresh TU build reviews the nine sum-to-100
demographic categories (GENDER, AGE, ETHNICITY, EDUCATION, INCOME,
OCCUPATION, PARENTAL_STATUS, RELATIONSHIP, SEXUAL_ORIENTATION -
canonical labels in migration/canonical_demos.PIPELINE_DEMO_SCHEMA)
against the subject's persona AND the Gen Pop distribution for the
same buckets (loaded from s3://dashboard-inputs/Gen_Pop_2026.csv so
the model can reason about indexes vs gen pop). Per category the
model returns:

  * verdict: plausible | implausible
  * one-line reason
  * for implausible categories, a corrected bucket distribution
    grounded in what is publicly known about the audience (cable news
    = older + affluent + educated; a kids' toy brand = parents 25-44;
    a country artist = broader middle-income south/midwest tilt).

Corrections are MANDATORY, not advisory: an implausible category is
replaced with the corrected distribution, renormalized to sum exactly
100 with subject-salted micro-jitter (hashlib-deterministic; no bucket
lands on a clean 2dp boundary; 4dp messy values), and Raw / Projection
/ Category Share are recomputed for the changed rows via the canonical
chain (Raw = round(sample x BP/100), Proj = round(Raw/10M x 329.9M)).
Both wire points run BEFORE the enforcer chain, so the terminal
recompute_raw_and_projection pass canonicalizes everything again.

Fail-safe posture (matches augment_from_hostmap + hybrid sanity):
on API failure, timeout, or unparseable output the gate logs loudly
and passes the frame through unchanged - additive intelligence never
aborts a build. But when the call succeeds and flags a category, the
correction is applied, always.

Scope: TU builds and time-shifted refreshes only. NEVER cuts
(avid / gender / age / geo) - cuts inherit demos from their parent by
construction and have their own coherence enforcers (cut_demo_anchor).
The gate self-skips when the subject or out-key carries a
"{Subject} - {Cut}" suffix, and callers gate too.

Public API
----------
    enforce_demo_plausibility(df, subject, persona_brief=None,
                              brand_category=None, *, s3_key=None,
                              genpop_demos=None, claude_call=None,
                              verbose=True) -> (df, report)

    ``claude_call`` and ``genpop_demos`` exist for offline tests:
    claude_call(system, user) -> str replaces the live client;
    genpop_demos {cat: {bucket: pct}} replaces the S3 Gen Pop load.

Read-only audit CLI (no S3 writes, real Claude call):
    python3 -m migration.demo_plausibility_gate "MS NOW.csv"

The dumb deterministic tripwire underneath this reasoned layer is
invariant I10 in migration/final_ship_gate.py (degenerate one-bucket
demo on a TU). This module is the smart layer; I10 is the backstop.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
from typing import Callable, Optional

__all__ = ["enforce_demo_plausibility", "DEMO_CATS_100"]

BUCKET = "dashboard-inputs"
GENPOP_KEY = "Gen_Pop_2026.csv"
US_POP = 329_900_000
PANEL_DENOM = 10_000_000

# The nine sum-to-100 demographic categories under review. Canonical
# spellings first; the underscore/space variants both appear in shipped
# files and are treated as the same category.
DEMO_CATS_100 = [
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "PARENTAL_STATUS", "RELATIONSHIP", "SEXUAL_ORIENTATION",
]
_CAT_ALIASES = {
    "PARENTAL STATUS": "PARENTAL_STATUS",
    "SEXUAL ORIENTATION": "SEXUAL_ORIENTATION",
}

# Anthropic web-search tool descriptors (same ladder avid_share_reasoner
# and hybrid_reasoning use): newest first, legacy second, text-only last.
_WEB_SEARCH_TOOL = {
    "type": "web_search_20260209", "name": "web_search", "max_uses": 5,
}
_WEB_SEARCH_TOOL_LEGACY = {
    "type": "web_search_20250305", "name": "web_search", "max_uses": 5,
}

_SYSTEM_PROMPT = """You are an audience measurement analyst reviewing a demographic \
profile before it ships.

You are given, for one subject's Total Universe audience, the bucket \
distribution of each demographic category, plus the US general-population \
distribution for the same buckets so you can reason about indexes.

For EACH category, judge whether the distribution is PLAUSIBLE for what is \
publicly known about this specific audience. Reason from real-world \
knowledge and published research (Pew, MRI-Simmons, Nielsen, platform \
filings): a cable news network's audience is older, heavily \
college-educated, and indexes well above gen pop on $75K+ household \
income; a kids' toy brand's buyers concentrate in parents 25-44; a \
country artist tilts broader middle-income with a south/midwest lean. \
An audience that should index clearly ABOVE gen pop on a dimension but \
reads at or BELOW gen pop (or vice versa) is implausible.

Be conservative: flag a category ONLY when its shape is clearly wrong \
for this audience, not when it is merely a few points off your intuition. \
Distributions within a reasonable band of a defensible persona read are \
plausible.

For every category you flag as implausible you MUST provide a corrected \
distribution containing EVERY bucket exactly as listed for that category \
(same labels, verbatim), with percentages that sum to approximately 100 \
and reflect the publicly known shape of this audience.

Output STRICT JSON only, no prose outside it:
{"categories": {"<CATEGORY>": {"verdict": "plausible"|"implausible", \
"reason": "<one line>", "corrected": {"<bucket>": <pct>, ...}}, ...}}

Include every category you were given. Omit "corrected" for plausible \
categories."""


# ---------------------------------------------------------------------------
# Deterministic helpers (hashlib - never Python hash(), which is salted
# per process)
# ---------------------------------------------------------------------------

def _unit(subject: str, salt: str) -> float:
    """Deterministic [0,1) draw from sha256(subject|salt)."""
    h = hashlib.sha256(f"{subject}|{salt}".encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _on_2dp_boundary(v: float) -> bool:
    scaled = int(round(round(v, 4) * 10000))
    return scaled % 100 == 0


def _renorm_messy(subject: str, cat: str, pairs):
    """Renormalize [(label, value), ...] to sum exactly 100.0000 with
    subject-salted micro-jitter so no bucket lands on a clean 2dp
    boundary. Deterministic per (subject, category, label)."""
    labels = [p[0] for p in pairs]
    vals = []
    for label, v in pairs:
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        u = _unit(subject, f"demo-plaus|{cat}|{label}")
        v = max(0.0101, v + (u - 0.5) * 0.06)
        vals.append(v)
    total = sum(vals)
    if total <= 0:
        vals = [100.0 / len(vals)] * len(vals)
        total = 100.0
    vals = [round(v * 100.0 / total, 4) for v in vals]
    # Absorb the rounding residual on the largest bucket.
    resid = round(100.0 - sum(vals), 4)
    if abs(resid) >= 0.0001:
        i_max = max(range(len(vals)), key=lambda i: vals[i])
        vals[i_max] = round(vals[i_max] + resid, 4)
    # De-boundary passes: move a salted epsilon between the offending
    # bucket and the largest other bucket (zero-sum, keeps the 100).
    for _ in range(6):
        moved = False
        for i, v in enumerate(vals):
            if not _on_2dp_boundary(v):
                continue
            d = 1 + int(_unit(subject, f"demo-plaus-db|{cat}|{labels[i]}") * 89)
            if d % 100 == 0:
                d += 1
            delta = d / 10000.0
            donors = [j for j in range(len(vals)) if j != i]
            j = max(donors, key=lambda k: vals[k])
            vals[i] = round(vals[i] + delta, 4)
            vals[j] = round(vals[j] - delta, 4)
            moved = True
        if not moved:
            break
    return list(zip(labels, vals))


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------

def _num(v):
    try:
        s = str(v).replace("%", "").replace(",", "").strip()
        if not s or s.lower() in ("nan", "none", "null", "-"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _detect_cols(df):
    cols = {str(c).strip().lower(): c for c in df.columns}

    def _find(pred):
        for lc, c in cols.items():
            if pred(lc):
                return c
        return None

    return {
        "cat": _find(lambda c: c == "column"),
        "val": _find(lambda c: c == "value"),
        "bp": _find(lambda c: c.startswith("brand penetration")),
        "cs": _find(lambda c: c == "category share"),
        "raw": _find(lambda c: c.startswith("original raw")),
        "proj": _find(lambda c: "projection" in c),
    }


def _canon_cat(cat: str) -> Optional[str]:
    cu = str(cat or "").strip().upper()
    cu = _CAT_ALIASES.get(cu, cu)
    return cu if cu in DEMO_CATS_100 else None


def _norm_bucket(s: str) -> str:
    s = str(s or "").strip().upper()
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u2019", "").replace("\u2018", "").replace("'", "")
    return re.sub(r"\s+", " ", s)


def _is_cut(subject, s3_key) -> Optional[str]:
    """Return a skip reason when this frame is a derived cut, else None.
    Same detection the rest of the codebase uses: a ' - ' cut suffix in
    the out-key basename or the subject/display name."""
    for cand in (s3_key, subject):
        base = os.path.basename(str(cand or "").strip())
        if base.lower().endswith(".csv"):
            base = base[:-4]
        if " - " in base:
            return f"cut-suffixed name ({base!r})"
    return None


def _extract_sample(df, cols):
    """Sample size from BRAND INPUT (fallback SAMPLE SIZE) row Raw."""
    if cols["raw"] is None or cols["cat"] is None:
        return None
    cat_u = df[cols["cat"]].astype(str).str.upper().str.strip()
    for cat in ("BRAND INPUT", "SAMPLE SIZE"):
        sel = df[cat_u == cat]
        if len(sel) == 0:
            continue
        v = _num(sel.iloc[0].get(cols["raw"]))
        if v is not None and v > 0:
            return int(round(v))
    return None


def _extract_demo_distributions(df, cols):
    """{canonical_cat: [(row_index, bucket_label, bp_float), ...]} for
    every reviewed demo category present with 2+ parseable buckets."""
    out = {}
    if cols["cat"] is None or cols["val"] is None or cols["bp"] is None:
        return out
    for idx in df.index:
        cat = _canon_cat(df.at[idx, cols["cat"]])
        if cat is None:
            continue
        bp = _num(df.at[idx, cols["bp"]])
        if bp is None:
            continue
        label = str(df.at[idx, cols["val"]]).strip()
        if not label:
            continue
        out.setdefault(cat, []).append((idx, label, bp))
    return {c: rows for c, rows in out.items() if len(rows) >= 2}


def _write_cell_like(df, idx, col, new_float, decimals=4, as_count=False):
    """Write a numeric value back preserving the cell's existing style:
    '48.1234%' strings keep the percent sign, bare strings stay strings,
    numeric-object cells (BG.py path) stay numeric."""
    old = df.at[idx, col]
    if as_count:
        iv = int(round(new_float))
        df.at[idx, col] = str(iv) if isinstance(old, str) else iv
        return
    v = round(float(new_float), decimals)
    if isinstance(old, str):
        if old.strip().endswith("%"):
            df.at[idx, col] = f"{v:.4f}%"
        else:
            df.at[idx, col] = f"{v:.4f}"
    else:
        df.at[idx, col] = v


# ---------------------------------------------------------------------------
# Gen Pop demo distributions (independent load, cached, injectable)
# ---------------------------------------------------------------------------

_GENPOP_LOCK = threading.Lock()
_GENPOP_CACHE = {"map": None, "ts": 0.0}
_GENPOP_TTL = 3600.0


def _load_genpop_demos(verbose=True):
    """{canonical_cat: {bucket_label: bp}} from the canonical Gen Pop
    file in S3. Returns None on any failure (the prompt then omits the
    gen pop anchor; the gate still runs)."""
    with _GENPOP_LOCK:
        if (_GENPOP_CACHE["map"] is not None
                and time.time() - _GENPOP_CACHE["ts"] < _GENPOP_TTL):
            return _GENPOP_CACHE["map"]
    try:
        import boto3
        import pandas as pd
        body = boto3.client("s3", region_name="us-east-2").get_object(
            Bucket=BUCKET, Key=GENPOP_KEY)["Body"].read()
        gdf = pd.read_csv(io.BytesIO(body), keep_default_na=False, dtype=str)
        cols = _detect_cols(gdf)
        out = {}
        for idx in gdf.index:
            cat = _canon_cat(gdf.at[idx, cols["cat"]])
            if cat is None:
                continue
            bp = _num(gdf.at[idx, cols["bp"]])
            if bp is None:
                continue
            out.setdefault(cat, {})[str(gdf.at[idx, cols["val"]]).strip()] = bp
        with _GENPOP_LOCK:
            _GENPOP_CACHE["map"] = out
            _GENPOP_CACHE["ts"] = time.time()
        return out
    except Exception as e:
        if verbose:
            print(f"[demo-plaus] Gen Pop load failed "
                  f"({type(e).__name__}: {e}); reviewing without the "
                  f"gen pop anchor")
        return None


# ---------------------------------------------------------------------------
# Claude call + parsing
# ---------------------------------------------------------------------------

def _build_user_prompt(subject, brand_category, persona_brief,
                       demo_dists, genpop_demos):
    parts = [
        f"SUBJECT: {subject}",
        f"BRAND CATEGORY: {(brand_category or 'UNKNOWN').strip().upper()}",
    ]
    if persona_brief:
        pb = str(persona_brief).strip()
        if len(pb) > 4000:
            pb = pb[:4000] + " ..."
        parts.append(f"AUDIENCE PERSONA BRIEF:\n{pb}")
    for cat in DEMO_CATS_100:
        rows = demo_dists.get(cat)
        if not rows:
            continue
        lines = [f"CATEGORY {cat} - profile distribution:"]
        for _, label, bp in rows:
            lines.append(f"  {label}: {bp:.4f}")
        gp = (genpop_demos or {}).get(cat)
        if gp:
            lines.append(f"CATEGORY {cat} - US gen pop distribution "
                         f"(for indexing):")
            for label, bp in gp.items():
                lines.append(f"  {label}: {bp:.4f}")
        parts.append("\n".join(lines))
    parts.append(
        f"Review each category's distribution for {subject}'s audience. "
        f"STRICT JSON only."
    )
    return "\n\n".join(parts)


def _default_claude_call(system, user):
    """Live client, mirroring avid_share_reasoner's ladder: newest
    web-search tool first, legacy second, text-only last. Returns ''
    on total failure."""
    try:
        from migration.claude_client import claude_messages
    except ImportError:
        from claude_client import claude_messages  # type: ignore
    raw = ""
    for tools in ([_WEB_SEARCH_TOOL], [_WEB_SEARCH_TOOL_LEGACY], None):
        try:
            raw = claude_messages(
                system=system, user=user,
                max_tokens=6000, temperature=0.2, tools=tools,
            )
        except Exception as e:
            print(f"[demo-plaus] claude_messages raised "
                  f"({type(e).__name__}: {e}); trying next variant")
            raw = ""
        if raw and raw.strip():
            break
    return raw


def _parse_response(raw):
    """Extract the {"categories": {...}} object. Returns None when
    unparseable."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("categories"), dict):
            return obj["categories"]
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enforce_demo_plausibility(df, subject, persona_brief=None,
                              brand_category=None, *, s3_key=None,
                              genpop_demos=None,
                              claude_call: Optional[Callable] = None,
                              verbose=True):
    """Review the sum-to-100 demo categories against the subject's
    persona and auto-correct implausible ones. Returns (df, report).

    Never raises and never blocks the build: any failure returns the
    input frame unchanged with a loud log. A successful call that flags
    a category ALWAYS applies the correction (mandatory, not advisory).
    """
    report = {"ran": False, "subject": subject, "categories": {},
              "n_corrected": 0}
    try:
        if df is None or len(df) == 0:
            report["skipped"] = "empty frame"
            return df, report
        skip = _is_cut(subject, s3_key)
        if skip:
            report["skipped"] = skip
            if verbose:
                print(f"[demo-plaus] skipped: {skip} - cuts inherit "
                      f"demos from their parent")
            return df, report

        cols = _detect_cols(df)
        demo_dists = _extract_demo_distributions(df, cols)
        if not demo_dists:
            report["skipped"] = "no reviewable demo categories"
            if verbose:
                print(f"[demo-plaus] {subject}: no reviewable demo "
                      f"categories found; skipping")
            return df, report

        if genpop_demos is None:
            genpop_demos = _load_genpop_demos(verbose=verbose)

        user = _build_user_prompt(subject, brand_category, persona_brief,
                                  demo_dists, genpop_demos)
        call = claude_call or _default_claude_call
        try:
            raw = call(_SYSTEM_PROMPT, user)
        except Exception as e:
            print(f"⚠️ [demo-plaus] Claude call raised "
                  f"({type(e).__name__}: {e}) for {subject!r} - "
                  f"PASSING FRAME THROUGH UNCHANGED")
            report["error"] = f"claude call raised: {e}"
            return df, report

        verdicts = _parse_response(raw)
        if verdicts is None:
            print(f"⚠️ [demo-plaus] Claude call failed or unparseable "
                  f"for {subject!r} - PASSING FRAME THROUGH UNCHANGED")
            report["error"] = "unparseable or empty response"
            return df, report

        report["ran"] = True
        df = df.copy()
        sample = _extract_sample(df, cols)

        for cat, rows in demo_dists.items():
            v = verdicts.get(cat)
            if not isinstance(v, dict):
                # Also accept the space-spelled key the file itself used.
                for alias, canon in _CAT_ALIASES.items():
                    if canon == cat and isinstance(verdicts.get(alias), dict):
                        v = verdicts[alias]
                        break
            if not isinstance(v, dict):
                report["categories"][cat] = {
                    "verdict": "missing", "corrected": False,
                    "reason": "model returned no verdict"}
                continue
            verdict = str(v.get("verdict") or "").strip().lower()
            reason = str(v.get("reason") or "").strip()
            entry = {"verdict": verdict, "reason": reason,
                     "corrected": False}
            report["categories"][cat] = entry
            if verdict != "implausible":
                continue

            corrected = v.get("corrected")
            if not isinstance(corrected, dict) or not corrected:
                print(f"⚠️ [demo-plaus] {subject} / {cat}: flagged "
                      f"implausible but no corrected distribution "
                      f"returned; leaving as-is")
                entry["error"] = "no corrected distribution"
                continue
            corr_norm = {_norm_bucket(k): _num(val)
                         for k, val in corrected.items()}
            matched = sum(1 for _, label, _bp in rows
                          if corr_norm.get(_norm_bucket(label)) is not None)
            if matched < max(2, int(0.8 * len(rows))):
                print(f"⚠️ [demo-plaus] {subject} / {cat}: corrected "
                      f"distribution covers only {matched}/{len(rows)} "
                      f"buckets; leaving as-is")
                entry["error"] = "corrected distribution bucket mismatch"
                continue

            old = {label: bp for _, label, bp in rows}
            pairs = []
            for _, label, bp in rows:
                cv = corr_norm.get(_norm_bucket(label))
                pairs.append((label, cv if cv is not None else bp))
            new_pairs = _renorm_messy(subject, cat, pairs)
            new_by_label = dict(new_pairs)

            for idx, label, _bp in rows:
                nv = new_by_label[label]
                _write_cell_like(df, idx, cols["bp"], nv)
                # Demo blocks carry Category Share = BP identity.
                if cols["cs"] is not None:
                    _write_cell_like(df, idx, cols["cs"], nv)
                if sample:
                    raw_v = round(nv / 100.0 * sample)
                    if cols["raw"] is not None:
                        _write_cell_like(df, idx, cols["raw"], raw_v,
                                         as_count=True)
                    if cols["proj"] is not None:
                        proj_v = round(raw_v / PANEL_DENOM * US_POP)
                        _write_cell_like(df, idx, cols["proj"], proj_v,
                                         as_count=True)

            entry["corrected"] = True
            entry["old"] = old
            entry["new"] = {label: v for label, v in new_pairs}
            report["n_corrected"] += 1
            if verbose:
                print(f"[demo-plaus] {subject} / {cat}: IMPLAUSIBLE - "
                      f"{reason or 'no reason given'}; corrected "
                      f"{len(new_pairs)} buckets (sum "
                      f"{sum(v for _, v in new_pairs):.4f})")

        if verbose:
            n_pl = sum(1 for e in report["categories"].values()
                       if e.get("verdict") == "plausible")
            print(f"[demo-plaus] {subject}: reviewed "
                  f"{len(report['categories'])} demo categories - "
                  f"{n_pl} plausible, {report['n_corrected']} corrected")
        return df, report
    except Exception as e:
        print(f"⚠️ [demo-plaus] internal error "
              f"({type(e).__name__}: {e}) for {subject!r} - "
              f"PASSING FRAME THROUGH UNCHANGED")
        report["error"] = f"internal: {e}"
        return df, report


if __name__ == "__main__":
    # Read-only audit: downloads the key, runs the gate, prints the
    # report. NEVER writes anything back to S3.
    import sys as _sys
    import boto3 as _boto3
    import pandas as _pd

    _keys = [a for a in _sys.argv[1:] if not a.startswith("-")]
    if not _keys:
        print("usage: python3 -m migration.demo_plausibility_gate "
              "<s3_key> [<s3_key> ...]")
        raise SystemExit(2)
    _s3 = _boto3.client("s3", region_name="us-east-2")
    for _key in _keys:
        _body = _s3.get_object(Bucket=BUCKET, Key=_key)["Body"].read()
        _df = _pd.read_csv(io.BytesIO(_body), keep_default_na=False,
                           dtype=str)
        _base = os.path.basename(_key)
        if _base.lower().endswith(".csv"):
            _base = _base[:-4]
        _subj = re.sub(r"[_\s]\d{2}[_\s]\d{2}[_\s]\d{4}.*$", "",
                       _base.split(" - ")[0]).replace("_", " ").strip()
        _cols = _detect_cols(_df)
        _bc = ""
        try:
            _cu = _df[_cols["cat"]].astype(str).str.upper().str.strip()
            _bc_rows = _df[_cu == "BRAND CATEGORY"]
            if len(_bc_rows):
                _bc = str(_bc_rows.iloc[0][_cols["val"]]).strip()
        except Exception:
            pass
        print(f"\n=== {_key} (subject={_subj!r}, category={_bc!r}) ===")
        _, _report = enforce_demo_plausibility(
            _df, _subj, persona_brief=None, brand_category=_bc,
            s3_key=_key, verbose=True,
        )
        print(json.dumps(
            {k: v for k, v in _report.items() if k != "subject"},
            indent=2, default=str))
