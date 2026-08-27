#!/usr/bin/env python3
"""Pre-ship reasoned vetting - research + reasoning review of every NEW
profile and cut, after the mechanical ship gate passes and before the
bytes publish.

Why this module exists (Jenna directive 2026-08-26, verbatim: "how do
we ebsure these errors never ship again? go through all found errors
today and ensure no new profiles ship with errors like these. there
has to be research and reasoning done before shipping a profile"):
today's QA sweep surfaced defect classes that only a REASONED review
catches - the mechanical invariants all held, but the numbers were
wrong for the audience. The canonical case: Bethenny Frankel shipped
Visa at index 70.7 on a mid-income, 35-54, heavily female audience.
Every deterministic check passed; the value was simply implausible
against Federal Reserve SCF / Statista-class benchmarks for that
demographic. A human reviewer caught it by eye. This module is that
reviewer, automated, on every new file.

Where it sits in the publish sequence
-------------------------------------
    generation-time scrubs (deladder_decision_map, ...)
 -> enforcer chain (run_all_enforcers, 42 steps)
 -> write safety net + polish + pre-publish gate G1-G18
 -> mechanical final ship gate I1-I19 (final_ship_gate.py)
 -> THIS MODULE (reasoned vetting, one consolidated research call)
 -> publish

It runs on NEW keys only (fresh builds, refreshes to a new dated key,
fresh cut syntheses). In-place corrections to an existing key skip it:
those are mechanical repairs of already-vetted content and re-running
a paid reasoning pass on each would burn cost without new information.
Cut engines pass is_new=True explicitly because a re-derived cut is
new reasoning even when it overwrites the same key.

What the reasoner reviews (one consolidated call, web research on)
------------------------------------------------------------------
Following .cursor/rules/crosswalk-audience-vetting-framework.mdc:

  * Face-validity per major category against the audience's demo
    composition. The Visa case: is index 70 on Visa plausible for a
    mid-income 35-54 female audience? No, per Federal Reserve SCF /
    Statista-class benchmarks.
  * Benchmark comparisons for the high-stakes grids (credit and
    banking, streaming platforms, search, social, QSR) using
    approved-source consensus (SEC filings, Pew, Statista, eMarketer).
    Never-use sources (store visits, household device penetration,
    total brand reach) are excluded by instruction.
  * Subject coherence: self-pins present (TALENT / FRANCHISE / own
    league at 100), own-platform carriage pins per the
    self_property_coherence convention, cuts strictly subsets of
    parents.
  * Synthetic-signature review: the mechanical detectors' outputs
    (fractional ladders, cross-grid duplicates, exact-100 non-subject
    pins) ride into the prompt as context so the reasoner spends its
    budget on judgment calls, not re-detection.
  * BRAND INPUT slug sanity: clickstream-plausible slugs, no category
    labels, no generic platform landing pages.
  * Demographic sums, sample-size messiness, deliverable naming.

Verdict handling
----------------
  PASS        -> publish; ledger entry for observability.
  BORDERLINE  -> publish; finding logged to the review ledger at
                 s3://dashboard-inputs/system/vetting_ledger/ for the
                 weekly mining job.
  FAIL, every finding deterministic + benchmark-backed
              -> autofix in place (sanity-guarded, jittered, 4dp),
                 chain recompute, re-sort, mechanical ship gate re-run
                 on the corrected bytes, publish. Ledger records what
                 changed.
  FAIL, any finding needing judgment
              -> quarantine to _quarantine/ + plain-language hold
                 email (same flow as the mechanical gate) + raise
                 PreShipVettingError (a ShipGateError subclass, so
                 every existing caller's hold handling applies).

Fail-open posture on INFRASTRUCTURE only: if the reasoner is
unreachable, times out, or returns unparseable output, the file
publishes (the mechanical gate already passed) with a loud log and a
ledger entry recording that the reasoned review was skipped. A
successful reasoning call is always acted on.

Cost and latency controls
-------------------------
  * ONE consolidated reasoning call per file (categories are chunked
    into a single prompt; row lists are capped).
  * Output tokens capped (default 6000).
  * Benchmark context is cached per day per process: the static
    benchmark table is code, and the Gen Pop anchor map reuses
    genpop_baseline's hourly cache.
  * Web research capped at 5 searches per call.
  * Wall-clock target: under ~90s added per profile on the text-only
    path; research calls can run longer but are bounded by
    claude_client's watchdog.

Deterministic pre-scan (no API, always runs)
--------------------------------------------
Besides feeding the reasoner, the pre-scan closes two audit gaps:

  * Benchmark-band candidates (2026-08-26 audit gap 4): index vs Gen
    Pop for the anchor brands in the high-stakes grids, checked
    against published-consensus bands. Out-of-band rows are nominated
    to the reasoner; a PASS verdict that leaves a nominated row
    unaddressed downgrades to BORDERLINE so the ledger catches it.
  * Gen Pop baseline coverage (2026-08-26 audit gap 5): every row
    with a penetration must resolve a Gen Pop baseline. Missing rows
    are counted, exampled, fed to the reasoner, and written to the
    ledger. Brands known to our brand universe self-heal via the
    daily Gen Pop sync (migration/genpop_hostmap_sync.py); the ledger
    makes the gap visible until they do.

Public API
----------
    run_pre_ship_vetting(df, subject, s3_key, *, category=None,
                         s3_client=None, claude_call=None,
                         genpop_map=None, enforce=True, is_new=None,
                         sort_fn=None, ledger=True, verbose=True)
        -> (df, report)

    vet_before_publish(df, body, subject, s3_key, *, ...)
        -> (df, body, report)   # bytes-aware wrapper for the writer

Read-only audit CLI (no S3 writes, no ledger, real reasoning call):
    python3 -m migration.pre_ship_vetting <s3_key_or_local_csv> ...
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

__all__ = [
    "PreShipVettingError",
    "run_pre_ship_vetting",
    "vet_before_publish",
    "BENCHMARK_BANDS",
]

BUCKET = "dashboard-inputs"
QUARANTINE_PREFIX = "_quarantine/"
LEDGER_PREFIX = "system/vetting_ledger/"
US_POP = 329_900_000
PANEL_DENOM = 10_000_000

HOLD_NOTICE_TO = ["jenna@crosswalknyc.com", "jessie@crosswalknyc.com"]
HOLD_NOTICE_FROM = "Crosswalk Ops <jenna@crosswalknyc.com>"

# Autofix safety rails: the reasoner proposes, these dispose.
MAX_AUTOFIX_ROWS = 40
MAX_FIX_MOVE_PP = 45.0

DEMO_CATS = {
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "PARENTAL_STATUS", "PARENTAL STATUS",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION",
    "LOCATION", "AGE_OF_CHILDREN", "AGE OF CHILDREN",
}
META_CATS = {
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT",
    "INPUT_METADATA", "INPUT METADATA", "GENERAL",
}
FAN_CATS = {"AVID FAN", "CASUAL FAN"}

TALENT_FAMILY = {
    "ACTOR", "ATHLETE", "COMEDIAN", "INFLUENCER/CREATOR",
    "CREATOR/INFLUENCER", "EMERGING TALENT", "HOST/PERSONALITY",
    "MUSICIAN/BAND", "PODCASTER", "POLITICS/ACTIVIST",
    "WRITER/DIRECTOR/AUTHOR/ARTIST",
}

# Grids where an identical 4dp value for the same brand across two
# categories is REQUIRED by convention (Rule 3b purchase mirror and the
# sports companion sync), so the duplicate scan must not flag them.
MIRROR_FAMILY_CATS = {
    "MOST PURCHASED BRANDS", "CPG", "APPAREL/FOOTWEAR",
    "APPAREL & FOOTWEAR", "BEAUTY/WELLNESS", "HOME/OUTDOOR",
    "ACCESSORIES", "TECHNOLOGY BRAND", "PETS", "WHERE THEY SHOP",
    "SPORTS TEAM", "MLB", "NBA", "NFL", "NHL", "MLS", "WNBA", "MILB",
    "EPL", "LA LIGA", "SERIE A", "LIGUE 1", "BUNDESLIGA", "CFB",
    "SOCCER", "AL", "NL", "AFC", "NFC", "AL/NL", "AFC/NFC",
    "WESTERN CONFERENCE", "EASTERN CONFERENCE",
}

# ---------------------------------------------------------------------------
# Benchmark bands (2026-08-26 audit gap 4). Index vs Gen Pop bands for
# the anchor brands of the high-stakes grids, valid for a MAINSTREAM
# US ADULT audience. Sources are approved-class only (Federal Reserve
# SCF/SHED, Pew Research, Statista, eMarketer, SEC filings). The bands
# NOMINATE candidates; the reasoner issues the verdict conditioned on
# the audience's demographic composition (a kids-content universe or a
# heavily under-18 audience legitimately breaks these).
# ---------------------------------------------------------------------------
BENCHMARK_BANDS = {
    "CREDIT PROVIDER": {
        "visa": (85, 130, "Federal Reserve SHED 2023: 82% of US adults "
                          "hold a credit card; Visa is the largest US "
                          "network (Statista 2024: ~52% of network "
                          "purchase volume)"),
        "mastercard": (78, 135, "Statista 2024: Mastercard ~36% of US "
                                "cards; near-universal acceptance"),
        "americanexpress": (40, 260, "Amex skews high-income (SEC 10-K "
                                     "premium positioning)"),
        "discovercreditcard": (45, 230, "Statista: Discover ~8% of US "
                                        "cards"),
    },
    "CREDIT PROVIDERS": "CREDIT PROVIDER",
    "BANKING": {
        "chase": (70, 210, "JPMorgan 10-K: ~80M US consumer accounts, "
                           "largest US retail bank"),
        "bankofamerica": (65, 210, "Bank of America 10-K: ~69M "
                                   "consumer and small-business "
                                   "clients"),
        "wellsfargo": (60, 210, "Wells Fargo 10-K: ~70M customers"),
    },
    "BANKS": "BANKING",
    "DIGITAL BANKING": {
        "paypal": (70, 165, "Pew Research 2022: 57% of US adults have "
                            "used PayPal"),
        "venmo": (48, 220, "Pew Research 2022: 38% of US adults"),
        "zelle": (55, 220, "Early Warning Services reports 2B+ annual "
                           "transactions; bank-embedded reach"),
        "cashapp": (40, 260, "Pew Research 2022: 26% of US adults; "
                             "younger and lower-income tilt"),
    },
    "SEARCH ENGINE/AI": {
        "google": (85, 114, "Pew Research: 93% of US adults use the "
                            "internet; Statista: Google ~89% US "
                            "search share"),
    },
    "SOCIAL MEDIA": {
        "youtube": (80, 120, "Pew Research 2024: 83% of US adults use "
                             "YouTube"),
        "facebook": (68, 132, "Pew Research 2024: 68% of US adults"),
        "instagram": (52, 175, "Pew Research 2024: 47% of US adults"),
        "tiktok": (38, 210, "Pew Research 2024: 33% of US adults; "
                            "strong under-30 skew"),
    },
    "STREAMING/PLATFORM": {
        "netflix": (72, 132, "Netflix reports 301M global subscribers; "
                             "eMarketer: the most-penetrated US SVOD"),
        "amazonprimevideo": (65, 140, "eMarketer: Prime Video reaches "
                                      "most Prime members"),
    },
    "STREAMING VIDEO": "STREAMING/PLATFORM",
    "QSR": {
        "mcdonalds": (75, 125, "Statista / QSR Magazine: ~85-90% "
                               "annual US reach, the category anchor"),
        "starbucks": (58, 165, "Starbucks 10-K: ~34M US 90-day active "
                               "rewards members; urban-income tilt"),
    },
}

# Youth share above which the adult-anchored bands become advisory
# (the reasoner is told the audience is youth-skewed and judges from
# composition instead).
YOUTH_ADVISORY_SHARE = 25.0

# Seed map of single-homing categories: on a brand-scoped universe
# (customers / subscribers / members / buyers / switchers of X), rivals
# in these categories are EXPECTED to read below general-population
# consensus, and a rival at gen-pop level is itself suspect. This is a
# small seed, not a rulebook: the reasoner makes the actual call
# (Jenna 2026-08-26: consensus is a base, the call is per-audience).
SINGLE_HOMING_CATS = (
    "BANKING", "BANKS", "TELECOM", "INSURANCE", "SECURITY",
)

_WEB_SEARCH_TOOL = {
    "type": "web_search_20260209", "name": "web_search", "max_uses": 5,
}
_WEB_SEARCH_TOOL_LEGACY = {
    "type": "web_search_20250305", "name": "web_search", "max_uses": 5,
}


class PreShipVettingError(RuntimeError):
    """Raised when the reasoned vetting review holds a file. Subclasses
    nothing exotic on purpose; see _mk_error for the ShipGateError
    aliasing that lets every existing hold handler catch it."""

    def __init__(self, s3_key, findings, quarantine_key=None):
        self.s3_key = s3_key
        self.violations = findings or []
        self.findings = self.violations
        self.quarantine_key = quarantine_key
        name = _display_name(s3_key)
        super().__init__(
            f"{name} was held for review before delivery: "
            f"{len(self.violations)} finding(s) from the final audience "
            f"review need a judgment call. The file was not published."
        )


def _rebase_error_class():
    """Rebase PreShipVettingError onto ShipGateError so the queue
    worker's `except ShipGateError` and every cut engine's re-raise
    treat a vetting hold exactly like a mechanical gate hold. Falls
    back to RuntimeError parentage when the gate module is
    unavailable (tests, stripped environments)."""
    global PreShipVettingError
    try:
        try:
            from migration.final_ship_gate import ShipGateError
        except ImportError:
            from final_ship_gate import ShipGateError  # type: ignore
    except Exception:
        return
    if ShipGateError in PreShipVettingError.__mro__:
        return

    class _PreShipVettingError(ShipGateError):
        def __init__(self, s3_key, findings, quarantine_key=None):
            super().__init__(s3_key, findings,
                             quarantine_key=quarantine_key)
            self.findings = self.violations

    _PreShipVettingError.__name__ = "PreShipVettingError"
    _PreShipVettingError.__qualname__ = "PreShipVettingError"
    _PreShipVettingError.__doc__ = PreShipVettingError.__doc__
    PreShipVettingError = _PreShipVettingError


_rebase_error_class()


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------

def _num(v):
    try:
        s = str(v).replace("%", "").replace(",", "").strip()
        if not s or s.lower() in ("nan", "none", "null", "-"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _norm_brand(b):
    return re.sub(r"[^a-z0-9]+", "", str(b or "").lower())


def _norm_cat(c):
    return re.sub(r"[_\s]+", " ", str(c or "").strip().upper())


def _gp_get(gp_map, cat_u, brand_norm):
    """Alias-aware Gen Pop lookup: tries the raw category first, then
    the alias-folded spelling genpop_baseline stores under (e.g.
    INFLUENCER/CREATOR rows resolve CREATOR/INFLUENCER entries)."""
    if not gp_map:
        return None
    hit = gp_map.get((cat_u, brand_norm))
    if hit is not None:
        return hit
    try:
        try:
            from migration.genpop_baseline import _norm_cat as _gp_norm
        except ImportError:
            from genpop_baseline import _norm_cat as _gp_norm  # type: ignore
        return gp_map.get((_gp_norm(cat_u), brand_norm))
    except Exception:
        return None


def _display_name(s3_key):
    base = os.path.basename(str(s3_key or "").strip())
    return base[:-4] if base.lower().endswith(".csv") else base


def _unit(seed: str) -> float:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _on_2dp_boundary(v: float) -> bool:
    return int(round(round(v, 4) * 10000)) % 100 == 0


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
        "raw": _find(lambda c: c.startswith("original raw") or c == "raw"),
        "proj": _find(lambda c: "projection" in c),
    }


def _extract_sample(df, cols):
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


def _meta_value(df, cols, cat_name):
    if cols["cat"] is None or cols["val"] is None:
        return ""
    cat_u = df[cols["cat"]].astype(str).str.upper().str.strip()
    sel = df[cat_u == cat_name]
    if len(sel) == 0:
        return ""
    return str(sel.iloc[0][cols["val"]]).strip()


def _subject_tokens(subject, s3_key):
    toks = set()
    for cand in (subject, _display_name(s3_key).split(" - ")[0]):
        n = _norm_brand(cand)
        if n:
            toks.add(n)
    return toks


def _resolve_band_table(cat_u):
    entry = BENCHMARK_BANDS.get(cat_u)
    if isinstance(entry, str):
        entry = BENCHMARK_BANDS.get(entry)
    return entry if isinstance(entry, dict) else None


def _env_enabled():
    v = (os.environ.get("PRE_SHIP_VETTING") or "").strip().lower()
    return v not in ("0", "off", "false", "no")


def _key_exists(s3_key, s3_client):
    try:
        s3_client.head_object(Bucket=BUCKET, Key=str(s3_key))
        return True
    except Exception:
        return False


def _s3(s3_client):
    if s3_client is not None:
        return s3_client
    import boto3
    return boto3.client("s3", region_name="us-east-2")


# ---------------------------------------------------------------------------
# Deterministic pre-scan
# ---------------------------------------------------------------------------

def _load_genpop(genpop_map, s3_client, verbose):
    if genpop_map is not None:
        return genpop_map
    try:
        try:
            from migration.genpop_baseline import load_genpop_map
        except ImportError:
            from genpop_baseline import load_genpop_map  # type: ignore
        return load_genpop_map(s3_client)
    except Exception as e:
        if verbose:
            print(f"[pre-ship-vetting] Gen Pop map load failed "
                  f"({type(e).__name__}: {e}); benchmark indexes and "
                  f"coverage check skipped")
        return None


def _demo_summary(df, cols):
    out = {}
    if cols["cat"] is None or cols["val"] is None or cols["bp"] is None:
        return out
    for idx in df.index:
        cu = _norm_cat(df.at[idx, cols["cat"]])
        if cu not in ("GENDER", "AGE", "INCOME", "ETHNICITY"):
            continue
        bp = _num(df.at[idx, cols["bp"]])
        if bp is None:
            continue
        out.setdefault(cu, []).append(
            (str(df.at[idx, cols["val"]]).strip(), round(bp, 2)))
    for cu in out:
        out[cu].sort(key=lambda t: -t[1])
    return out


def _youth_share(demo_summary):
    for label, bp in demo_summary.get("AGE", []):
        lu = label.upper()
        if "17" in lu and "UNDER" in lu:
            return bp
    return 0.0


def _deterministic_prescan(df, subject, s3_key, genpop_map, verbose=True):
    """All-local scan. Returns the facts dict fed to the reasoner and
    the ledger. Never raises; partial results carry an 'errors' list."""
    facts = {
        "benchmark_candidates": [],
        "genpop_gaps": {"n_brand_rows": 0, "n_missing": 0, "examples": []},
        "ladders": {"n_flagged": 0, "groups": []},
        "cross_grid_dupes": [],
        "coherence": {},
        "brand_input": {},
        "naming": {},
        "sample": None,
        "demo_summary": {},
        "errors": [],
    }
    cols = _detect_cols(df)
    if cols["cat"] is None or cols["val"] is None or cols["bp"] is None:
        facts["errors"].append("core columns missing")
        return facts

    facts["sample"] = _extract_sample(df, cols)
    facts["demo_summary"] = _demo_summary(df, cols)
    youth = _youth_share(facts["demo_summary"])
    facts["youth_share"] = youth
    subject_toks = _subject_tokens(subject, s3_key)

    # Row walk: benchmark candidates + genpop coverage + dupe map.
    by_brand_bp = {}
    n_brand_rows = 0
    n_missing = 0
    missing_examples = []
    for idx in df.index:
        cu = _norm_cat(df.at[idx, cols["cat"]])
        if not cu or cu in META_CATS or cu in DEMO_CATS or cu in FAN_CATS:
            continue
        bp = _num(df.at[idx, cols["bp"]])
        if bp is None:
            continue
        brand = str(df.at[idx, cols["val"]]).strip()
        bn = _norm_brand(brand)
        if not bn:
            continue
        n_brand_rows += 1

        gp_hit = None
        if genpop_map:
            gp_hit = _gp_get(genpop_map, cu, bn)
            if gp_hit is None and bn not in subject_toks:
                n_missing += 1
                if len(missing_examples) < 15:
                    missing_examples.append(f"{cu} / {brand}")

        band_table = _resolve_band_table(cu)
        if band_table and bn in band_table and gp_hit:
            lo, hi, source = band_table[bn]
            gp_v = gp_hit[0]
            if gp_v and gp_v > 0:
                index = bp / gp_v * 100.0
                if not (lo <= index <= hi):
                    facts["benchmark_candidates"].append({
                        "category": cu, "brand": brand,
                        "bp": round(bp, 4),
                        "genpop_bp": round(gp_v, 4),
                        "index": round(index, 1),
                        "expected_index_band": [lo, hi],
                        "direction": "below" if index < lo else "above",
                        "band_source": source,
                        "youth_advisory": youth >= YOUTH_ADVISORY_SHARE,
                    })

        if 0.0001 < bp < 99.99:
            by_brand_bp.setdefault((bn, round(bp, 4)), []).append(cu)

    facts["genpop_gaps"] = {
        "n_brand_rows": n_brand_rows,
        "n_missing": n_missing,
        "examples": missing_examples,
    }

    # Cross-grid duplicate artifact scan (mirror families exempt).
    for (bn, bp), cats in by_brand_bp.items():
        distinct = sorted(set(cats))
        if len(distinct) < 2:
            continue
        non_mirror = [c for c in distinct if c not in MIRROR_FAMILY_CATS]
        if len(non_mirror) >= 2:
            facts["cross_grid_dupes"].append({
                "brand_norm": bn, "bp": bp, "categories": non_mirror,
            })
    facts["cross_grid_dupes"] = facts["cross_grid_dupes"][:12]

    # Fractional-ladder detector output (mechanical, reused as input).
    try:
        try:
            from migration.fractional_ladders import (
                detect_fractional_ladders, ladder_in_scope,
            )
        except ImportError:
            from fractional_ladders import (  # type: ignore
                detect_fractional_ladders, ladder_in_scope,
            )
        triples = []
        for idx in df.index:
            cu = _norm_cat(df.at[idx, cols["cat"]])
            bp = _num(df.at[idx, cols["bp"]])
            if bp is None:
                continue
            if ladder_in_scope(cu, bp):
                triples.append((idx, cu, bp))
        res = detect_fractional_ladders(triples)
        facts["ladders"] = {
            "n_flagged": len(res["flagged_ids"]),
            "groups": [
                {"category": c, "suffix": s, "count": n, "threshold": t}
                for c, s, n, t in res["percat_groups"][:8]
            ] + [
                {"category": "(file-wide)", "suffix": s, "count": n,
                 "threshold": t}
                for s, n, t in res["filewide_groups"][:4]
            ],
        }
    except Exception as e:
        facts["errors"].append(f"ladder scan: {e}")

    # Subject coherence facts.
    coh = {}
    base = _display_name(s3_key)
    is_cut = " - " in base
    coh["is_cut"] = is_cut
    coh["cut_label"] = base.split(" - ", 1)[1].strip() if is_cut else None
    bc = _meta_value(df, cols, "BRAND CATEGORY").upper()
    coh["brand_category"] = bc
    if bc in TALENT_FAMILY:
        talent_rows = []
        cat_series = df[cols["cat"]].astype(str).str.upper().str.strip()
        for idx in df.index[cat_series == "TALENT"]:
            bn = _norm_brand(df.at[idx, cols["val"]])
            bp = _num(df.at[idx, cols["bp"]])
            if bn in subject_toks and bp is not None:
                talent_rows.append(bp)
        coh["talent_grid_present"] = bool((cat_series == "TALENT").any())
        coh["talent_self_pin"] = (max(talent_rows) if talent_rows
                                  else None)
    try:
        try:
            from migration.self_property_coherence import must_pin_100
        except ImportError:
            from self_property_coherence import (  # type: ignore
                must_pin_100,
            )
        own_pin_misses = []
        for idx in df.index:
            cu = _norm_cat(df.at[idx, cols["cat"]])
            if cu in META_CATS or cu in DEMO_CATS or cu in FAN_CATS:
                continue
            bp = _num(df.at[idx, cols["bp"]])
            if bp is None or abs(bp - 100.0) <= 0.00005:
                continue
            val = str(df.at[idx, cols["val"]]).strip()
            if must_pin_100(subject, cu, val):
                own_pin_misses.append(f"{cu} / {val} at {bp:.4f}")
        coh["own_property_pin_misses"] = own_pin_misses[:8]
    except Exception:
        coh["own_property_pin_misses"] = None
    facts["coherence"] = coh

    # BRAND INPUT slug sanity.
    bi = _meta_value(df, cols, "BRAND INPUT")
    bi_facts = {"value": bi[:600]}
    bi_facts["has_apostrophe"] = bool(
        re.search(r"[\u2018\u2019\u02bc'`]", bi))
    bi_facts["label_like"] = bi.strip().upper() in {
        "MOVIE", "PODCAST", "SERIES", "GAME", "GAMES", "BRAND",
        "TALENT", "CONTENT", "PLATFORM",
    }
    generic_hits = []
    try:
        try:
            from migration.viewer_carriage import is_generic_landing_url
        except ImportError:
            from viewer_carriage import (  # type: ignore
                is_generic_landing_url,
            )
        for tok in [t.strip() for t in bi.split(",") if t.strip()]:
            if "/" in tok or re.match(
                    r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$", tok):
                if is_generic_landing_url(
                        tok, require_platform_domain=("/" not in tok)):
                    generic_hits.append(tok)
    except Exception:
        pass
    bi_facts["generic_landing_hits"] = generic_hits[:5]
    facts["brand_input"] = bi_facts

    # Naming + sample messiness.
    naming = {"deliverable": base, "kind": "cut" if is_cut else "TU"}
    head = base.split(" - ", 1)[0]
    naming["comma_variant_leak"] = "," in head
    facts["naming"] = naming
    sample = facts["sample"]
    if sample:
        facts["sample_messy_ok"] = (sample % 10 != 0)
    return facts


# ---------------------------------------------------------------------------
# The consolidated reasoning call
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the final audience-quality reviewer for a measurement \
company. A profile file has passed every mechanical check and is about to be \
delivered. Your job is the judgment layer: does every number make sense for \
THIS audience, given its demographic composition and what published research \
says about the real world?

You follow the Crosswalk Audience Vetting Framework:

1. ENGAGER DEFINITION. Each file profiles digital engagers: people with at \
least one digital touchpoint for the subject in a trailing 12-month window \
(search, social, media, eCommerce, owned channels). Engagers index AT OR \
ABOVE the general population on digital behaviors. Streaming video, \
streaming music, gaming, and vMVPD/FAST grids are measured as viewers / \
listeners / players, benchmarked from platform-reported subscriber and \
active-user figures.

2. APPROVED BENCHMARK SOURCES: SEC filings and 10-K disclosures, earnings \
reports, Federal Reserve SCF/SHED, Pew Research, Statista, eMarketer, YouGov, \
app analytics (Sensor Tower, data.ai), MRI-Simmons digital cuts. NEVER use: \
annual store visits, in-store visit counts, household device penetration, \
cable reach, total brand reach that mixes digital and offline, POS \
transaction data.

3. FACE VALIDITY PER CATEGORY. For each category you are shown, judge \
whether the values are plausible for this audience's demographic \
composition. The canonical failure: a mid-income, 35-54, heavily female US \
audience showing Visa at index 70 vs the general population. Per the Federal \
Reserve SHED (82% of US adults hold a credit card) and Statista network \
shares, that demographic holds and uses Visa at or above the general \
population. Index 70 on Visa there is a data defect, not a signal. \
Deviations ARE acceptable when the composition explains them: an audience \
that is 40% under 18 legitimately depresses credit, banking, and insurance \
grids; a 65+ audience legitimately depresses TikTok.

4. VERDICT DISCIPLINE. Be conservative. Flag a row only when it is CLEARLY \
wrong for this audience, not merely a few points off your intuition. \
PASS when everything is defensible. BORDERLINE when something deviates \
5-10 index points beyond what composition explains, or a review-worthy \
pattern exists that does not block delivery. FAIL only for material, \
demographic-inexplicable deviations or integrity findings.

4b. REASONING WINS WHEN ROOTED IN FACT (Jenna, 2026-08-26). Published \
consensus figures and the benchmark bands below are a BASE, never a \
target. Your job is the per-audience call: does the consensus apply to \
THIS audience? A reasoned call that cites concrete, verifiable facts \
(a researched anchor from an approved source, the file's demographic \
composition, the subject's competitive context, carriage reality) \
OVERRIDES the band. A call with no citable factual basis does NOT \
override anything: it defers to the default and lands as a borderline \
review item. Mathematical invariants are outside your jurisdiction and \
stay absolute (subset coherence, demo sums, BP ceiling, self-pins); \
everything statistical is a default your fact-rooted reasoning may beat.

4c. DIRECTION AND INTENSITY. The canonical defect is the DEPRESSION: a \
value materially BELOW what the audience's composition supports (the Visa \
case). But direction alone decides nothing; the fact pattern does. \
Engaged audiences legitimately run hot on engagement grids (streaming, \
social, media, fan-adjacent brands), and derived intensity cuts (Avid Fan \
and similar) legitimately index 150-300 there: that is a fact-explained \
high read, leave it untouched (info, or borderline at most). A high read \
is a defect only when a concrete fact makes it wrong for this audience, \
and then the correct fix is a reasoned TRIM with the fact cited.

4d. COMPETITIVE EXCLUSIVITY. When the universe is scoped to a brand \
(customers, subscribers, members, buyers, or switchers of X), rival \
brands in the SAME category are EXPECTED to read below general-population \
consensus in single-homing categories: primary banking, wireless carrier, \
home insurer, internet provider (seed grids: BANKING, TELECOM, \
INSURANCE, SECURITY; reason beyond the seed when the facts call for it). \
A Bank of \
America customer is not likely also a Chase customer; Chase depressed on \
a Bank of America universe is truth, never a defect, never a raise. The \
converse also holds: a single-homing rival sitting AT general-population \
consensus on a brand-scoped universe is an over-read, and a fact-rooted \
trim is the correct call. Multi-homing categories (streaming, QSR, social \
media, betting, credit-card networks like Visa/Mastercard co-holding) are \
different: rival co-usage is normal and can legitimately be high.

4e. DEMOGRAPHIC ELIGIBILITY. Age- and income-gated products (American \
Express and premium cards, mortgages, investment platforms, LinkedIn) \
must be judged against the file's composition, not adult-population \
consensus. A tween/teen-skewing audience with near-zero Amex is CORRECT; \
never raise it. The same gated product reading at full adult consensus on \
a composition that cannot hold it is an over-read: a fact-rooted trim is \
the correct call. The Bethenny Frankel case was the opposite pattern (a \
mid-income 35-54 adult audience at index 70 on Visa, not composition- \
explainable): that is exactly when a raise is right.

5. FIXES. fixable=true means you are confident enough to re-level the row \
yourself, in EITHER direction, and every fix must be rooted in fact. \
Provide: the expected index band for THIS audience, a concrete corrected \
penetration value (fix_bp, in percent of this audience), the benchmark \
source you anchored on, and fact_basis: one sentence naming the concrete \
fact that justifies the move (the researched anchor and source class, the \
composition fact, or the competitive-context fact). A RAISE corrects a \
depression the composition cannot explain. A TRIM corrects an over-read \
that a concrete fact (eligibility gating, single-homing rivalry, carriage \
impossibility) makes wrong for this audience; never trim expected \
avid/fan intensity. A fix without a citable fact_basis will not be \
applied; it becomes a review item. For findings that require \
rebuild-level judgment (wrong audience definition, contaminated \
qualifier, structural artifacts you cannot re-level row by row), set \
fixable=false.

6. SYNTHETIC SIGNATURES. You are given the outputs of mechanical detectors \
(shared-suffix value ladders, cross-grid duplicate values, coverage gaps). \
Do not re-detect; judge. A ladder group above threshold that survived to \
this stage is a FAIL with fixable=false (the repair is a re-draw, not a \
re-level). A handful of shared suffixes below threshold is BORDERLINE at \
most. Cross-grid duplicates outside required mirror families are BORDERLINE \
unless systematic.

7. LANGUAGE. Write every "plain" string for a client reader: plain English, \
specific, no internal tooling or vendor or model names, no hedging \
boilerplate. Never use em dashes.

Output STRICT JSON only, no prose outside it:
{"verdict": "PASS"|"BORDERLINE"|"FAIL",
 "summary": "<2-3 sentences, client-safe>",
 "findings": [
   {"code": "<SHORT_CODE>",
    "severity": "fail"|"borderline"|"info",
    "category": "<grid>", "brand": "<row value>",
    "current_bp": <number or null>,
    "expected_index_band": [<lo>, <hi>] or null,
    "fix_bp": <number or null>,
    "benchmark": "<source, one line>",
    "fact_basis": "<the concrete fact justifying a fix, one line; \
required for every fixable=true finding>",
    "fixable": true|false,
    "plain": "<one sentence, client-safe>"}]}

A PASS verdict must still include findings entries (severity "info" or \
"borderline") for every nominated candidate you reviewed and cleared, \
stating why the composition supports the value."""


