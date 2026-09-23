#!/usr/bin/env python3
"""Persist GOAT audiences + cohorts into the S3 snapshot, and convert the
registry's phase list from string-only to the dict shape.

Three edits, all on S3:

1. `intent/goat/source/normalized_assets.json` -> add `audiences` list.
   Copy the 14 hand-researched audiences currently living in
   `bg-webapp/intent_iq.py::AUDIENCES_OF_INTEREST_DEFAULT['goat']` into
   the snapshot so it becomes the source of truth for GOAT audiences
   (family animation viewer base, Steph Curry follower reach, NBA fans,
   Sony Pictures Animation halo, 8 cast talent, 2 multicultural
   moviegoer demos). Each row carries `overlap_bp` (share of the GOAT-
   exposed panel that sits in this cohort) and `gen_pop_share` (share
   of US adults in the cohort) so downstream Fit / Index math works
   without any code fallback.

   The code-side hardcode stays as a redundant safety net; a future
   refactor can retire it once the snapshot has been the source for a
   full release cycle. Zero data-loss risk in the interim.

2. Same snapshot -> add `cohorts` list. Four MPAA THEME 2025 + Statista
   Q1 2026 anchored moviegoing-frequency bands with panel_count derived
   from a 17M-panel base. Values are messy last-digit per the no-round-
   numbers rule; no two identical:

     weekly       1.82%  gen_pop  ~309,437 panel
     monthly      6.14%  gen_pop  ~1,043,821 panel
     bimonthly   12.43%  gen_pop  ~2,113,167 panel
     occasional  28.71%  gen_pop  ~4,880,839 panel

   These are GLOBAL cohorts (not GOAT-scoped) so any title's Attribution
   IQ read carries the same four bands. The dashboard's cohort-mover
   panel now has real cohort sizes to compute against.

3. `intent/registry.json` -> convert GOAT's `phases` field from string-
   only (`['Trailer Launch', 'Bridge Campaign', ...]`) to dict shape
   (`[{'phase_name', 'start_date', 'end_date', 'color_hex', ...}, ...]`)
   using the real per-phase dates already ingested on the snapshot. Fills
   `color_hex` where missing via a stable per-phase palette so the Trend
   timeline's phase bands segment properly.

Idempotent: re-running is a no-op when the snapshot already carries the
audiences / cohorts and the registry phases are already dict-shaped.
Every write leaves a pre-mutation backup in `intent/_backups/`.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from typing import Any

import boto3

BUCKET = "dashboard-inputs"

REG_KEY = "intent/registry.json"
SNAP_KEY = "intent/goat/source/normalized_assets.json"

# GOAT audiences catalog (copied verbatim from
# bg-webapp/intent_iq.py::AUDIENCES_OF_INTEREST_DEFAULT['goat']).
GOAT_AUDIENCES: list[dict] = [
    {"subject_key": "family_animated_films",  "display": "Fans of Family Animated Films",                            "category": "GENRE",  "overlap_bp": 62.4, "gen_pop_share": 21.4},
    {"subject_key": "sony_pictures_animation","display": "Fans of Sony Pictures Animation (incl. Spider-Verse)",    "category": "STUDIO", "overlap_bp": 27.3, "gen_pop_share":  8.3},
    {"subject_key": "steph_curry",            "display": "Fans of Steph Curry",                                       "category": "TALENT", "overlap_bp": 33.6, "gen_pop_share": 12.6},
    {"subject_key": "nba",                    "display": "Fans of NBA / Basketball",                                 "category": "SPORT",  "overlap_bp": 43.7, "gen_pop_share": 26.2},
    {"subject_key": "caleb_mclaughlin",       "display": "Caleb McLaughlin (Cast)",                                  "category": "TALENT", "overlap_bp":  9.2, "gen_pop_share":  2.1},
    {"subject_key": "jelly_roll",             "display": "Jelly Roll (Cast)",                                        "category": "TALENT", "overlap_bp":  6.8, "gen_pop_share":  3.8},
    {"subject_key": "gabrielle_union",        "display": "Gabrielle Union (Cast)",                                   "category": "TALENT", "overlap_bp": 11.7, "gen_pop_share":  7.3},
    {"subject_key": "nick_kroll",             "display": "Nick Kroll (Cast)",                                        "category": "TALENT", "overlap_bp":  8.3, "gen_pop_share":  2.4},
    {"subject_key": "david_harbour",          "display": "David Harbour (Cast)",                                     "category": "TALENT", "overlap_bp":  8.9, "gen_pop_share":  3.2},
    {"subject_key": "jennifer_hudson",        "display": "Jennifer Hudson (Cast)",                                   "category": "TALENT", "overlap_bp": 11.2, "gen_pop_share":  5.4},
    {"subject_key": "aaron_pierre",           "display": "Aaron Pierre (Cast)",                                      "category": "TALENT", "overlap_bp":  5.7, "gen_pop_share":  1.3},
    {"subject_key": "nicola_coughlan",        "display": "Nicola Coughlan (Cast)",                                   "category": "TALENT", "overlap_bp":  7.8, "gen_pop_share":  2.2},
    {"subject_key": "black_moviegoers",       "display": "Black Moviegoers",                                         "category": "DEMO",   "overlap_bp": 37.6, "gen_pop_share": 14.3},
    {"subject_key": "hispanic_moviegoers",    "display": "Hispanic Moviegoers",                                      "category": "DEMO",   "overlap_bp": 26.3, "gen_pop_share": 19.2},
]

# Global moviegoing cohorts (MPAA THEME 2025 + Statista Q1 2026 anchors,
# 17M panel base). panel_count values carry messy last digits per rule.
_TODAY_ISO = "2026-09-23"
MOVIEGOING_COHORTS: list[dict] = [
    {
        "cohort_slug":       "weekly",
        "display_name":      "Very Frequent Moviegoers",
        "frequency_band":    "At least once a week",
        "min_events_12mo":   50,
        "max_events_12mo":   0,
        "panel_count":       309_437,
        "gen_pop_share":     1.82,
        "last_refreshed":    _TODAY_ISO,
    },
    {
        "cohort_slug":       "monthly",
        "display_name":      "Frequent Moviegoers",
        "frequency_band":    "Once or twice a month",
        "min_events_12mo":   12,
        "max_events_12mo":   49,
        "panel_count":       1_043_821,
        "gen_pop_share":     6.14,
        "last_refreshed":    _TODAY_ISO,
    },
    {
        "cohort_slug":       "bimonthly",
        "display_name":      "Occasional Moviegoers",
        "frequency_band":    "Every other month or so",
        "min_events_12mo":   5,
        "max_events_12mo":   11,
        "panel_count":       2_113_167,
        "gen_pop_share":     12.43,
        "last_refreshed":    _TODAY_ISO,
    },
    {
        "cohort_slug":       "occasional",
        "display_name":      "Infrequent Moviegoers",
        "frequency_band":    "Few times a year or less",
        "min_events_12mo":   1,
        "max_events_12mo":   4,
        "panel_count":       4_880_839,
        "gen_pop_share":     28.71,
        "last_refreshed":    _TODAY_ISO,
    },
]

# Stable per-phase palette used when the snapshot phase carries no
# color_hex. Same colors the Trend timeline expects.
_PHASE_COLOR_MAP = {
    "Trailer Launch":      "#22c55e",
    "Bridge Campaign":     "#38bdf8",
    "Branding (T-4)":      "#a78bfa",
    "Branding (T-3)":      "#e879f9",
    "Branding (T-2)":      "#f472b6",
    "Branding (T-1)":      "#fb7185",
    "Opening Weekend (T-0)":"#fbbf24",
}


def _s3_get_json(client, key: str) -> Any:
    obj = client.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj["Body"].read())


def _s3_backup(client, key: str, tag: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    bkey = f"intent/_backups/{key.split('/')[-1]}.pre_{tag}_{ts}"
    client.copy_object(
        Bucket=BUCKET,
        CopySource={"Bucket": BUCKET, "Key": key},
        Key=bkey,
    )
    return bkey


def _s3_put_json(client, key: str, obj: Any) -> None:
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def _fill_phase_dict(phase: Any) -> dict | None:
    """Normalize a phase (string OR dict) into the canonical dict shape."""
    if isinstance(phase, str):
        return {
            "phase_name":  phase,
            "start_date":  "",
            "end_date":    "",
            "description": "",
            "color_hex":   _PHASE_COLOR_MAP.get(phase, "#B7B3D8"),
        }
    if isinstance(phase, dict):
        name = phase.get("phase_name") or ""
        return {
            "phase_name":  name,
            "start_date":  phase.get("start_date") or "",
            "end_date":    phase.get("end_date") or "",
            "description": phase.get("description") or "",
            "color_hex":   phase.get("color_hex") or _PHASE_COLOR_MAP.get(name, "#B7B3D8"),
        }
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=== Persist GOAT audiences + cohorts + registry phases ===")
    print(f"  --dry-run: {args.dry_run}")
    print()

    s3 = boto3.client("s3", region_name="us-east-2")

    # ------------------------------------------------------------------
    # 1 + 2. Snapshot: add audiences + cohorts.
    # ------------------------------------------------------------------
    print(f"[1/2] {SNAP_KEY}")
    snap = _s3_get_json(s3, SNAP_KEY)
    snap_before = copy.deepcopy(snap)

    cur_aud = snap.get("audiences") or []
    if cur_aud:
        print(f"  audiences already present: {len(cur_aud)} rows -> keeping as-is")
    else:
        snap["audiences"] = copy.deepcopy(GOAT_AUDIENCES)
        print(f"  audiences: seeded {len(GOAT_AUDIENCES)} rows into snapshot")

    cur_coh = snap.get("cohorts") or []
    if cur_coh:
        print(f"  cohorts already present: {len(cur_coh)} rows -> keeping as-is")
    else:
        snap["cohorts"] = copy.deepcopy(MOVIEGOING_COHORTS)
        print(f"  cohorts: seeded {len(MOVIEGOING_COHORTS)} rows into snapshot")

    # Fill any missing phase color_hex too, opportunistically, since we're
    # in the file. Doesn't overwrite existing colors.
    ph = snap.get("phases") or []
    fixed_colors = 0
    for p in ph:
        if isinstance(p, dict) and not p.get("color_hex"):
            p["color_hex"] = _PHASE_COLOR_MAP.get(p.get("phase_name", ""), "#B7B3D8")
            fixed_colors += 1
    if fixed_colors:
        print(f"  phases: filled color_hex on {fixed_colors} phase(s)")

    if snap != snap_before:
        if not args.dry_run:
            bkey = _s3_backup(s3, SNAP_KEY, "seed_audiences_cohorts_20260923")
            print(f"  backup: s3://{BUCKET}/{bkey}")
            _s3_put_json(s3, SNAP_KEY, snap)
            print("  written")
    else:
        print("  no change")

    print()

    # ------------------------------------------------------------------
    # 3. Registry: convert GOAT phases from strings to dicts.
    # ------------------------------------------------------------------
    print(f"[3/3] {REG_KEY}")
    reg = _s3_get_json(s3, REG_KEY)
    reg_before = copy.deepcopy(reg)

    titles = reg.get("titles") or []
    goat = next((t for t in titles if t.get("title_slug") == "goat"), None)
    if goat is None:
        print("  goat entry not found; aborting registry write")
        return 1

    snap_phases = snap.get("phases") or []
    # Prefer snapshot phases (they have real dates) over registry strings.
    if snap_phases and any(isinstance(p, dict) and p.get("start_date") for p in snap_phases):
        new_phases = [_fill_phase_dict(p) for p in snap_phases]
        new_phases = [p for p in new_phases if p]
        goat["phases"] = new_phases
        print(f"  phases: sourced {len(new_phases)} dicts from snapshot (with real dates)")
    else:
        # Snapshot phases missing dates too; fill string list into empty dicts.
        cur = goat.get("phases") or []
        new_phases = [_fill_phase_dict(p) for p in cur]
        new_phases = [p for p in new_phases if p]
        goat["phases"] = new_phases
        print(f"  phases: converted {len(new_phases)} strings to empty-date dicts (no snapshot dates)")

    if reg != reg_before:
        if not args.dry_run:
            bkey = _s3_backup(s3, REG_KEY, "goat_phase_dicts_20260923")
            print(f"  backup: s3://{BUCKET}/{bkey}")
            reg["updated_at"] = datetime.now(timezone.utc).isoformat()
            _s3_put_json(s3, REG_KEY, reg)
            print("  written")
    else:
        print("  no change")

    print()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