def _bench_context_block():
    """Static benchmark table rendered once per process per day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cached = _BENCH_CACHE.get(today)
    if cached:
        return cached
    lines = ["PUBLISHED BENCHMARK BANDS (index vs Gen Pop, mainstream "
             "US adult audience; advisory when the audience is "
             "youth-skewed):"]
    for cat, table in BENCHMARK_BANDS.items():
        if not isinstance(table, dict):
            continue
        for bn, (lo, hi, src) in table.items():
            lines.append(f"  {cat} / {bn}: [{lo}, {hi}]  ({src})")
    block = "\n".join(lines)
    _BENCH_CACHE.clear()
    _BENCH_CACHE[today] = block
    return block


_BENCH_CACHE: dict = {}

# Categories always shown in full; everything else is top-N summarized.
_FULL_DETAIL_CATS = set(
    k for k in BENCHMARK_BANDS
) | {"BANKS", "CREDIT PROVIDERS", "STREAMING VIDEO", "INSURANCE",
     "TELECOM", "VMVPD/FAST", "VIRTUAL MVPD FAST", "VIRTUAL MVPD/FAST"}
_TOP_N_OTHER = 12
_MAX_PROMPT_ROWS = 460


def _build_user_prompt(df, subject, s3_key, category, facts, genpop_map):
    cols = _detect_cols(df)
    parts = [
        f"SUBJECT: {subject}",
        f"DELIVERABLE: {_display_name(s3_key)} "
        f"({facts['naming'].get('kind', 'TU')})",
        f"BRAND CATEGORY: {(category or facts['coherence'].get('brand_category') or 'UNKNOWN')}",
        f"AUDIENCE SIZE: {facts.get('sample') or 'unknown'}",
        "COMPETITIVE CONTEXT: if the subject or deliverable name scopes "
        "this universe to a brand (customers, subscribers, members, "
        "buyers, switchers of X), apply rule 4d: same-category rivals "
        "in single-homing grids are expected to read low; never raise "
        "them, and a rival at general-population level there is itself "
        "suspect.",
    ]
    coh0 = facts.get("coherence", {})
    if coh0.get("is_cut"):
        parts.append(
            f"COHORT: this is a derived '{coh0.get('cut_label')}' cut of "
            f"a parent universe. Intensity cuts legitimately index "
            f"150-300 on engagement grids (streaming, social, media, "
            f"fan-adjacent brands); the bands below are anchored on full "
            f"universes, so on this file apply them to DEPRESSIONS only "
            f"and read above-band engagement as expected intensity.")
    demo_lines = ["DEMOGRAPHIC COMPOSITION:"]
    for cu, pairs in facts.get("demo_summary", {}).items():
        row = ", ".join(f"{label} {bp:.1f}" for label, bp in pairs[:8])
        demo_lines.append(f"  {cu}: {row}")
    parts.append("\n".join(demo_lines))
    parts.append(_bench_context_block())

    if facts.get("benchmark_candidates"):
        lines = ["NOMINATED OUT-OF-BAND ROWS (deterministic scan; judge "
                 "each against the composition):"]
        for c in facts["benchmark_candidates"][:20]:
            lines.append(
                f"  {c['category']} / {c['brand']}: pen {c['bp']:.4f}, "
                f"Gen Pop {c['genpop_bp']:.4f}, index {c['index']:.1f}, "
                f"expected band {c['expected_index_band']} "
                f"[{c.get('direction', '?')} band]"
                + (" [youth-skewed audience; band advisory]"
                   if c.get("youth_advisory") else ""))
        parts.append("\n".join(lines))

    det_lines = ["MECHANICAL DETECTOR CONTEXT:"]
    lad = facts.get("ladders", {})
    det_lines.append(
        f"  shared-suffix value ladders: {lad.get('n_flagged', 0)} row(s) "
        f"in {len(lad.get('groups', []))} group(s) at/over threshold")
    for g in lad.get("groups", [])[:6]:
        det_lines.append(
            f"    {g['category']}: suffix .{g['suffix']} x{g['count']} "
            f"(threshold {g['threshold']})")
    dupes = facts.get("cross_grid_dupes", [])
    det_lines.append(f"  cross-grid duplicate values outside mirror "
                     f"families: {len(dupes)}")
    for d in dupes[:6]:
        det_lines.append(f"    {d['brand_norm']} at {d['bp']:.4f} in "
                         f"{', '.join(d['categories'])}")
    gg = facts.get("genpop_gaps", {})
    det_lines.append(
        f"  rows with penetration but no Gen Pop baseline: "
        f"{gg.get('n_missing', 0)} of {gg.get('n_brand_rows', 0)}")
    for ex in gg.get("examples", [])[:8]:
        det_lines.append(f"    {ex}")
    coh = facts.get("coherence", {})
    if coh.get("brand_category") in TALENT_FAMILY:
        det_lines.append(
            f"  TALENT self-inclusion: grid present="
            f"{coh.get('talent_grid_present')}, subject row at "
            f"{coh.get('talent_self_pin')}")
    if coh.get("own_property_pin_misses"):
        det_lines.append("  own-property rows NOT at 100: "
                         + "; ".join(coh["own_property_pin_misses"]))
    bi = facts.get("brand_input", {})
    det_lines.append(f"  BRAND INPUT: {bi.get('value', '')[:300]}")
    if bi.get("generic_landing_hits"):
        det_lines.append("  BRAND INPUT generic landing pages: "
                         + ", ".join(bi["generic_landing_hits"]))
    if bi.get("has_apostrophe"):
        det_lines.append("  BRAND INPUT contains apostrophes "
                         "(not clickstream-plausible)")
    if bi.get("label_like"):
        det_lines.append("  BRAND INPUT is a bare category label "
                         "(defect)")
    if facts.get("naming", {}).get("comma_variant_leak"):
        det_lines.append("  deliverable name carries a comma variant "
                         "list (naming defect)")
    if facts.get("sample") and not facts.get("sample_messy_ok", True):
        det_lines.append("  audience size ends in 0 (implausibly round)")
    parts.append("\n".join(det_lines))

    # Category row blocks: full detail for high-stakes grids, top-N
    # for the rest, capped overall.
    cat_series = df[cols["cat"]].astype(str).map(_norm_cat)
    blocks = []
    n_rows_used = 0
    seen_order = []
    for cu in cat_series:
        if cu not in seen_order:
            seen_order.append(cu)
    for cu in seen_order:
        if (not cu or cu in META_CATS or cu in DEMO_CATS
                or cu in FAN_CATS):
            continue
        if n_rows_used >= _MAX_PROMPT_ROWS:
            break
        rows = []
        for idx in df.index[cat_series == cu]:
            bp = _num(df.at[idx, cols["bp"]])
            if bp is None:
                continue
            brand = str(df.at[idx, cols["val"]]).strip()
            gp_hit = _gp_get(genpop_map, cu, _norm_brand(brand)) \
                if genpop_map else None
            idx_s = ""
            if gp_hit and gp_hit[0]:
                idx_s = f", idx {bp / gp_hit[0] * 100.0:.0f}"
            rows.append((bp, f"  {brand}: {bp:.4f}{idx_s}"))
        if not rows:
            continue
        rows.sort(key=lambda t: -t[0])
        full = cu in _FULL_DETAIL_CATS
        keep = rows if full else rows[:_TOP_N_OTHER]
        keep = keep[:max(0, _MAX_PROMPT_ROWS - n_rows_used)]
        if not keep:
            break
        n_rows_used += len(keep)
        head = f"CATEGORY {cu} ({len(rows)} rows" + \
               ("" if full else f"; top {len(keep)} shown") + "):"
        blocks.append("\n".join([head] + [line for _, line in keep]))
    parts.append("\n\n".join(blocks))
    parts.append(
        "Review this file per the framework. STRICT JSON only.")
    return "\n\n".join(parts)


def _default_claude_call(system, user):
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
            print(f"[pre-ship-vetting] reasoning call raised "
                  f"({type(e).__name__}: {e}); trying next variant")
            raw = ""
        if raw and raw.strip():
            break
    return raw


def _parse_verdict(raw):
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text,
                  flags=re.MULTILINE)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except Exception:
            continue
        if (isinstance(obj, dict)
                and str(obj.get("verdict", "")).upper()
                in ("PASS", "BORDERLINE", "FAIL")):
            obj["verdict"] = str(obj["verdict"]).upper()
            if not isinstance(obj.get("findings"), list):
                obj["findings"] = []
            return obj
    return None


# ---------------------------------------------------------------------------
# Autofix (deterministic application of benchmark-backed fixes)
# ---------------------------------------------------------------------------

def _write_cell_like(df, idx, col, new_float, decimals=4, as_count=False):
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


def _fact_basis(finding):
    """The concrete fact a fix cites (fact_basis field). Raises without
    one still apply under the benchmark-band discipline (the band table
    itself carries the published source); trims and out-of-band moves
    require it."""
    basis = str(finding.get("fact_basis") or "").strip()
    return basis if len(basis) >= 12 else ""


def _fix_sanity(finding, cur_bp, genpop_bp):
    """Validate a proposed fix. Returns (ok, corrected_target or reason).
    The reasoner proposes; this disposes.

    Per Jenna 2026-08-26 ("reasoning should always win when rooted in
    fact"): fixes are bidirectional. The band is a DEFAULT, not a cage:
    a fix that cites a concrete fact_basis may land outside the band
    (eligibility-gated products on ineligible compositions, single-
    homing rivals on brand-scoped universes legitimately sit far below
    it). A fix with NO citable factual basis never overrides anything:
    raises without a basis fall back to the benchmark band discipline,
    downward fixes without a basis are rejected outright. Mathematical
    plausibility (range, bounded move, non-noop) stays absolute."""
    fix = _num(finding.get("fix_bp"))
    if fix is None:
        return False, "no fix value"
    if not (0.01 <= fix <= 99.49):
        return False, f"fix {fix} outside plausible range"
    basis = _fact_basis(finding)
    if cur_bp is not None and abs(fix - cur_bp) < 0.005:
        return False, "fix is a no-op"
    if cur_bp is not None and fix < cur_bp and not basis:
        # No-fact-no-override: a trim must cite the concrete fact
        # (eligibility gate, single-homing rivalry, carriage reality).
        return False, "downward fix without a factual basis"
    if cur_bp is not None and abs(fix - cur_bp) > MAX_FIX_MOVE_PP:
        return False, f"fix moves {abs(fix - cur_bp):.1f}pp (cap {MAX_FIX_MOVE_PP})"
    band = finding.get("expected_index_band")
    if (not basis and isinstance(band, (list, tuple)) and len(band) == 2
            and genpop_bp and genpop_bp > 0):
        lo, hi = _num(band[0]), _num(band[1])
        if lo is not None and hi is not None and lo < hi:
            fix_idx = fix / genpop_bp * 100.0
            cur_idx = (cur_bp / genpop_bp * 100.0
                       if cur_bp is not None else None)
            if not (lo * 0.88 <= fix_idx <= hi * 1.12):
                return False, (f"fix index {fix_idx:.1f} outside band "
                               f"[{lo}, {hi}]")
            if cur_idx is not None:
                if cur_idx < lo and fix_idx <= cur_idx:
                    return False, "fix does not move toward band"
                if cur_idx > hi and fix_idx >= cur_idx:
                    return False, "fix does not move toward band"
    return True, fix


def _apply_fixes(df, findings, subject, genpop_map, verbose=True):
    """Apply sanity-passing benchmark fixes in place. Returns
    (df, applied:list, rejected:list)."""
    cols = _detect_cols(df)
    sample = _extract_sample(df, cols)
    applied, rejected = [], []
    cat_series = df[cols["cat"]].astype(str).map(_norm_cat)
    for f in findings:
        cu = _norm_cat(f.get("category"))
        bn = _norm_brand(f.get("brand"))
        if not cu or not bn:
            rejected.append((f, "no category/brand"))
            continue
        if cu in META_CATS or cu in DEMO_CATS or cu in FAN_CATS:
            rejected.append((f, "protected category"))
            continue
        hit_idx = None
        for idx in df.index[cat_series == cu]:
            if _norm_brand(df.at[idx, cols["val"]]) == bn:
                hit_idx = idx
                break
        if hit_idx is None:
            rejected.append((f, "row not found"))
            continue
        cur = _num(df.at[hit_idx, cols["bp"]])
        if cur is not None and abs(cur - 100.0) <= 0.00005:
            rejected.append((f, "self-pin row"))
            continue
        gp_hit = _gp_get(genpop_map, cu, bn) if genpop_map else None
        ok, res = _fix_sanity(f, cur, gp_hit[0] if gp_hit else None)
        if not ok:
            rejected.append((f, res))
            continue
        target = float(res)
        # Subject-salted jitter so the fix never lands on a shared or
        # round value; re-drawn until off any 2dp boundary.
        j = (_unit(f"vetting-fix|{subject}|{cu}|{bn}") - 0.5) * 0.08
        newv = round(max(0.0102, min(99.4899, target + j)), 4)
        tries = 0
        while (_on_2dp_boundary(newv) or (cur is not None
               and abs(newv - cur) < 0.00005)) and tries < 9:
            tries += 1
            newv = round(newv + 0.0013 * (tries + 1), 4)
        _write_cell_like(df, hit_idx, cols["bp"], newv)
        if sample:
            raw_v = round(newv / 100.0 * sample)
            if cols["raw"] is not None:
                _write_cell_like(df, hit_idx, cols["raw"], raw_v,
                                 as_count=True)
            if cols["proj"] is not None:
                _write_cell_like(df, hit_idx, cols["proj"],
                                 round(raw_v / PANEL_DENOM * US_POP),
                                 as_count=True)
        applied.append({
            "category": cu, "brand": str(f.get("brand")),
            "old_bp": cur, "new_bp": newv,
            "direction": ("trim" if cur is not None and newv < cur
                          else "raise"),
            "benchmark": str(f.get("benchmark") or "")[:200],
            "fact_basis": str(f.get("fact_basis") or "")[:300],
        })
        if verbose:
            print(f"[pre-ship-vetting] fix applied: {cu} / "
                  f"{f.get('brand')}: {cur} -> {newv:.4f}")
    return df, applied, rejected


# ---------------------------------------------------------------------------
# Hold flow + ledger
# ---------------------------------------------------------------------------

def _quarantine_bytes(body, s3_key, s3_client, verbose):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = _display_name(s3_key) or "profile"
    qkey = f"{QUARANTINE_PREFIX}{base}.vetting_hold_{ts}.csv"
    try:
        _s3(s3_client).put_object(Bucket=BUCKET, Key=qkey, Body=body,
                                  ContentType="text/csv")
        if verbose:
            print(f"[pre-ship-vetting] held copy saved to "
                  f"s3://{BUCKET}/{qkey}")
        return qkey
    except Exception as e:
        print(f"[pre-ship-vetting] quarantine write failed for "
              f"{s3_key}: {e}")
        return None


def _email_hold_notice(s3_key, findings, quarantine_key, verbose):
    name = _display_name(s3_key)
    lines = [
        f"The file {name} was held before delivery: the final audience "
        f"review found {len(findings)} item(s) that need a judgment "
        f"call before it can publish.",
        "",
        "It was NOT published to the dashboard.",
    ]
    if quarantine_key:
        lines.append(f"A copy is saved for review at {quarantine_key}.")
    lines.append("")
    lines.append("What was found:")
    for i, f in enumerate(findings[:25], start=1):
        plain = str(f.get("plain") or "").strip()
        where = f"{f.get('category', '')} / {f.get('brand', '')}".strip(" /")
        lines.append(f"  {i}. {plain or where}")
    if len(findings) > 25:
        lines.append(f"  ... and {len(findings) - 25} more.")
    lines += [
        "",
        "Next step: review the held copy. Once the underlying issue is "
        "addressed, rerun the build and the file will publish "
        "automatically when every check passes.",
    ]
    payload = {
        "subject_line": f"Profile held for review: {name}",
        "body": "\n".join(lines),
        "to": list(HOLD_NOTICE_TO),
        "source": HOLD_NOTICE_FROM,
    }
    # Debounced delivery (Jenna 2026-08-27: "yes I only want real
    # emails not gate blocks, just if the final cannnot ship"). A
    # judgment hold an agent or the machinery resolves inside the
    # window (republish of the same deliverable) never emails; one that
    # persists is exactly the final-cannot-ship case and sends once.
    # On any recording failure the notice sends immediately (fail-safe).
    try:
        try:
            from migration.hold_notice_debounce import record_pending
        except ImportError:
            from hold_notice_debounce import record_pending  # type: ignore
        disposition = record_pending(
            s3_key, "vetting_hold", payload,
            quarantine_key=quarantine_key, n_findings=len(findings),
            verbose=verbose,
        )
        if verbose:
            print(f"[pre-ship-vetting] hold notice {disposition} "
                  f"(debounced delivery)")
        return
    except Exception as e:
        print(f"[pre-ship-vetting] hold-notice debounce unavailable "
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
            print(f"[pre-ship-vetting] hold notice emailed to "
                  f"{', '.join(HOLD_NOTICE_TO)}")
    except Exception as e:
        print(f"[pre-ship-vetting] hold notice email failed: {e}")


def _ledger_append(entry, s3_client, verbose=True):
    """Append one JSON line to today's review ledger. Read-modify-write
    is fine at this volume; failures never block a publish."""
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"{LEDGER_PREFIX}{day}.jsonl"
        s3c = _s3(s3_client)
        try:
            prior = s3c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        except Exception:
            prior = b""
        line = json.dumps(entry, default=str,
                          ensure_ascii=False).encode("utf-8")
        s3c.put_object(Bucket=BUCKET, Key=key,
                       Body=prior + line + b"\n",
                       ContentType="application/x-ndjson")
        if verbose:
            print(f"[pre-ship-vetting] ledger appended -> {key}")
    except Exception as e:
        print(f"[pre-ship-vetting] ledger append failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_pre_ship_vetting(df, subject, s3_key, *, category=None,
                         s3_client=None,
                         claude_call: Optional[Callable] = None,
                         genpop_map=None, enforce=True, is_new=None,
                         sort_fn=None, ledger=True, verbose=True):
    """Reasoned pre-publish review. Returns (df, report).

    enforce=True: a FAIL verdict with judgment-required findings
    quarantines the frame, records a debounced hold notice (emails only
    if the hold outlives the window; see hold_notice_debounce), and
    raises PreShipVettingError (a ShipGateError subclass). enforce=False
    (audits, dry runs, local ops override) reports without holding.

    is_new: True forces the review (cut engines: a re-derived cut is
    new reasoning even on an existing key); False skips it; None
    auto-detects by key existence (existing key = in-place correction
    of already-reviewed content = skip).

    Infrastructure failures (reasoner unreachable, unparseable output)
    fail OPEN with a loud log and a ledger entry: the mechanical gate
    already passed, and an outage must not wedge every publish.
    """
    t0 = time.time()
    report = {"ran": False, "verdict": None, "subject": subject,
              "s3_key": s3_key, "findings": [], "autofix": [],
              "skipped": None}
    try:
        if df is None or len(df) == 0:
            report["skipped"] = "empty frame"
            return df, report
        if not _env_enabled():
            report["skipped"] = "disabled by PRE_SHIP_VETTING env"
            print("[pre-ship-vetting] DISABLED via PRE_SHIP_VETTING "
                  "env; publishing without the reasoned review")
            return df, report
        key = str(s3_key or "")
        base = os.path.basename(key)
        if "/" in key:
            report["skipped"] = f"non-root key ({key.split('/')[0]}/)"
            return df, report
        if re.match(r"(?i)^gen[_\s]?pop", base):
            report["skipped"] = "Gen Pop baseline file"
            return df, report
        s3c = None
        if is_new is None:
            try:
                s3c = _s3(s3_client)
                if _key_exists(key, s3c):
                    report["skipped"] = ("existing key (in-place "
                                         "correction)")
                    if verbose:
                        print(f"[pre-ship-vetting] {base}: existing "
                              f"key; in-place corrections skip the "
                              f"reasoned review")
                    return df, report
            except Exception:
                pass
        elif is_new is False:
            report["skipped"] = "caller marked not-new"
            return df, report
        if s3c is None:
            try:
                s3c = _s3(s3_client)
            except Exception:
                s3c = None

        gp_map = _load_genpop(genpop_map, s3c, verbose)
        facts = _deterministic_prescan(df, subject, key, gp_map,
                                       verbose=verbose)
        report["prescan"] = {
            "benchmark_candidates": len(facts["benchmark_candidates"]),
            "genpop_missing": facts["genpop_gaps"]["n_missing"],
            "genpop_brand_rows": facts["genpop_gaps"]["n_brand_rows"],
            "ladder_rows": facts["ladders"]["n_flagged"],
            "cross_grid_dupes": len(facts["cross_grid_dupes"]),
        }

        user = _build_user_prompt(df, subject, key, category, facts,
                                  gp_map)
        call = claude_call or _default_claude_call
        try:
            raw = call(_SYSTEM_PROMPT, user)
        except Exception as e:
            raw = ""
            report["error"] = f"reasoning call raised: {e}"
        verdict_obj = _parse_verdict(raw)
        if verdict_obj is None:
            report["skipped"] = "reasoner unavailable or unparseable"
            print(f"⚠️ [pre-ship-vetting] {base}: reasoned review "
                  f"unavailable; publishing on the mechanical gate "
                  f"alone (loud fail-open)")
            if ledger:
                _ledger_append({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "s3_key": key, "subject": subject,
                    "verdict": "SKIPPED_REASONER_UNAVAILABLE",
                    "prescan": report["prescan"],
                }, s3c, verbose=verbose)
            return df, report

        report["ran"] = True
        verdict = verdict_obj["verdict"]
        findings = [f for f in verdict_obj.get("findings", [])
                    if isinstance(f, dict)]
        report["summary"] = str(verdict_obj.get("summary") or "")[:600]
        report["findings"] = findings

        # Direction guard, fact-conditional (Jenna 2026-08-26:
        # "reasoning should always win when rooted in fact"). An
        # above-band fail or a proposed trim WITHOUT a citable factual
        # basis downgrades to borderline: engaged audiences and derived
        # cuts legitimately run hot, and an unfounded trim must never
        # apply. When the reasoner cites a concrete fact_basis
        # (eligibility gating, single-homing rivalry, carriage
        # impossibility), the reasoned call stands and the fix flows to
        # the sanity gate like any other. Structural findings without a
        # band (ladders, contamination, coherence) keep their severity.
        n_intensity_downgrades = 0
        for f in findings:
            if str(f.get("severity", "")).lower() != "fail":
                continue
            if _fact_basis(f):
                continue
            band = f.get("expected_index_band")
            if not (isinstance(band, (list, tuple)) and len(band) == 2):
                continue
            hi = _num(band[1])
            cur = _num(f.get("current_bp"))
            fixv = _num(f.get("fix_bp"))
            above = False
            if fixv is not None and cur is not None and fixv < cur:
                above = True
            elif cur is not None and hi is not None:
                gp_hit = _gp_get(gp_map, _norm_cat(f.get("category")),
                                 _norm_brand(f.get("brand")))
                if gp_hit and gp_hit[0]:
                    above = (cur / gp_hit[0] * 100.0) > hi
            if above:
                f["severity"] = "borderline"
                f["fixable"] = False
                f["fix_bp"] = None
                n_intensity_downgrades += 1
        if n_intensity_downgrades:
            report["intensity_downgrades"] = n_intensity_downgrades
            if verdict == "FAIL" and not any(
                    str(f.get("severity", "")).lower() == "fail"
                    for f in findings):
                verdict = "BORDERLINE"

        # A PASS that ignored a nominated out-of-band candidate is not
        # a clean pass: downgrade to BORDERLINE so the ledger sees it.
        if verdict == "PASS" and facts["benchmark_candidates"]:
            addressed = {(_norm_cat(f.get("category")),
                          _norm_brand(f.get("brand")))
                         for f in findings}
            unaddressed = [
                c for c in facts["benchmark_candidates"]
                if (_norm_cat(c["category"]), _norm_brand(c["brand"]))
                not in addressed
            ]
            if unaddressed:
                verdict = "BORDERLINE"
                report["downgrade"] = (
                    f"{len(unaddressed)} nominated row(s) not "
                    f"addressed by the review")

        # Standing coverage-gap finding (audit gap 5): visible in the
        # ledger even when the reasoner did not comment. Brands in our
        # brand universe self-heal via the daily Gen Pop sync.
        gg = facts["genpop_gaps"]
        if gg["n_missing"] > max(25, int(0.03 * max(1, gg["n_brand_rows"]))):
            if verdict == "PASS":
                verdict = "BORDERLINE"
                report.setdefault("downgrade", "")
            report["findings"] = findings + [{
                "code": "BASELINE_COVERAGE",
                "severity": "borderline",
                "category": "(file-wide)", "brand": "",
                "fixable": False,
                "plain": (f"{gg['n_missing']} of {gg['n_brand_rows']} "
                          f"rows have no US baseline for indexing yet; "
                          f"they gain one on the next daily baseline "
                          f"refresh."),
            }]
            findings = report["findings"]

        report["verdict"] = verdict
        fail_findings = [f for f in findings
                         if str(f.get("severity", "")).lower() == "fail"]

        if verdict == "FAIL" and fail_findings:
            fixable = [f for f in fail_findings if f.get("fixable")]
            judgment = [f for f in fail_findings if not f.get("fixable")]
            if not judgment and 0 < len(fixable) <= MAX_AUTOFIX_ROWS:
                df = df.copy()
                # Strip baseline columns so the fixes and the chain
                # recompute see the canonical frame; re-appended by the
                # caller's terminal step.
                try:
                    try:
                        from migration.genpop_baseline import (
                            strip_genpop_columns,
                        )
                    except ImportError:
                        from genpop_baseline import (  # type: ignore
                            strip_genpop_columns,
                        )
                    df = strip_genpop_columns(df)
                except Exception:
                    pass
                df, applied, rejected_fixes = _apply_fixes(
                    df, fixable, subject, gp_map, verbose=verbose)
                report["autofix"] = applied
                report["autofix_rejected"] = [
                    {"finding": f.get("code"), "reason": r}
                    for f, r in rejected_fixes]
                if rejected_fixes and not applied:
                    # Every proposed fix failed sanity: this is a
                    # judgment hold, not an autofix.
                    judgment = fixable
                else:
                    # Recompute the chain from the corrected BPs and
                    # re-run the mechanical gate on the fixed bytes.
                    try:
                        try:
                            from migration.post_generation_enforcers \
                                import run_write_safety_net
                        except ImportError:
                            from post_generation_enforcers import (  # type: ignore
                                run_write_safety_net,
                            )
                        df, _ = run_write_safety_net(df, subject,
                                                     verbose=False)
                    except Exception as e:
                        print(f"[pre-ship-vetting] post-fix safety net "
                              f"raised ({type(e).__name__}: {e})")
                    if sort_fn is None:
                        try:
                            try:
                                from migration.profile_writer import (
                                    _sort_within_category as sort_fn,
                                )
                            except ImportError:
                                from profile_writer import (  # type: ignore
                                    _sort_within_category as sort_fn,
                                )
                        except Exception:
                            sort_fn = None
                    if sort_fn is not None:
                        try:
                            df = sort_fn(df)
                        except Exception:
                            pass
                    try:
                        try:
                            from migration.final_ship_gate import (
                                run_final_ship_gate,
                            )
                        except ImportError:
                            from final_ship_gate import (  # type: ignore
                                run_final_ship_gate,
                            )
                        buf = io.StringIO()
                        df.to_csv(buf, index=False)
                        run_final_ship_gate(
                            buf.getvalue().encode("utf-8"), key,
                            subject, enforce=enforce, s3_client=s3c,
                            verbose=verbose,
                        )
                    except ImportError:
                        pass
                    report["verdict"] = "FAIL_AUTOFIXED"
                    if verbose:
                        print(f"[pre-ship-vetting] {base}: FAIL with "
                              f"{len(applied)} benchmark-backed fix(es) "
                              f"applied; re-checked and publishing")
            if judgment or (verdict == "FAIL"
                            and len(fixable) > MAX_AUTOFIX_ROWS):
                hold = judgment or fail_findings
                report["verdict"] = "FAIL_HELD"
                if ledger:
                    _ledger_append({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "s3_key": key, "subject": subject,
                        "verdict": "FAIL_HELD",
                        "summary": report.get("summary"),
                        "findings": findings[:40],
                        "prescan": report["prescan"],
                        "elapsed_s": round(time.time() - t0, 1),
                    }, s3c, verbose=verbose)
                if enforce:
                    buf = io.StringIO()
                    df.to_csv(buf, index=False)
                    qkey = _quarantine_bytes(
                        buf.getvalue().encode("utf-8"), key, s3c,
                        verbose)
                    _email_hold_notice(key, hold, qkey, verbose)
                    raise PreShipVettingError(key, hold,
                                              quarantine_key=qkey)
                return df, report

        if ledger:
            _ledger_append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "s3_key": key, "subject": subject,
                "verdict": report["verdict"],
                "summary": report.get("summary"),
                "findings": [f for f in findings
                             if str(f.get("severity", "")).lower()
                             != "info"][:40],
                "autofix": report.get("autofix", [])[:40],
                "prescan": report["prescan"],
                "elapsed_s": round(time.time() - t0, 1),
            }, s3c, verbose=verbose)
        if verbose:
            print(f"[pre-ship-vetting] {base}: verdict "
                  f"{report['verdict']} "
                  f"({len(findings)} finding(s), "
                  f"{time.time() - t0:.0f}s)")
        return df, report
    except PreShipVettingError:
        raise
    except Exception as e:
        print(f"⚠️ [pre-ship-vetting] internal error "
              f"({type(e).__name__}: {e}) for {subject!r}; publishing "
              f"on the mechanical gate alone")
        report["error"] = f"internal: {e}"
        return df, report


def vet_before_publish(df, body, subject, s3_key, *, category=None,
                       s3_client=None, enforce=True, sort_fn=None,
                       verbose=True):
    """Bytes-aware wrapper for profile_writer: takes the serialized
    body the mechanical gate just approved, runs the reasoned review,
    and re-serializes when an autofix changed the frame. Returns
    (df, body, report). PreShipVettingError propagates; every other
    failure returns the inputs unchanged."""
    try:
        df2, report = run_pre_ship_vetting(
            df, subject, s3_key, category=category, s3_client=s3_client,
            enforce=enforce, sort_fn=sort_fn, verbose=verbose,
        )
    except PreShipVettingError:
        raise
    except Exception as e:
        print(f"[pre-ship-vetting] wrapper error "
              f"({type(e).__name__}: {e}); publishing original bytes")
        return df, body, {"error": str(e)}
    if report.get("autofix"):
        # Re-append the Gen Pop baseline columns the writer added at
        # step 6.5 (the fix pass stripped them), then re-serialize.
        try:
            try:
                from migration.genpop_baseline import (
                    append_genpop_columns,
                )
            except ImportError:
                from genpop_baseline import (  # type: ignore
                    append_genpop_columns,
                )
            df2 = append_genpop_columns(df2, s3_client=s3_client,
                                        verbose=False)
        except Exception as e:
            print(f"[pre-ship-vetting] baseline re-append skipped: {e}")
        buf = io.StringIO()
        df2.to_csv(buf, index=False)
        return df2, buf.getvalue().encode("utf-8"), report
    return df, body, report


if __name__ == "__main__":
    # Read-only audit CLI: real reasoning call, no holds, no ledger,
    # no S3 writes. Accepts S3 keys (bucket root or _backups/) and
    # local CSV paths.
    import sys as _sys

    import pandas as _pd

    _args = [a for a in _sys.argv[1:] if not a.startswith("-")]
    if not _args:
        print("usage: python3 -m migration.pre_ship_vetting "
              "<s3_key_or_local_csv> [...]")
        raise SystemExit(2)
    _s3c = None
    for _a in _args:
        if os.path.exists(_a):
            _df = _pd.read_csv(_a, dtype=str, keep_default_na=False)
            _key = os.path.basename(_a)
        else:
            import boto3 as _b3
            _s3c = _s3c or _b3.client("s3", region_name="us-east-2")
            _body = _s3c.get_object(Bucket=BUCKET, Key=_a)["Body"].read()
            _df = _pd.read_csv(io.BytesIO(_body), dtype=str,
                               keep_default_na=False)
            _key = os.path.basename(_a)
        _base = _display_name(_key)
        _subj = re.sub(r"[_\s]\d{2}[_\s]\d{2}[_\s]\d{4}.*$", "",
                       _base.split(" - ")[0])
        _subj = re.sub(r"\.pre_.*$", "", _subj)
        _subj = re.sub(r"[_\s]\d{2}[_\s]\d{2}[_\s]\d{4}.*$", "",
                       _subj).replace("_", " ").strip()
        print(f"\n=== {_a} (subject={_subj!r}) ===")
        _t0 = time.time()
        _, _report = run_pre_ship_vetting(
            _df, _subj, _key, enforce=False, is_new=True,
            ledger=False, verbose=True,
        )
        print(f"--- verdict: {_report.get('verdict')} "
              f"({time.time() - _t0:.0f}s)")
        print(json.dumps({
            "verdict": _report.get("verdict"),
            "summary": _report.get("summary"),
            "prescan": _report.get("prescan"),
            "downgrade": _report.get("downgrade"),
            "findings": _report.get("findings", [])[:20],
            "autofix": _report.get("autofix", [])[:20],
        }, indent=2, default=str, ensure_ascii=False))
