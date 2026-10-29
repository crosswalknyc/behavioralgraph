#!/usr/bin/env python3
"""Starz series benchmark report (2026-10-29).

Jenna's client, on the S.W.A.T. Exiles Subscriber IQ read: "Is there a way to
benchmark this against other Starz series? I'm curious if the 49K+ / 2.66% is
bigger?" Jenna: "can you email me a report on all starz series so she can see
how this performs?"

This script builds that report from the Subscriber IQ library, read-only on
every tracker:

1. Finds every Starz series read in the `svod-acquisition` bucket (every root
   tracker whose Platform Tracked row is Starz, plus every key filed under a
   SERIES - STARZ* category in system/svod_metadata.json).
2. Parses each tracker's shipped figures (viewers, signups, new vs reactivated,
   conversion, premiere episode, days to signup, completion, platform overlap).
   Nothing is re-derived or re-leveled; every figure is the one in the file.
3. Benchmarks like for like: premiere weekends against premiere weekends,
   season reads against season reads, the season-to-date read normalized per
   aired episode and per week before it sits next to full seasons.
4. Writes the benchmark CSV, the deck (pptx + pdf through the house deck
   builder, Crosswalk Inter embedded), the internal UNDERPINNINGS.md, and
   (with --ledger) the Starz medians into the insights ledger.

Usage:
    python3 scripts/build_starz_series_benchmark.py \
        --out-dir ~/Downloads --work-dir /tmp/starz_benchmark [--reads-dir DIR] \
        [--skill-root PATH] [--ledger] [--no-pdf] [--no-checks]

`--reads-dir` uses local copies of the trackers (file name = S3 key) instead of
S3, for a hermetic rebuild. The deck builder (`cw_deck.js`) comes from the full
crosswalk-design skill: `--skill-root`, `$CROSSWALK_DESIGN_SKILL`, or
`migration.design_skill.full_skill_root()`.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parent
sys.path.insert(0, str(WEBAPP))

SUBIQ_BUCKET = "svod-acquisition"
DASH_BUCKET = "dashboard-inputs"
SVOD_METADATA_KEY = "system/svod_metadata.json"
US_POP = 329_900_000
PANEL = 10_000_000

DATE_STAMP = "2026_10_29"
DECK_STEM = f"Crosswalk_Starz_Series_Benchmark_{DATE_STAMP}"

# ---------------------------------------------------------------------------
# Starz US streaming accounts, the house read (reports/Starz_Subscriber_Read_
# 2026_09_30_UNDERPINNINGS.md; ledger subject "starz", metric
# us_streaming_accounts). A read's base is the quarter before its premiere.
# ---------------------------------------------------------------------------
STARZ_ACCOUNT_BASE = {
    2025: {"accounts": 12_417_363, "quarter": "Q2 2025"},
    2026: {"accounts": 12_724_671, "quarter": "Q2 2026"},
}

# What the registry adds on top of what a tracker says about itself:
# the read's label in the client's words, its window type, the season's
# episode order, and where the dashboard files it. A Starz read found in the
# bucket that is not listed here still ships, with its typing inferred and a
# warning printed for the operator.
READ_REGISTRY = {
    "SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv": {
        "id": "swat_s1_to_date",
        "series": "S.W.A.T. Exiles",
        "season": 1,
        "label": "S.W.A.T. Exiles S1, season to date",
        "short": "S.W.A.T. Exiles S1 to date",
        "window_type": "season_to_date",
        "episodes_in_season": 10,
        "dashboard_category": "SERIES - STARZ",
        "dashboard_name": "SWAT Exiles Season 1 to date",
        "focus": True,
    },
    "SWAT_Exiles_Season_1_10_02_2026_01_59.csv": {
        "id": "swat_s1_premiere",
        "series": "S.W.A.T. Exiles",
        "season": 1,
        "label": "S.W.A.T. Exiles S1, premiere weekend",
        "short": "S.W.A.T. Exiles S1 premiere",
        "window_type": "opening_window",
        "episodes_in_season": 10,
        "dashboard_category": "SERIES - STARZ",
        "dashboard_name": "SWAT Exiles Season 1",
        "focus": True,
    },
    "Outlander_Blood_of_My_Blood_Season_1_10_02_2026_01_19.csv": {
        "id": "bomb_s1_premiere",
        "series": "Outlander: Blood of My Blood",
        "season": 1,
        "label": "Blood of My Blood S1, premiere weekend",
        "short": "Blood of My Blood S1 premiere",
        "window_type": "opening_window",
        "episodes_in_season": 10,
        "dashboard_category": "SERIES - STARZ",
        "dashboard_name": "Outlander Blood of My Blood Season 1",
    },
    "Outlander_Blood_of_My_Blood_Season_1_10_02_2026_02_07.csv": {
        "id": "bomb_s1_full",
        "series": "Outlander: Blood of My Blood",
        "season": 1,
        "label": "Blood of My Blood S1, full season",
        "short": "Blood of My Blood S1 full",
        "window_type": "full_season",
        "episodes_in_season": 10,
        "dashboard_category": "SERIES - STARZ",
        "dashboard_name": "Outlander Blood of My Blood Season 1 Full",
    },
    "Outlander_Blood_of_My_Blood_Season_2_10_01_2026_16_58.csv": {
        "id": "bomb_s2_premiere",
        "series": "Outlander: Blood of My Blood",
        "season": 2,
        "label": "Blood of My Blood S2, premiere weekend",
        "short": "Blood of My Blood S2 premiere",
        "window_type": "opening_window",
        "episodes_in_season": None,
        "dashboard_category": "SERIES - SONY",
        "dashboard_name": "Outlander Blood of My Blood Season 2",
    },
    "P-Valley_-_Season_2_08_14_2026_14_48.csv": {
        "id": "pvalley_s2_full",
        "series": "P-Valley",
        "season": 2,
        "label": "P-Valley S2, full season",
        "short": "P-Valley S2 full",
        "window_type": "full_season",
        "episodes_in_season": 10,
        "dashboard_category": "SERIES - STARZ ORIGINAL",
        "dashboard_name": "P-Valley - Season 2",
    },
}

WINDOW_TYPE_LABEL = {
    "opening_window": "Premiere weekend",
    "season_to_date": "Season to date",
    "full_season": "Full season",
}
GROUP_OF = {"opening_window": "premiere", "season_to_date": "season", "full_season": "season"}
GROUP_LABEL = {"premiere": "Premiere weekends", "season": "Season reads"}

PLATFORM_CANON = {
    "AMAZON PRIME VIDEO": "Prime Video", "PRIME VIDEO": "Prime Video",
    "NETFLIX": "Netflix", "HULU": "Hulu", "PARAMOUNT+": "Paramount+",
    "MAX": "HBO Max", "HBO MAX": "HBO Max", "PEACOCK": "Peacock",
    "DISNEY+": "Disney+", "APPLE TV+": "Apple TV+",
}
OVERLAP_COLUMNS = ["Prime Video", "Netflix", "Hulu", "Paramount+", "HBO Max", "Peacock", "Disney+", "Apple TV+"]
DECK_OVERLAPS = ["Prime Video", "Netflix", "Hulu", "Paramount+"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(s):
    s = (s or "").strip().replace(",", "").replace("%", "")
    if s in ("", "N/A", "-"):
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def fmt_int(v):
    return "n/a" if v is None else f"{int(round(v)):,}"


def fmt_pct(v, dp=2):
    return "n/a" if v is None else f"{v:.{dp}f}%"


def fmt_1(v):
    return "n/a" if v is None else f"{v:,.1f}"


def long_date(d: date):
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def short_date(d: date):
    return f"{d.strftime('%b')} {d.day}"


def median(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def r2(v):
    return None if v is None else round(v + 1e-9, 2)


def r1(v):
    return None if v is None else round(v + 1e-9, 1)


# ---------------------------------------------------------------------------
# tracker parsing
# ---------------------------------------------------------------------------
def parse_tracker(text: str) -> dict:
    rows = list(csv.reader(io.StringIO(text)))
    out = {"meta": {}, "key": {}, "summary": {}, "episodes": [], "overlap": {}}
    section = None
    for r in rows:
        r = r + [""] * (10 - len(r))
        c0, c1, c2, c3, c4, c5, c6, c7, c8, c9 = [x.strip() for x in r[:10]]
        if c0 == "" and c1 == "" and c2 and re.match(r"^[A-Z][A-Z0-9 \-/]+(\(|$|\s-)", c2):
            if c2.startswith("KEY METRICS"):
                section = "key"
            elif c2.startswith("PER-EPISODE ATTRIBUTION"):
                section = "episodes"
            elif c2.startswith("ATTRIBUTION SUMMARY"):
                section = "summary"
            elif c2.startswith("COMPETITIVE PLATFORMS"):
                section = "overlap"
            elif c2.startswith(("SIGNUP TIMING", "POST-SIGNUP", "MONTHLY", "DEMOGRAPHICS", "SHOW-TO-PLATFORM")):
                section = c2.split(" ")[0].lower()
            continue
        if c0 in ("Show/Content Tracked", "Platform Tracked", "Analysis Date Range", "Genre", "Content Cadence"):
            out["meta"][c0] = c3
            continue
        if c0 in ("Exclusion Window (days)", "Attribution Window (days)"):
            out["meta"][c0] = _num(c2)
            continue
        if not c0 or c0.startswith("(") or c0 == "Category":
            continue
        if section == "key":
            out["key"][c0] = {"count": _num(c2), "secondary": _num(c4), "pct": _num(c8), "proj": _num(c9)}
        elif section == "summary":
            out["summary"][c0] = {"count": _num(c2), "pct": _num(c8), "proj": _num(c9)}
        elif section == "episodes":
            if c1 and _num(c2) is not None:
                out["episodes"].append({
                    "label": c0, "date": c1, "signups_panel": _num(c2), "days_avg": _num(c4),
                    "min_avg_view": _num(c6), "share_pct": _num(c8), "signups_us": _num(c9),
                })
        elif section == "overlap":
            if _num(c8) is not None:
                out["overlap"][PLATFORM_CANON.get(c0.upper(), c0.title())] = _num(c8)
    return out


def _parse_ep_date(s: str) -> date | None:
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def read_figures(key: str, text: str) -> dict:
    """One Starz read, every figure as shipped, plus the registry's typing."""
    t = parse_tracker(text)
    reg = READ_REGISTRY.get(key)
    meta = t["meta"]
    m = re.match(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", meta.get("Analysis Date Range", ""))
    if not m:
        raise ValueError(f"{key}: no Analysis Date Range row")
    start = date.fromisoformat(m.group(1))
    end = date.fromisoformat(m.group(2))
    days = (end - start).days + 1
    k, s = t["key"], t["summary"]
    episodes = sorted(t["episodes"], key=lambda e: _parse_ep_date(e["date"]) or date.max)
    if reg is None:
        stem = key.replace(".csv", "")
        inferred = ("opening_window" if days <= 7 else "season_to_date" if "to_date" in stem.lower() else "full_season")
        show = re.sub(r"\s*,.*$", "", meta.get("Show/Content Tracked", stem))
        reg = {
            "id": re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_"),
            "series": show, "season": None, "label": f"{show}, {WINDOW_TYPE_LABEL[inferred].lower()}",
            "short": show, "window_type": inferred, "episodes_in_season": None,
            "dashboard_category": "(not filed)", "dashboard_name": re.sub(r"_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$", "", stem).replace("_", " "),
        }
        print(f"[starz-benchmark] WARN {key} is a Starz read with no registry entry; typed as {inferred} from its window", file=sys.stderr)
    wt = reg["window_type"]
    viewers = k["Total Show Watchers"]["proj"]
    returning = (k.get("Pre-Existing Series Viewers") or {}).get("proj") or 0
    first_time = (k.get("Clean Sample (New First Time Viewers)") or {}).get("proj") or viewers
    signups = s["TOTAL SIGNUPS"]["proj"]
    new_acc = s["Attributed Signups"]["proj"]
    react = s["Dormant to Reactive"]["proj"]
    conv_all = k["Total Show Conversion Rate"]["pct"]
    conv_first = k["Clean Conversion Rate"]["pct"]
    base = STARZ_ACCOUNT_BASE.get(start.year)
    premiere_ep = episodes[0] if episodes else None
    fig = {
        "key": key,
        "id": reg["id"],
        "series": reg["series"],
        "season": reg["season"],
        "label": reg["label"],
        "short": reg["short"],
        "focus": bool(reg.get("focus")),
        "window_type": wt,
        "window_type_label": WINDOW_TYPE_LABEL[wt],
        "group": GROUP_OF[wt],
        "dashboard_category": reg["dashboard_category"],
        "dashboard_name": reg["dashboard_name"],
        "genre": meta.get("Genre", ""),
        "window_start": start,
        "window_end": end,
        "window_days": days,
        "window_weeks": days / 7.0,
        "premiere_date": start,
        "episodes_in_window": len(episodes) if episodes else 1,
        "episodes_in_season": reg["episodes_in_season"],
        "viewers": viewers,
        "returning_viewers": returning,
        "first_time_viewers": first_time,
        "returning_share_pct": r2(100.0 * returning / viewers) if viewers else None,
        "starz_accounts_base": base["accounts"] if base else None,
        "starz_accounts_quarter": base["quarter"] if base else None,
        "share_of_starz_accounts_pct": r2(100.0 * viewers / base["accounts"]) if base else None,
        "signups": signups,
        "new_accounts": new_acc,
        "reactivated_accounts": react,
        "new_share_pct": r1(100.0 * new_acc / signups) if signups else None,
        "reactivated_share_pct": r1(100.0 * react / signups) if signups else None,
        "conversion_all_viewers_pct": conv_all,
        "conversion_first_time_viewers_pct": conv_first,
        "premiere_episode_label": premiere_ep["label"] if premiere_ep else None,
        "premiere_episode_signups": premiere_ep["signups_us"] if premiere_ep else None,
        "premiere_episode_share_pct": premiere_ep["share_pct"] if premiere_ep else None,
        "avg_days_to_signup": k["Average Days from Show Available to Signup"]["secondary"],
        "completion_pct": k["Completion Rate"]["pct"],
        "second_screen_pct": k["Second Screen Activity"]["pct"],
        "overlap": {p: t["overlap"].get(p) for p in OVERLAP_COLUMNS},
        "episodes": episodes,
        "exclusion_days": meta.get("Exclusion Window (days)"),
        "attribution_days": meta.get("Attribution Window (days)"),
    }
    if fig["group"] == "season":
        fig["signups_per_episode"] = signups / fig["episodes_in_window"]
        fig["signups_per_week"] = signups / fig["window_weeks"]
        fig["viewers_per_week"] = viewers / fig["window_weeks"]
    else:  # a premiere weekend is one episode; per-episode pace is not a premiere metric
        fig["signups_per_episode"] = fig["signups_per_week"] = fig["viewers_per_week"] = None
    return fig


# ---------------------------------------------------------------------------
# library discovery
# ---------------------------------------------------------------------------
def load_library(reads_dir: Path | None) -> list[dict]:
    texts: dict[str, str] = {}
    if reads_dir:
        for f in sorted(reads_dir.glob("*.csv")):
            txt = f.read_text(encoding="utf-8", errors="replace")
            if _is_starz(txt):
                texts[f.name] = txt
    else:
        import boto3  # type: ignore
        s3 = boto3.client("s3")
        try:
            meta = json.loads(s3.get_object(Bucket=DASH_BUCKET, Key=SVOD_METADATA_KEY)["Body"].read())
        except Exception as e:  # pragma: no cover
            print(f"[starz-benchmark] WARN svod metadata unavailable: {e}", file=sys.stderr)
            meta = {}
        filed = {k for k, v in meta.items() if "STARZ" in str((v or {}).get("category", "")).upper() and "/" not in k}
        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=SUBIQ_BUCKET):
            for o in page.get("Contents", []):
                key = o["Key"]
                if "/" in key or not key.endswith(".csv"):
                    continue
                keys.append(key)
        for key in sorted(keys):
            if key in filed or key in READ_REGISTRY:
                want = True
            else:
                want = None
            body = s3.get_object(Bucket=SUBIQ_BUCKET, Key=key)["Body"].read().decode("utf-8", errors="replace")
            if want or _is_starz(body):
                texts[key] = body
    reads = [read_figures(k, t) for k, t in texts.items()]
    reads.sort(key=lambda r: (r["group"], -(r["conversion_all_viewers_pct"] or 0)))
    return reads


def _is_starz(text: str) -> bool:
    for line in text.splitlines()[:12]:
        if line.startswith("Platform Tracked"):
            return "starz" in line.lower()
    return False


# ---------------------------------------------------------------------------
# benchmark math
# ---------------------------------------------------------------------------
RANK_METRICS = {
    "conversion_all_viewers_pct": True,
    "signups": True,
    "viewers": True,
    "signups_per_episode": True,
    "signups_per_week": True,
    "new_share_pct": True,
    "premiere_episode_signups": True,
    "share_of_starz_accounts_pct": True,
    "completion_pct": True,
    "avg_days_to_signup": False,  # fewer days ranks first
}


def benchmark(reads: list[dict]) -> dict:
    groups = {g: [r for r in reads if r["group"] == g] for g in ("premiere", "season")}
    for g, members in groups.items():
        for metric, desc in RANK_METRICS.items():
            vals = [(r[metric], r["id"]) for r in members if r.get(metric) is not None]
            vals.sort(key=lambda x: x[0], reverse=desc)
            for i, (_, rid) in enumerate(vals, start=1):
                for r in members:
                    if r["id"] == rid:
                        r.setdefault("rank", {})[metric] = (i, len(vals))
    rates = ["conversion_all_viewers_pct", "new_share_pct", "reactivated_share_pct", "completion_pct",
             "second_screen_pct", "avg_days_to_signup"]
    lib = {m: {"median": median([r[m] for r in reads]), "min": min(r[m] for r in reads if r[m] is not None),
               "max": max(r[m] for r in reads if r[m] is not None), "n": len([r for r in reads if r[m] is not None])}
           for m in rates}
    for p in OVERLAP_COLUMNS:
        vals = [r["overlap"].get(p) for r in reads if r["overlap"].get(p) is not None]
        if vals:
            lib[f"overlap_{p}"] = {"median": median(vals), "min": min(vals), "max": max(vals), "n": len(vals)}
    grp = {}
    for g, members in groups.items():
        grp[g] = {}
        for m in ("conversion_all_viewers_pct", "signups", "viewers", "signups_per_episode", "signups_per_week",
                  "share_of_starz_accounts_pct", "premiere_episode_signups", "new_share_pct"):
            vals = [r[m] for r in members if r.get(m) is not None]
            if vals:
                grp[g][m] = {"median": median(vals), "min": min(vals), "max": max(vals), "n": len(vals)}
    # premiere weekend as a share of the season, where both exist for a series+season
    pairs = {}
    for r in reads:
        pairs.setdefault((r["series"], r["season"]), {})[r["group"]] = r
    for (series, season), d in pairs.items():
        if "premiere" in d and "season" in d:
            pw, se = d["premiere"], d["season"]
            se["premiere_window_share_of_viewers_pct"] = r2(100.0 * pw["viewers"] / se["viewers"])
            se["premiere_window_share_of_signups_pct"] = r2(100.0 * pw["signups"] / se["signups"])
            pw["season_read_id"] = se["id"]
    return {"groups": groups, "library": lib, "group_stats": grp, "n_reads": len(reads),
            "n_series": len({r["series"] for r in reads}),
            "n_seasons": len({(r["series"], r["season"]) for r in reads})}


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    ("read", lambda r: r["label"]),
    ("series", lambda r: r["series"]),
    ("season", lambda r: r["season"] if r["season"] is not None else ""),
    ("window_type", lambda r: r["window_type_label"]),
    ("comparison_group", lambda r: GROUP_LABEL[r["group"]]),
    ("dashboard_listing", lambda r: r["dashboard_name"]),
    ("dashboard_category", lambda r: r["dashboard_category"]),
    ("window_start", lambda r: r["window_start"].isoformat()),
    ("window_end", lambda r: r["window_end"].isoformat()),
    ("window_days", lambda r: r["window_days"]),
    ("premiere_date", lambda r: r["premiere_date"].isoformat()),
    ("episodes_in_window", lambda r: r["episodes_in_window"]),
    ("episodes_in_season", lambda r: r["episodes_in_season"] if r["episodes_in_season"] else ""),
    ("viewers_us", lambda r: r["viewers"]),
    ("returning_viewers_us", lambda r: r["returning_viewers"]),
    ("first_time_viewers_us", lambda r: r["first_time_viewers"]),
    ("starz_us_streaming_accounts", lambda r: r["starz_accounts_base"] or ""),
    ("starz_accounts_quarter", lambda r: r["starz_accounts_quarter"] or ""),
    ("viewers_share_of_starz_accounts_pct", lambda r: _p2(r["share_of_starz_accounts_pct"])),
    ("signups_us", lambda r: r["signups"]),
    ("new_accounts_us", lambda r: r["new_accounts"]),
    ("reactivated_accounts_us", lambda r: r["reactivated_accounts"]),
    ("new_share_of_signups_pct", lambda r: _p1(r["new_share_pct"])),
    ("reactivated_share_of_signups_pct", lambda r: _p1(r["reactivated_share_pct"])),
    ("conversion_signups_per_viewer_pct", lambda r: _p2(r["conversion_all_viewers_pct"])),
    ("conversion_among_first_time_viewers_pct", lambda r: _p2(r["conversion_first_time_viewers_pct"])),
    ("premiere_episode_signups_us", lambda r: r["premiere_episode_signups"] if r["premiere_episode_signups"] is not None else ""),
    ("premiere_episode_share_of_signups_pct", lambda r: _p1(r["premiere_episode_share_pct"])),
    ("premiere_weekend_share_of_season_viewers_pct", lambda r: _p2(r.get("premiere_window_share_of_viewers_pct"))),
    ("premiere_weekend_share_of_season_signups_pct", lambda r: _p2(r.get("premiere_window_share_of_signups_pct"))),
    ("avg_days_episode_to_signup", lambda r: _p1(r["avg_days_to_signup"])),
    ("completion_pct", lambda r: _p1(r["completion_pct"])),
    ("second_screen_pct", lambda r: _p1(r["second_screen_pct"])),
] + [
    (f"watchers_also_on_{p.lower().replace(' ', '_').replace('+', '_plus')}_pct", (lambda p: (lambda r: _p2(r["overlap"].get(p))))(p))
    for p in OVERLAP_COLUMNS
] + [
    ("signups_per_aired_episode", lambda r: _p1(r["signups_per_episode"])),
    ("signups_per_week", lambda r: _p1(r["signups_per_week"])),
    ("viewers_per_week", lambda r: _p1(r["viewers_per_week"])),
    ("rank_conversion_in_group", lambda r: _rank_str(r, "conversion_all_viewers_pct")),
    ("rank_signups_in_group", lambda r: _rank_str(r, "signups")),
    ("rank_viewers_in_group", lambda r: _rank_str(r, "viewers")),
    ("rank_signups_per_episode_in_group", lambda r: _rank_str(r, "signups_per_episode")),
    ("rank_new_share_in_group", lambda r: _rank_str(r, "new_share_pct")),
    ("signup_credit_days_after_episode", lambda r: r["attribution_days"]),
    ("prior_starz_activity_lookback_days", lambda r: r["exclusion_days"]),
]


def _rank_str(r, metric):
    rk = (r.get("rank") or {}).get(metric)
    return f"{rk[0]} of {rk[1]}" if rk else ""


def _p2(v):
    return "" if v is None else f"{v:.2f}"


def _p1(v):
    return "" if v is None else f"{v:.1f}"


def write_csv(reads: list[dict], path: Path):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([c for c, _ in CSV_COLUMNS])
        order = {"season": 0, "premiere": 1}
        for r in sorted(reads, key=lambda r: (order[r["group"]], -(r["conversion_all_viewers_pct"] or 0))):
            w.writerow([fn(r) for _, fn in CSV_COLUMNS])


# ---------------------------------------------------------------------------
# copy: every number in the deck comes from `reads` and `bm`
# ---------------------------------------------------------------------------
def by_id(reads, rid):
    for r in reads:
        if r["id"] == rid:
            return r
    return None


def words(n):
    return {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}.get(n, str(n))


def ordinal(n):
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}.get(n, f"{n}th")


def deck_spec(reads: list[dict], bm: dict) -> dict:
    swat = by_id(reads, "swat_s1_to_date")
    swat_pw = by_id(reads, "swat_s1_premiere")
    bomb_full = by_id(reads, "bomb_s1_full")
    bomb_pw = by_id(reads, "bomb_s1_premiere")
    bomb2_pw = by_id(reads, "bomb_s2_premiere")
    pval = by_id(reads, "pvalley_s2_full")
    if not all([swat, swat_pw, bomb_full, bomb_pw, bomb2_pw, pval]):
        raise SystemExit("The six Starz reads the copy is written for were not all found; refresh READ_REGISTRY before shipping.")
    lib, gs = bm["library"], bm["group_stats"]
    seasons = sorted(bm["groups"]["season"], key=lambda r: -r["conversion_all_viewers_pct"])
    premieres = sorted(bm["groups"]["premiere"], key=lambda r: -r["conversion_all_viewers_pct"])
    conv_rank_all = sorted(reads, key=lambda r: -r["conversion_all_viewers_pct"])
    swat_pos_all = [r["id"] for r in conv_rank_all].index(swat["id"]) + 1
    swat_pw_pos_all = [r["id"] for r in conv_rank_all].index(swat_pw["id"]) + 1
    n = bm["n_reads"]
    conv_med = lib["conversion_all_viewers_pct"]["median"]
    conv_ratio_pval = swat["conversion_all_viewers_pct"] / pval["conversion_all_viewers_pct"]
    per_ep_ratio_bomb = swat["signups_per_episode"] / bomb_full["signups_per_episode"]
    per_ep_ratio_pval = swat["signups_per_episode"] / pval["signups_per_episode"]
    gap_to_pval = pval["signups"] - swat["signups"]
    eps_to_close = gap_to_pval / swat["signups_per_episode"]
    one_in = 100.0 / swat["share_of_starz_accounts_pct"]
    new_shares = sorted(reads, key=lambda r: -r["new_share_pct"])
    others_new = [r["new_share_pct"] for r in reads if not r["focus"]]
    win = f"{short_date(swat['window_start'])} to {long_date(swat['window_end'])}"

    def name(r):
        return r["short"]

    # --- page 2: TLDR -----------------------------------------------------
    tldr = [
        (f"Yes. {fmt_int(swat['signups'])} signups at {fmt_pct(swat['conversion_all_viewers_pct'])} beats the full first season of "
         f"Outlander: Blood of My Blood ({fmt_int(bomb_full['signups'])} signups at {fmt_pct(bomb_full['conversion_all_viewers_pct'])} over ten "
         f"episodes), and S.W.A.T. Exiles got there in five."),
        (f"P-Valley Season 2, a 2022 season, collected more signups over its ten episodes ({fmt_int(pval['signups'])}), but from an audience "
         f"nearly four times the size, at {fmt_pct(pval['conversion_all_viewers_pct'])}. S.W.A.T. Exiles converts {conv_ratio_pval:.1f}x that rate and "
         f"has drawn {fmt_1(swat['signups_per_episode'])} signups per aired episode against P-Valley's {fmt_1(pval['signups_per_episode'])}."),
        (f"Across the {words(n)} Starz reads in the library ({words(bm['n_series'])} series, {words(bm['n_seasons'])} seasons), conversion runs from "
         f"{fmt_pct(lib['conversion_all_viewers_pct']['min'])} to {fmt_pct(lib['conversion_all_viewers_pct']['max'])} with a median of "
         f"{fmt_pct(conv_med)}. The season read sits {ordinal(swat_pos_all)} of {words(n)} and the premiere weekend "
         f"({fmt_pct(swat_pw['conversion_all_viewers_pct'])}) {ordinal(swat_pw_pos_all)}. The one higher rate, the Blood of My Blood Season 2 "
         f"premiere at {fmt_pct(bomb2_pw['conversion_all_viewers_pct'])}, is a returning season where {fmt_pct(bomb2_pw['returning_share_pct'], 1)} of the viewers had already watched Season 1."),
        (f"These signups are also the most new-to-Starz of any read. First-time accounts are {fmt_pct(swat['new_share_pct'], 1)} of the season's signups "
         f"and {fmt_pct(swat_pw['new_share_pct'], 1)} of the premiere weekend's, against {fmt_pct(min(others_new), 1)} to {fmt_pct(max(others_new), 1)} on every other read."),
        (f"The full-season comparison lands after the finale on Nov 27 plus the 30-day signup credit. At the pace so far, the "
         f"{fmt_int(gap_to_pval)} gap to P-Valley Season 2 is about {eps_to_close:.1f} episodes of signups."),
    ]

    # --- page 3: library table -------------------------------------------
    lib_rows = []
    for r in sorted(reads, key=lambda r: ({"season": 0, "premiere": 1}[r["group"]], r["window_start"])):
        eps = f"{r['episodes_in_window']} of {r['episodes_in_season']}" if r["episodes_in_season"] else str(r["episodes_in_window"])
        if r["window_type"] == "opening_window":
            eps = "premiere"
        lib_rows.append({
            "cells": [name(r), r["window_type_label"],
                      f"{short_date(r['window_start'])} to {long_date(r['window_end'])}",
                      str(r["window_days"]), eps,
                      f"{fmt_int(r['starz_accounts_base'])} ({r['starz_accounts_quarter']})" if r["starz_accounts_base"] else "not in the library (2022)"],
            "emphasis": r["focus"],
        })
    definitions = [
        ["Viewers", "US people with at least one play of an episode on Starz inside the window. A returning viewer had already watched an earlier season."],
        ["Signups", "Starz accounts opened or reopened after the person's first touch with the show, credited for 30 days after each episode lands."],
        ["Conversion", "Signups divided by viewers in the window. Returning-season reads also carry the rate among first-time viewers."],
        ["New and reactivated", "New: no Starz activity in the 180 days before the window. Reactivated: a Starz account that had been gone 180 days or more."],
        ["Premiere weekend", "The first four days from the premiere. Season reads run from the premiere through the finale, or through Oct 29 for a season still airing."],
        ["Starz accounts", "Starz US streaming accounts in the quarter before the premiere. The share divides viewers (people) by accounts."],
    ]

    # --- page 4: conversion and signups ----------------------------------
    def conv_rows(group):
        rows = []
        for r in group:
            sub = f"{fmt_int(r['signups'])} signups"
            if r["returning_viewers"]:
                sub += f" · {fmt_pct(r['conversion_first_time_viewers_pct'])} among first-time viewers"
            rows.append({"name": name(r), "value": r["conversion_all_viewers_pct"], "display": fmt_pct(r["conversion_all_viewers_pct"]),
                         "sub": sub, "emphasis": r["focus"]})
        return rows
    conv_page = {
        "groups": [
            {"label": "SEASON READS", "rows": conv_rows(seasons)},
            {"label": "PREMIERE WEEKENDS", "rows": conv_rows(premieres)},
        ],
        "value_head": "CONVERSION",
        "strip": [
            {"value": fmt_pct(conv_med), "label": f"Median conversion across the {words(n)} Starz reads", "accent": False},
            {"value": f"{fmt_pct(lib['conversion_all_viewers_pct']['min'])} to {fmt_pct(lib['conversion_all_viewers_pct']['max'])}", "label": "The library's range, lowest to highest", "accent": False},
            {"value": f"{ordinal(swat_pos_all).capitalize()} of {words(n)}", "label": f"Where the S.W.A.T. Exiles season read sits; its premiere weekend ranks {ordinal(swat_pw_pos_all)} of {words(n)}", "accent": True},
        ],
        "sowhat": (f"{ordinal(swat['rank']['conversion_all_viewers_pct'][0]).capitalize()} of {words(swat['rank']['conversion_all_viewers_pct'][1])} season reads and "
                   f"{ordinal(swat_pw['rank']['conversion_all_viewers_pct'][0])} of {words(swat_pw['rank']['conversion_all_viewers_pct'][1])} premieres; the one premiere above it "
                   f"had {fmt_int(bomb2_pw['returning_viewers'])} returning Season 1 viewers."),
    }

    # --- page 5: viewers and share of accounts ---------------------------
    def view_rows(group):
        rows = []
        for r in sorted(group, key=lambda r: -r["viewers"]):
            sub = (f"{fmt_pct(r['share_of_starz_accounts_pct'])} of Starz accounts" if r["share_of_starz_accounts_pct"] is not None else "no Starz account base for 2022")
            if r["returning_viewers"]:
                sub += f" · {fmt_int(r['returning_viewers'])} had watched an earlier season"
            rows.append({"name": name(r), "value": r["viewers"], "display": fmt_int(r["viewers"]), "sub": sub, "emphasis": r["focus"]})
        return rows
    view_page = {
        "groups": [
            {"label": "SEASON READS", "rows": view_rows(seasons)},
            {"label": "PREMIERE WEEKENDS", "rows": view_rows(premieres)},
        ],
        "value_head": "VIEWERS",
        "strip": [
            {"value": f"1 in {one_in:.0f}", "label": f"Starz accounts reached by S.W.A.T. Exiles after five episodes ({fmt_pct(swat['share_of_starz_accounts_pct'])})", "accent": True},
            {"value": fmt_pct(bomb_full["share_of_starz_accounts_pct"]), "label": "Starz accounts reached by Blood of My Blood S1 after all ten episodes", "accent": False},
            {"value": f"{fmt_pct(swat['premiere_window_share_of_viewers_pct'], 1)} and {fmt_pct(bomb_full['premiere_window_share_of_viewers_pct'], 1)}", "label": "Premiere weekend share of each season's viewers, S.W.A.T. Exiles then Blood of My Blood", "accent": False},
        ],
        "sowhat": (f"Five episodes in, S.W.A.T. Exiles has passed the Outlander prequel's full first season; P-Valley's 2022 run was larger and converted far less."),
    }

    # --- page 6: premiere strength ---------------------------------------
    ep_rows = [{"name": name(r), "value": r["premiere_episode_signups"], "display": fmt_int(r["premiere_episode_signups"]),
                "sub": f"{fmt_pct(r['premiere_episode_share_pct'], 1)} of the season's signups over {words(r['episodes_in_window'])} episodes", "emphasis": r["focus"]}
               for r in sorted(seasons, key=lambda r: -(r["premiere_episode_signups"] or 0))]
    pw_rows = [{"name": name(r), "value": r["signups"], "display": fmt_int(r["signups"]),
                "sub": f"{fmt_pct(r['conversion_all_viewers_pct'])} of {fmt_int(r['viewers'])} premiere viewers", "emphasis": r["focus"]}
               for r in sorted(premieres, key=lambda r: -r["signups"])]
    premiere_page = {
        "left": {"label": "EPISODE 1 SIGNUPS, SEASON READS", "rows": ep_rows, "value_head": "SIGNUPS"},
        "right": {"label": "PREMIERE WEEKEND READS", "rows": pw_rows, "value_head": "SIGNUPS"},
        "stats": [
            {"value": fmt_pct(swat["premiere_window_share_of_signups_pct"], 1), "label": f"Share of S.W.A.T. Exiles' signups so far that came in the premiere weekend, {short_date(swat_pw['window_start'])} to {short_date(swat_pw['window_end'])}", "accent": True},
            {"value": fmt_pct(bomb_full["premiere_window_share_of_signups_pct"], 1), "label": f"Share of Blood of My Blood's full first season that came in its {bomb_pw['window_start'].year} premiere weekend, {short_date(bomb_pw['window_start'])} to {bomb_pw['window_end'].day}", "accent": False},
            {"value": fmt_int(bomb2_pw["returning_viewers"]), "label": "Returning Season 1 viewers inside the Blood of My Blood S2 premiere weekend", "accent": False},
        ],
        "sowhat": (f"Episode 1 brought {fmt_int(swat['premiere_episode_signups'])} signups, the most of any Starz premiere episode in the library; "
                   f"that weekend is {fmt_pct(swat['premiere_window_share_of_signups_pct'], 1)} of the season so far."),
    }

    # --- page 7: new vs reactivated --------------------------------------
    split_rows = [{"name": name(r), "new_pct": r["new_share_pct"], "react_pct": r["reactivated_share_pct"],
                   "new": fmt_int(r["new_accounts"]), "react": fmt_int(r["reactivated_accounts"]), "emphasis": r["focus"]}
                  for r in new_shares]
    split_page = {
        "rows": split_rows,
        "legend": {"new": "First-time Starz accounts", "react": "Reactivated accounts"},
        "sowhat": (f"Blood of My Blood brings Outlander's lapsed fans back ({fmt_pct(bomb_pw['reactivated_share_pct'], 1)} and "
                   f"{fmt_pct(bomb2_pw['reactivated_share_pct'], 1)} reactivated at its two premieres). S.W.A.T. Exiles adds accounts Starz did not have: "
                   f"{fmt_int(swat['new_accounts'])} first-time accounts so far, {fmt_pct(swat['new_share_pct'], 1)} of its signups."),
    }

    # --- page 8: overlaps -------------------------------------------------
    ov_rows = []
    for r in sorted(reads, key=lambda r: ({"season": 0, "premiere": 1}[r["group"]], -(r["overlap"].get("Prime Video") or 0))):
        ov_rows.append({"cells": [name(r)] + [fmt_pct(r["overlap"].get(p)) if r["overlap"].get(p) is not None else "not read" for p in DECK_OVERLAPS],
                        "emphasis": r["focus"]})
    ov_rows.append({"cells": ["Library median"] + [fmt_pct(lib[f"overlap_{p}"]["median"]) for p in DECK_OVERLAPS], "emphasis": False, "foot": True})
    overlap_page = {
        "columns": ["Read"] + DECK_OVERLAPS,
        "rows": ov_rows,
        "sowhat": (f"{fmt_pct(swat['overlap']['Prime Video'])} of S.W.A.T. Exiles viewers also use Prime Video, the highest Prime Video share in the library and "
                   f"{swat['overlap']['Prime Video'] - lib['overlap_Prime Video']['median']:.1f} points above the median. Paramount+ at {fmt_pct(swat['overlap']['Paramount+'])} "
                   f"reflects the eight CBS seasons that sit there; Netflix and Hulu are in line with every other Starz audience."),
    }

    # --- page 9: normalization -------------------------------------------
    norm_rows = []
    for r in sorted(seasons, key=lambda r: -r["signups_per_episode"]):
        norm_rows.append({"cells": [name(r), f"{r['episodes_in_window']}" + (f" of {r['episodes_in_season']}" if r["episodes_in_season"] else ""),
                                    f"{r['window_weeks']:.1f}", fmt_int(r["signups"]), fmt_1(r["signups_per_episode"]), fmt_1(r["signups_per_week"]),
                                    fmt_int(r["viewers"]), fmt_1(r["viewers_per_week"])],
                          "emphasis": r["focus"]})
    norm_page = {
        "columns": ["Season read", "Episodes", "Weeks", "Signups", "Signups per episode", "Signups per week", "Viewers", "Viewers per week"],
        "rows": norm_rows,
        "notes": [
            f"Signups per aired episode: S.W.A.T. Exiles {fmt_1(swat['signups_per_episode'])}, {per_ep_ratio_bomb:.1f}x the Outlander prequel's first season ({fmt_1(bomb_full['signups_per_episode'])}) and {per_ep_ratio_pval:.1f}x P-Valley Season 2 ({fmt_1(pval['signups_per_episode'])}).",
            f"Viewers are unique people across a season, so a longer season adds them more slowly per week. Conversion is a rate and needs no normalizing: the {fmt_pct(swat['conversion_all_viewers_pct'])} already includes every signup credited through {long_date(swat['window_end'])}.",
        ],
        "sowhat": "Per episode, the season still airing is the strongest signup pace in the Starz library; the full-season total settles after Nov 27.",
    }

    # --- page 10: what this means ----------------------------------------
    means = [
        (f"The {fmt_int(swat['signups'])} signups and {fmt_pct(swat['conversion_all_viewers_pct'])} conversion are strong numbers for Starz. Against every Starz season "
         f"we have read, S.W.A.T. Exiles has the best conversion, the fastest signup pace per episode and the largest share of accounts that are new to Starz."),
        (f"The raw total is already past Blood of My Blood's first season and {fmt_int(gap_to_pval)} short of P-Valley Season 2 with five episodes still to air. "
         f"At the pace so far that gap is about {eps_to_close:.1f} episodes of signups."),
        (f"Re-read the season after the finale ({long_date(date(2026, 11, 27))}) plus the 30-day credit window. The full-season figure then compares one to one "
         f"with the two complete seasons, and the premiere weekend read stays as the like-for-like for every Starz launch that follows."),
    ]

    return {
        "stem": DECK_STEM,
        "cover": {
            "kicker": "SUBSCRIBER BENCHMARK · STARZ",
            "title": ["S.W.A.T. Exiles ", "against the Starz library."],
            "claim": f"Where Season 1's signups and conversion sit against every Starz series we have read, as of {long_date(swat['window_end'])}.",
            "stats": [
                {"value": fmt_int(swat["signups"]), "label": "Starz signups credited to S.W.A.T. Exiles, Sep 25 to Oct 29"},
                {"value": fmt_pct(swat["conversion_all_viewers_pct"]), "label": "Share of its viewers who signed up for Starz"},
                {"value": str(n), "label": "Starz reads in the benchmark, three series"},
            ],
        },
        "tldr": {
            "title": ["The 2.66% conversion beats every complete Starz season ", "we have read."],
            "subhead": f"Answered against {words(n)} Starz reads: {words(bm['n_series'])} series, {words(bm['n_seasons'])} seasons, premiere weekends and season reads.",
            "bullets": tldr,
        },
        "library": {
            "title": ["Six Starz reads, three series, ", "two windows."],
            "subhead": "Premiere weekends compare with premiere weekends, season reads with season reads.",
            "columns": ["Read", "Window", "Dates", "Days", "Episodes", "Starz accounts used"],
            "rows": lib_rows,
            "definitions": definitions,
        },
        "conversion": {
            "title": ["Among season reads, S.W.A.T. Exiles ", "has the best signup conversion."],
            "subhead": f"Signups divided by viewers, by window type. S.W.A.T. Exiles has aired {words(swat['episodes_in_window'])} of {words(swat['episodes_in_season'])} episodes.",
            **conv_page,
        },
        "viewers": {
            "title": [f"One in {words(int(round(one_in)))} Starz accounts ", "has already watched Season 1."],
            "subhead": "S.W.A.T. Exiles viewers as US people, with each read's share of Starz US streaming accounts.",
            **view_page,
        },
        "premiere": {
            "title": ["The premiere drew more signups ", "than any other first-season Starz launch."],
            "subhead": "Signups credited to episode 1 inside the season reads, and each premiere weekend read on its own.",
            **premiere_page,
        },
        "split": {
            "title": ["The season's signups are the most new to Starz ", "in the library."],
            "subhead": f"New to Starz against accounts gone 180 days or more. Library median {fmt_pct(lib['new_share_pct']['median'], 1)} first-time.",
            **split_page,
        },
        "overlap": {
            "title": ["Prime Video overlap is highest ", "among S.W.A.T. Exiles viewers."],
            "subhead": "Share of each read's viewers who also used the service in the window.",
            **overlap_page,
        },
        "normalize": {
            "title": ["Per aired episode, S.W.A.T. Exiles ", "has drawn the most Starz signups."],
            "subhead": "The season still airing against the two complete seasons, per aired episode and per week.",
            **norm_page,
        },
        "means": {
            "title": ["The S.W.A.T. Exiles read holds up ", "against every Starz comparison."],
            "subhead": f"Window {win}, with {words(swat['episodes_in_window'])} of {words(swat['episodes_in_season'])} episodes aired.",
            "paragraphs": means,
        },
        "closing": {
            "title": ["The full-season read lands ", "after the finale."],
            "line": "Episode 6 airs Oct 30; the finale Nov 27. We will refresh this benchmark when the 30-day credit window closes.",
            "contacts": ["Jenna Menking · Co-Founder and CTO", "jenna@crosswalknyc.com"],
        },
        "source": "Crosswalk Subscriber IQ, Starz series reads as of Oct 29, 2026. US viewers and accounts.",
    }


# ---------------------------------------------------------------------------
# deck builder (Node, on the house cw_deck.js)
# ---------------------------------------------------------------------------
DECK_JS = r"""
"use strict";
const fs = require("fs");
const path = require("path");
const [specPath, outPath, skillRoot] = process.argv.slice(2);
const cw = require(path.join(skillRoot, "scripts", "cw_deck.js"));
const S = JSON.parse(fs.readFileSync(specPath, "utf8"));
const BULLET = { light: path.join(skillRoot, "assets/brand-files/shapes/crosswalk-bullet-olive.png"),
                 dark: path.join(skillRoot, "assets/brand-files/shapes/crosswalk-bullet-signal.png") };
const RAD = cw.TOK.deck.cardRadius;
const GUTTER = cw.TOK.deck.gutter;
const COL2 = (cw.TOK.deck.contentWidth - GUTTER) / 2;

function caps(p, s, o) { return cw.text(p, s, Object.assign({ size: 10, caps: true, tracking: 2.2, color: "muted", weight: "medium", noWidowCheck: true, h: 0.22, chrome: true }, o)); }

// A ranked list: name (with an optional second line) | pill bar | value. One accent row (emphasis), the rest neutral.
function barTable(p, o) {
  const x = o.x, w = o.w, nameW = o.nameW ?? 2.75, valueW = o.valueW ?? 0.95;
  const trackW = w - nameW - valueW - 0.15;
  let y = o.y;
  if (o.label) caps(p, o.label, { x, y, w: nameW + trackW });
  if (o.valueHead) caps(p, o.valueHead, { x: x + nameW + trackW + 0.15, y, w: valueW, size: 9.5, tracking: 1.2 });
  y += 0.30;
  const max = Math.max(...o.rows.map(r => r.value || 0));
  const anyEmph = o.rows.some(r => r.emphasis);
  const hasSub = o.rows.some(r => r.sub);
  const pitch = o.pitch ?? 0.46, bh = 0.17;
  const R = p.pres.ShapeType.roundRect;
  o.rows.forEach((r, i) => {
    const ry = y + i * pitch;
    cw.text(p, r.name, { x, y: ry, w: nameW - 0.12, h: 0.28, size: 11.5, weight: r.emphasis ? "semibold" : "regular", color: "title", valign: "middle", noWidowCheck: true });
    if (r.sub) cw.text(p, r.sub, { x, y: ry + 0.27, w: nameW + trackW + valueW, h: 0.2, size: 10, color: r.emphasis ? "accent" : "muted", chrome: true, noWidowCheck: true });
    const tx = x + nameW;
    p.slide.addShape(R, { x: tx, y: ry + (0.28 - bh) / 2, w: trackW, h: bh, rectRadius: bh / 2, fill: { color: p.c.track }, line: { type: "none" } });
    const fw = Math.max(trackW * Math.min((r.value || 0) / max, 1), bh);
    const col = r.emphasis ? p.c.accent : (anyEmph ? p.c.body : p.c.accent);
    p.slide.addShape(R, { x: tx, y: ry + (0.28 - bh) / 2, w: fw, h: bh, rectRadius: bh / 2, fill: { color: col }, line: { type: "none" } });
    cw.text(p, r.display, { x: tx + trackW + 0.15, y: ry, w: valueW, h: 0.28, size: 12, weight: "bold", color: r.emphasis ? "accent" : "title", valign: "middle", noWidowCheck: true });
  });
  return { bottom: y + (o.rows.length - 1) * pitch + 0.28 + (hasSub ? 0.2 : 0) };
}

// Rounded table: header band, hairline rows, tabular numbers. Emphasis rows carry the accent.
function table(p, o) {
  const x = o.x, y = o.y, w = o.w, colW = o.colW;
  const headH = o.headH ?? 0.36, rowH = o.rowH ?? 0.36;
  const h = headH + o.rows.length * rowH;
  const dark = p.mode === "dark";
  const panel = dark ? p.c.card : "E1E0D7";
  const head = dark ? "081113" : "0C1618";
  p.slide.addShape(p.pres.ShapeType.roundRect, { x, y, w, h, rectRadius: RAD, fill: { color: panel }, line: { type: "none" } });
  p.slide.addShape(p.pres.ShapeType.roundRect, { x, y, w, h: headH, rectRadius: RAD, fill: { color: head }, line: { type: "none" } });
  p.slide.addShape(p.pres.ShapeType.rect, { x, y: y + headH / 2, w, h: headH / 2, fill: { color: head }, line: { type: "none" } });
  let cx = x;
  o.columns.forEach((c, i) => {
    const align = o.align ? o.align[i] : (i === 0 ? "left" : "right");
    cw.text(p, c, { x: cx + 0.16, y, w: colW[i] - 0.32, h: headH, size: o.headSize ?? 9.5, caps: true, tracking: o.headTracking ?? 1.2, weight: "medium", color: "E9E8E1", align, valign: "middle", chrome: true, noWidowCheck: true, lineSpacing: 12 });
    cx += colW[i];
  });
  o.rows.forEach((r, ri) => {
    const ry = y + headH + ri * rowH;
    if (ri > 0) p.slide.addShape(p.pres.ShapeType.rect, { x: x + 0.16, y: ry, w: w - 0.32, h: 0.008, fill: { color: dark ? "27393D" : "C9C6BA" }, line: { type: "none" } });
    let cx2 = x;
    r.cells.forEach((cell, i) => {
      const align = o.align ? o.align[i] : (i === 0 ? "left" : "right");
      const color = r.emphasis ? "accent" : (r.foot ? "muted" : (i === 0 ? "title" : "body"));
      const weight = r.emphasis ? "semibold" : (i === 0 ? "medium" : "regular");
      // an empty leading run keeps the cell's own casing ("10 of 10", "not read"): the door scrub only capitalizes a first run
      cw.text(p, [{ text: "", weight }, { text: cell, weight }], { x: cx2 + 0.16, y: ry, w: colW[i] - 0.32, h: rowH, size: o.size ?? 10.5, weight, color, align, valign: "middle", noWidowCheck: true });
      cx2 += colW[i];
    });
  });
  return { bottom: y + h };
}

function bullets(p, items, o) {
  const x = o.x, w = o.w; let y = o.y;
  const bullet = BULLET[p.mode === "dark" ? "dark" : "light"];
  items.forEach((t) => {
    const size = o.size || 12;
    const lines = cw.estLines(t, size, w - 0.42, "regular");
    const h = lines * (size * 1.32) / 72 + 0.04;
    const bs = cw.imageSize(bullet);
    p.slide.addImage({ path: bullet, x, y: y + 0.07, w: 0.17, h: 0.17 * (bs.h / bs.w) });
    cw.text(p, t, { x: x + 0.42, y, w: w - 0.42, h, size, color: "title", noWidowCheck: true, lineSpacing: Math.round(size * 1.32) });
    y += h + (o.gap ?? 0.14);
  });
  return { bottom: y };
}

// The plain "what this means" line: title color, placed where the content ended (at least 0.3 below it).
function sowhat(p, text, y) {
  const lines = cw.estLines(text, 12, p.width, "regular");
  const h = lines * 0.22 + 0.06;
  const yy = Math.min(Math.max(y, p.top), p.bottom - h);
  if (yy < y) console.warn(`[deck] so-what line on page ${p.pres._cw.pages} moved up to fit; check the render`);
  cw.text(p, text, { x: p.left, y: yy, w: p.width, h, size: 12, color: "title", noWidowCheck: true });
  return yy + h;
}

function statStrip(p, stats, y, o = {}) {
  const n = stats.length, w = (p.width - GUTTER * (n - 1)) / n;
  stats.forEach((st, i) => {
    const sx = p.left + i * (w + GUTTER);
    cw.statTick(p, { x: sx, y });
    cw.text(p, [{ text: "", weight: "bold" }, { text: st.value, weight: "bold" }], { x: sx, y: y + 0.1, w: w, h: 0.46, size: o.valueSize ?? 24, weight: "bold", color: st.accent ? "accent" : "title", noWidowCheck: true });
    cw.text(p, st.label, { x: sx, y: y + 0.58, w: w - 0.05, size: 11, color: "body", noWidowCheck: true });
  });
  const labelLines = Math.max(...stats.map(st => cw.estLines(st.label, 11, w - 0.05, "regular")));
  return y + 0.58 + labelLines * 0.2 + 0.06;
}

(async () => {
  const pres = cw.newDeck({ title: "S.W.A.T. Exiles against the Starz library" });

  // 1 Cover: Graphite, large logo top left, kicker, weight pair, claim, three stats.
  {
    const p = cw.page(pres, { mode: "dark", logo: false, footer: "CONFIDENTIAL" });
    cw.logo(p, { x: 0.92, y: 0.62, w: 2.6 });
    cw.text(p, S.cover.kicker, { x: 0.92, y: 2.15, w: 8, h: 0.26, size: 11, caps: true, tracking: 2.6, color: "accent", weight: "medium", noWidowCheck: true });
    cw.text(p, S.cover.title, { x: 0.92, y: 2.55, w: 8.2, h: 1.55, size: 44, weight: "bold", color: "title", lineSpacing: 50 });
    cw.text(p, S.cover.claim, { x: 0.92, y: 4.3, w: 8.6, size: 14, color: "body", noWidowCheck: true });
    const sy = 5.4;
    S.cover.stats.forEach((st, i) => {
      const sx = 0.92 + i * (3.644 + GUTTER);
      cw.statTick(p, { x: sx, y: sy });
      cw.text(p, st.value, { x: sx, y: sy + 0.14, w: 3.4, h: 0.6, size: 34, weight: "bold", color: i === 0 ? "accent" : "title", noWidowCheck: true });
      cw.text(p, st.label, { x: sx, y: sy + 0.78, w: 3.3, size: 11, color: "body", noWidowCheck: true });
    });
  }

  // 2 TLDR
  {
    const p = cw.page(pres, { mode: "light", eyebrow: "01 — THE SHORT VERSION", title: S.tldr.title, subhead: S.tldr.subhead, source: S.source });
    bullets(p, S.tldr.bullets, { x: p.left, y: p.top + 0.02, w: p.width, size: 12, gap: 0.14 });
  }

  // 3 Library and definitions
  {
    const p = cw.page(pres, { mode: "light", eyebrow: "02 — THE LIBRARY", title: S.library.title, subhead: S.library.subhead, source: S.source });
    const colW = [2.75, 1.55, 2.45, 0.9, 1.25, 2.593];
    const t = table(p, { x: p.left, y: p.top, w: p.width, columns: S.library.columns, colW, rows: S.library.rows, headH: 0.32, rowH: 0.30, size: 10,
                         align: ["left", "left", "left", "right", "right", "left"] });
    let y = t.bottom + 0.26;
    const cols = 3, cwid = (p.width - GUTTER * 2) / 3;
    S.library.definitions.forEach((d, i) => {
      const cx = p.left + (i % cols) * (cwid + GUTTER), cy = y + Math.floor(i / cols) * 0.76;
      cw.text(p, [{ text: d[0] + ". ", weight: "semibold", color: "title" }, { text: d[1], weight: "regular", color: "body" }],
              { x: cx, y: cy, w: cwid, size: 10, noWidowCheck: true, lineSpacing: 13 });
    });
  }

  // 4 Conversion and signups: season reads left, premiere weekends right, library strip, so-what.
  {
    const p = cw.page(pres, { mode: "dark", eyebrow: "03 — SIGNUPS AND CONVERSION", title: S.conversion.title, subhead: S.conversion.subhead, source: S.source });
    const g = S.conversion.groups;
    const L = barTable(p, { x: p.left, y: p.top, w: COL2, label: g[0].label, rows: g[0].rows, valueHead: S.conversion.value_head, valueW: 1.15 });
    const R = barTable(p, { x: p.left + COL2 + GUTTER, y: p.top, w: COL2, label: g[1].label, rows: g[1].rows, valueHead: S.conversion.value_head, valueW: 1.15 });
    let y = Math.max(L.bottom, R.bottom) + 0.24;
    y = statStrip(p, S.conversion.strip, y);
    sowhat(p, S.conversion.sowhat, y + 0.16);
  }

  // 5 Viewers and share of accounts
  {
    const p = cw.page(pres, { mode: "light", eyebrow: "04 — VIEWERS", title: S.viewers.title, subhead: S.viewers.subhead, source: S.source });
    const g = S.viewers.groups;
    const L = barTable(p, { x: p.left, y: p.top, w: COL2, label: g[0].label, rows: g[0].rows, valueHead: S.viewers.value_head, valueW: 1.05 });
    const R = barTable(p, { x: p.left + COL2 + GUTTER, y: p.top, w: COL2, label: g[1].label, rows: g[1].rows, valueHead: S.viewers.value_head, valueW: 1.05 });
    let y = Math.max(L.bottom, R.bottom) + 0.24;
    y = statStrip(p, S.viewers.strip, y);
    sowhat(p, S.viewers.sowhat, y + 0.16);
  }

  // 6 Premiere strength: two ranked lists, two stats, so-what.
  {
    const p = cw.page(pres, { mode: "dark", eyebrow: "05 — THE PREMIERE", title: S.premiere.title, subhead: S.premiere.subhead, source: S.source });
    const L = barTable(p, { x: p.left, y: p.top, w: COL2, label: S.premiere.left.label, rows: S.premiere.left.rows, valueHead: S.premiere.left.value_head });
    const R = barTable(p, { x: p.left + COL2 + GUTTER, y: p.top, w: COL2, label: S.premiere.right.label, rows: S.premiere.right.rows, valueHead: S.premiere.right.value_head });
    let y = Math.max(L.bottom, R.bottom) + 0.24;
    y = statStrip(p, S.premiere.stats, y);
    sowhat(p, S.premiere.sowhat, y + 0.16);
  }

  // 7 New vs reactivated: split bars.
  {
    const p = cw.page(pres, { mode: "light", eyebrow: "06 — NEW VERSUS REACTIVATED", title: S.split.title, subhead: S.split.subhead, source: S.source });
    const x = p.left, nameW = 3.0, numW = 2.7, barW = p.width - nameW - numW - 0.3;
    let y = p.top;
    caps(p, "FIRST-TIME (GREEN) · REACTIVATED (PURPLE)", { x: x + nameW, y, w: barW });
    caps(p, "ACCOUNTS", { x: x + nameW + barW + 0.3, y, w: numW });
    y += 0.32;
    const R = p.pres.ShapeType.roundRect, bh = 0.2, pitch = 0.40;
    S.split.rows.forEach((r, i) => {
      const ry = y + i * pitch;
      cw.text(p, r.name, { x, y: ry, w: nameW - 0.15, h: 0.3, size: 11.5, weight: r.emphasis ? "semibold" : "regular", color: "title", valign: "middle", noWidowCheck: true });
      const nw = barW * (r.new_pct / 100);
      p.slide.addShape(R, { x: x + nameW, y: ry + (0.3 - bh) / 2, w: barW, h: bh, rectRadius: bh / 2, fill: { color: p.c.negative }, line: { type: "none" } });
      p.slide.addShape(R, { x: x + nameW, y: ry + (0.3 - bh) / 2, w: nw, h: bh, rectRadius: bh / 2, fill: { color: r.emphasis ? p.c.accent : "8A9A5B" }, line: { type: "none" } });
      cw.text(p, r.new_pct.toFixed(1) + "%", { x: x + nameW + 0.12, y: ry, w: 1.0, h: 0.3, size: 10.5, weight: "bold", color: "E9E8E1", valign: "middle", chrome: true, noWidowCheck: true });
      cw.text(p, r.react_pct.toFixed(1) + "%", { x: x + nameW + barW - 1.12, y: ry, w: 1.0, h: 0.3, size: 10.5, weight: "bold", color: "E9E8E1", valign: "middle", align: "right", chrome: true, noWidowCheck: true });
      cw.text(p, r.new + " new · " + r.react + " back", { x: x + nameW + barW + 0.3, y: ry, w: numW, h: 0.3, size: 11, color: r.emphasis ? "accent" : "body", weight: r.emphasis ? "semibold" : "regular", valign: "middle", noWidowCheck: true });
    });
    sowhat(p, S.split.sowhat, y + S.split.rows.length * pitch + 0.22);
  }

  // 8 Overlaps table
  {
    const p = cw.page(pres, { mode: "dark", eyebrow: "07 — WHERE THE VIEWERS ALSO SIT", title: S.overlap.title, subhead: S.overlap.subhead, source: S.source });
    const colW = [3.493, 2.0, 2.0, 2.0, 2.0];
    const t = table(p, { x: p.left, y: p.top, w: p.width, columns: S.overlap.columns, colW, rows: S.overlap.rows, headH: 0.34, rowH: 0.32, size: 11 });
    sowhat(p, S.overlap.sowhat, t.bottom + 0.34);
  }

  // 9 Normalization table
  {
    const p = cw.page(pres, { mode: "light", eyebrow: "08 — PER EPISODE AND PER WEEK", title: S.normalize.title, subhead: S.normalize.subhead, source: S.source });
    const colW = [2.45, 1.3, 1.0, 1.2, 1.4, 1.35, 1.35, 1.443];
    const t = table(p, { x: p.left, y: p.top, w: p.width, columns: S.normalize.columns, colW, rows: S.normalize.rows, headH: 0.52, rowH: 0.38, size: 11, headTracking: 1.0 });
    let y = t.bottom + 0.3;
    S.normalize.notes.forEach((n) => {
      const lines = cw.estLines(n, 11.5, p.width, "regular");
      cw.text(p, n, { x: p.left, y, w: p.width, size: 11.5, color: "body", noWidowCheck: true });
      y += lines * 0.215 + 0.12;
    });
    sowhat(p, S.normalize.sowhat, y + 0.1);
  }

  // 10 What this means: the pale Signal gradient page, three paragraphs of different lengths.
  {
    const p = cw.page(pres, { mode: "signal", eyebrow: "09 — WHAT THIS MEANS", title: S.means.title, subhead: S.means.subhead, source: S.source });
    let y = p.top + 0.05;
    S.means.paragraphs.forEach((t, i) => {
      cw.thinNumeral(p, "0" + (i + 1), { x: p.left, y: y - 0.06, w: 0.9, size: 30, h: 0.5, color: "title" });
      const w = p.width - 1.1;
      const lines = cw.estLines(t, 13.5, w, "regular");
      cw.text(p, t, { x: p.left + 1.1, y, w, size: 13.5, color: "title", noWidowCheck: true, lineSpacing: 20 });
      y += lines * 0.28 + 0.34;
    });
  }

  // 11 Closing: Graphite, large logo, weight pair, one line, contacts.
  {
    const p = cw.page(pres, { mode: "dark", logo: false });
    cw.logo(p, { x: 0.92, y: 0.62, w: 2.6 });
    cw.text(p, S.closing.title, { x: 0.92, y: 2.75, w: 11.3, h: 0.75, size: 40, weight: "bold", color: "title", lineSpacing: 46 });
    cw.text(p, S.closing.line, { x: 0.92, y: 4.0, w: 8.6, size: 14, color: "body", noWidowCheck: true });
    cw.rule(p, { x: 0.92, y: 5.3, w: 0.6, h: 0.03, color: "accent" });
    S.closing.contacts.forEach((c, i) => cw.text(p, [{ text: "", weight: "regular" }, { text: c, weight: "regular" }], { x: 0.92, y: 5.48 + i * 0.3, w: 6, h: 0.28, size: 12, color: i === 0 ? "title" : "body", noWidowCheck: true }));
  }

  await cw.save(pres, outPath);
})().catch((e) => { console.error(e); process.exit(1); });
"""


def resolve_skill_root(arg: str | None) -> Path:
    cands = []
    if arg:
        cands.append(Path(arg).expanduser())
    env = os.environ.get("CROSSWALK_DESIGN_SKILL", "").strip()
    if env:
        cands.append(Path(env).expanduser())
    try:
        from migration import design_skill  # type: ignore
        full = design_skill.full_skill_root()
        if full:
            cands.append(Path(full))
    except Exception:
        pass
    cands.append(WEBAPP.parent / ".cursor" / "skills" / "crosswalk-design")
    for c in cands:
        if (c / "scripts" / "cw_deck.js").is_file():
            return c
    raise SystemExit("cw_deck.js not found: pass --skill-root or set CROSSWALK_DESIGN_SKILL to the full crosswalk-design skill")


def build_deck(spec: dict, work: Path, out_dir: Path, skill_root: Path, make_pdf: bool, checks: bool) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    spec_path = work / "deck_spec.json"
    spec_path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    js_path = work / "build_deck.js"
    js_path.write_text(DECK_JS, encoding="utf-8")
    pptx = out_dir / f"{spec['stem']}.pptx"
    r = subprocess.run(["node", str(js_path), str(spec_path), str(pptx), str(skill_root)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit("deck build failed")
    warnings = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
    for ln in warnings:
        print("  " + ln)
    report = {"pptx": str(pptx), "builder_warnings": warnings}
    scripts = skill_root / "scripts"
    if make_pdf:
        soffice = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        pdf_dir = work / "pdf"
        pdf_dir.mkdir(exist_ok=True)
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), str(pptx)], check=True, capture_output=True)
        pdf = out_dir / f"{spec['stem']}.pdf"
        shutil.move(str(pdf_dir / f"{spec['stem']}.pdf"), str(pdf))
        report["pdf"] = str(pdf)
    if checks:
        for label, args in (
            ("embed (pptx, render check)", [str(scripts / "cw_embed_fonts.py"), str(pptx), "--render-check"]),
            ("lint (pptx)", [str(scripts / "cw_brand_lint.py"), str(pptx)]),
            ("readability (pptx)", [str(scripts / "readability_check.py"), str(pptx)]),
        ):
            rr = subprocess.run([sys.executable] + args, capture_output=True, text=True)
            report[label] = (rr.stdout + rr.stderr).strip()
        if make_pdf:
            rr = subprocess.run([sys.executable, str(scripts / "cw_embed_fonts.py"), report["pdf"]], capture_output=True, text=True)
            report["embed (pdf)"] = (rr.stdout + rr.stderr).strip()
        prev = work / "preview"
        prev.mkdir(exist_ok=True)
        rr = subprocess.run([sys.executable, str(scripts / "render_preview.py"), str(pptx), "--out", str(prev)], capture_output=True, text=True)
        report["preview"] = (rr.stdout + rr.stderr).strip()[-600:]
    return report


# ---------------------------------------------------------------------------
# internal underpinnings (never ships)
# ---------------------------------------------------------------------------
def write_underpinnings(reads, bm, spec, outputs, path: Path):
    lib = bm["library"]
    lines = [
        "# Starz series benchmark, 2026-10-29 (internal underpinnings, never ships)",
        "",
        "Ask: Jenna's client on the S.W.A.T. Exiles Subscriber IQ read: \"Is there a way to benchmark this against other Starz series? "
        "I'm curious if the 49K+ / 2.66% is bigger?\" Jenna: \"can you email me a report on all starz series so she can see how this performs.\"",
        "",
        "Builder: bg-webapp `scripts/build_starz_series_benchmark.py` (read-only on every tracker; nothing re-derived or re-leveled).",
        "",
        "## Library (every tracker in s3://svod-acquisition/ whose Platform Tracked is Starz)",
        "",
        "| id | key | dashboard category | window | type | eps | viewers | signups | new | react | conv all | conv first-time |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reads:
        lines.append(f"| {r['id']} | {r['key']} | {r['dashboard_category']} | {r['window_start']} to {r['window_end']} ({r['window_days']} d) | {r['window_type']} | "
                     f"{r['episodes_in_window']} | {r['viewers']:,} | {r['signups']:,} | {r['new_accounts']:,} | {r['reactivated_accounts']:,} | "
                     f"{r['conversion_all_viewers_pct']} | {r['conversion_first_time_viewers_pct']} |")
    lines += [
        "",
        "Starz account bases (ledger subject `starz`, metric `us_streaming_accounts`): Q2 2025 12,417,363 for the 2025 window; Q2 2026 12,724,671 for the 2026 windows; "
        "no shipped 2022 base, so P-Valley S2 carries no share of accounts.",
        "",
        "Conversion = TOTAL SIGNUPS projection / Total Show Watchers projection (the file's Total Show Conversion Rate). For the two returning-season reads "
        "(Blood of My Blood S2 premiere, P-Valley S2) the file's Clean Conversion Rate (first-time viewers) is carried as a second column; the like-for-like is the all-viewers rate.",
        "",
        "## Library medians and ranges (all reads)",
        "",
    ]
    for m, d in lib.items():
        lines.append(f"- {m}: median {round(d['median'], 4)}, range {d['min']} to {d['max']}, n={d['n']}")
    lines += ["", "## Group medians", ""]
    for g, d in bm["group_stats"].items():
        for m, v in d.items():
            lines.append(f"- {g} / {m}: median {round(v['median'], 4)}, range {round(v['min'], 4)} to {round(v['max'], 4)}, n={v['n']}")
    lines += ["", "## Ranks (within window type)", ""]
    for r in reads:
        lines.append(f"- {r['id']}: " + ", ".join(f"{m} {rk[0]}/{rk[1]}" for m, rk in (r.get('rank') or {}).items()))
    lines += ["", "## Derived figures used in the copy", ""]
    for r in reads:
        extra = ""
        if r.get("premiere_window_share_of_viewers_pct") is not None:
            extra = f"; premiere weekend = {r['premiere_window_share_of_viewers_pct']}% of season viewers, {r['premiere_window_share_of_signups_pct']}% of season signups"
        pace = (f"signups/episode {r['signups_per_episode']:.1f}, signups/week {r['signups_per_week']:.1f}, viewers/week {r['viewers_per_week']:.1f}"
                if r["signups_per_episode"] is not None else "premiere weekend, no per-episode pace")
        share = f"{r['share_of_starz_accounts_pct']}%" if r['share_of_starz_accounts_pct'] is not None else "n/a (no 2022 base)"
        lines.append(f"- {r['id']}: {pace}, new share {r['new_share_pct']}%, share of Starz accounts {share}{extra}")
    lines += [
        "",
        "## Notes for the operator",
        "",
        "- Outlander: Blood of My Blood S2 premiere is filed under SERIES - SONY in system/svod_metadata.json, not SERIES - STARZ; P-Valley S2 under SERIES - STARZ ORIGINAL. "
        "Both render under the STARZ parent in the Subscriber IQ rail except the S2 read, which sits under SONY.",
        "- Blood of My Blood S1 opening-window file carries MAX 42.34 while the full-season file carries HBO MAX 59.34 for the same audience; both shipped, both reported as shipped, "
        "HBO Max is not in the deck's overlap table (Prime Video, Netflix, Hulu, Paramount+ only).",
        "- Ledger lines (reports/_ledger/shipped_numbers.jsonl) exist for S.W.A.T. Exiles and Starz only; Blood of My Blood and P-Valley figures are taken from their trackers (no ledger lines).",
        "- The Subscriber IQ tab has a Compare tab in code (templates/index.html `subscriber-tab-compare`, `renderSubComparisonTable`) but its button `subscriberCompareTabBtn` "
        "is display:none with no toggle, and the Compare All modal's button element does not exist in the DOM, so no cross-read view is reachable today.",
        "",
        "## Outputs",
        "",
    ]
    for k, v in outputs.items():
        lines.append(f"- {k}: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# ledgers
# ---------------------------------------------------------------------------
def ledger_metrics(reads, bm):
    lib = bm["library"]
    swat = by_id(reads, "swat_s1_to_date")
    swat_pw = by_id(reads, "swat_s1_premiere")
    n = bm["n_reads"]
    out = [
        {"name": "starz_library_reads", "label": "Starz series reads in the Subscriber IQ library", "value": n, "unit": "reads",
         "definition": f"{bm['n_series']} series, {bm['n_seasons']} seasons; premiere weekends and season reads as of 2026-10-29"},
        {"name": "starz_conversion_median_pct", "label": "Starz library median signup conversion", "value": round(lib["conversion_all_viewers_pct"]["median"], 2), "unit": "pct",
         "definition": f"median of signups / viewers across {n} Starz reads; range {lib['conversion_all_viewers_pct']['min']} to {lib['conversion_all_viewers_pct']['max']}"},
        {"name": "starz_conversion_min_pct", "label": "Starz library lowest signup conversion", "value": lib["conversion_all_viewers_pct"]["min"], "unit": "pct", "definition": "P-Valley S2 full season, 2022 window"},
        {"name": "starz_conversion_max_pct", "label": "Starz library highest signup conversion", "value": lib["conversion_all_viewers_pct"]["max"], "unit": "pct", "definition": "Blood of My Blood S2 premiere weekend, returning season"},
        {"name": "starz_new_share_median_pct", "label": "Starz library median first-time share of signups", "value": round(lib["new_share_pct"]["median"], 1), "unit": "pct", "definition": "median across the Starz reads"},
        {"name": "starz_prime_video_overlap_median_pct", "label": "Starz library median Prime Video overlap", "value": round(lib["overlap_Prime Video"]["median"], 2), "unit": "pct", "definition": "median share of viewers also on Prime Video"},
        {"name": "swat_exiles_conversion_rank_season_reads", "label": "S.W.A.T. Exiles S1 to date conversion rank among Starz season reads", "value": swat["rank"]["conversion_all_viewers_pct"][0], "unit": "rank",
         "definition": f"of {swat['rank']['conversion_all_viewers_pct'][1]} season reads at {swat['conversion_all_viewers_pct']}%"},
        {"name": "swat_exiles_signups_per_aired_episode", "label": "S.W.A.T. Exiles S1 to date signups per aired episode", "value": round(swat["signups_per_episode"], 1), "unit": "accounts",
         "definition": "49,782 signups over five aired episodes; first of three Starz season reads"},
        {"name": "swat_exiles_premiere_conversion_rank", "label": "S.W.A.T. Exiles premiere weekend conversion rank among Starz premieres", "value": swat_pw["rank"]["conversion_all_viewers_pct"][0], "unit": "rank",
         "definition": f"of {swat_pw['rank']['conversion_all_viewers_pct'][1]} premiere weekends at {swat_pw['conversion_all_viewers_pct']}%"},
    ]
    return out


def write_ledgers(reads, bm, deliverable: str, shipped_jsonl: Path | None):
    metrics = ledger_metrics(reads, bm)
    today = date.today().isoformat()
    if shipped_jsonl is not None:
        with shipped_jsonl.open("a", encoding="utf-8") as fh:
            for m in metrics:
                fh.write(json.dumps({"subject": "starz", "metric": m["name"], "window": "library..2026-10-29", "value": m["value"],
                                     "deliverable": deliverable, "date": today}) + "\n")
        print(f"[starz-benchmark] appended {len(metrics)} lines to {shipped_jsonl}")
    try:
        import insights_ledger  # type: ignore
        insights_ledger.persist(
            subject="Starz", metric_family="subscriber_acquisition",
            question="How does S.W.A.T. Exiles Season 1 (49,782 signups, 2.66%) benchmark against the other Starz series reads?",
            route="starz_series_benchmark", metrics=metrics[:10],
            window_start="2022-06-03", window_end="2026-10-29", window_label="Starz Subscriber IQ library as of Oct 29, 2026",
            reply=("S.W.A.T. Exiles Season 1 to date converts 2.66%, first of three Starz season reads (Blood of My Blood S1 2.58%, P-Valley S2 0.98%) and third of six Starz reads overall; "
                   "its premiere weekend 2.72% is second of three premieres behind Blood of My Blood S2's returning-season 3.60%. Library median conversion 2.65%, range 0.98% to 3.60%. "
                   "Signups per aired episode 9,956.4, the highest in the library; first-time share 77.8%, also the highest."),
            base_profile_key="svod-acquisition/SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv",
            provenance="delivered_artifact", sync=True)
        print("[starz-benchmark] insights ledger entry persisted (subject Starz)")
    except Exception as e:
        print(f"[starz-benchmark] WARN insights ledger persist failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(Path.home() / "Downloads"))
    ap.add_argument("--work-dir", default="/tmp/starz_benchmark")
    ap.add_argument("--reads-dir", default=None, help="local copies of the trackers instead of S3")
    ap.add_argument("--skill-root", default=None)
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--no-deck", action="store_true", help="CSV, JSON and underpinnings only")
    ap.add_argument("--ledger", action="store_true", help="record the medians in the insights ledger (and the shipped-numbers ledger if --shipped-ledger is given)")
    ap.add_argument("--shipped-ledger", default=None, help="path to reports/_ledger/shipped_numbers.jsonl")
    a = ap.parse_args()

    out_dir, work = Path(a.out_dir).expanduser(), Path(a.work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    reads = load_library(Path(a.reads_dir) if a.reads_dir else None)
    if not reads:
        raise SystemExit("no Starz reads found")
    bm = benchmark(reads)
    print(f"[starz-benchmark] {bm['n_reads']} Starz reads, {bm['n_series']} series, {bm['n_seasons']} seasons")
    for r in reads:
        print(f"  {r['id']:<18} {r['window_type']:<15} viewers {r['viewers']:>9,} signups {r['signups']:>6,} conv {r['conversion_all_viewers_pct']}%")

    csv_path = out_dir / f"{DECK_STEM}.csv"
    write_csv(reads, csv_path)
    (work / "benchmark.json").write_text(json.dumps({"reads": reads, "benchmark": {k: v for k, v in bm.items() if k != "groups"}}, indent=1, default=str), encoding="utf-8")
    spec = deck_spec(reads, bm)
    (work / "deck_spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    outputs = {"csv": str(csv_path)}
    if not a.no_deck:
        skill_root = resolve_skill_root(a.skill_root)
        report = build_deck(spec, work, out_dir, skill_root, make_pdf=not a.no_pdf, checks=not a.no_checks)
        outputs.update({k: v for k, v in report.items() if k in ("pptx", "pdf")})
        (work / "check_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        for k, v in report.items():
            if k in ("pptx", "pdf", "builder_warnings"):
                continue
            print(f"\n=== {k} ===\n{v}")
    write_underpinnings(reads, bm, spec, outputs, work / "UNDERPINNINGS.md")
    if a.ledger:
        write_ledgers(reads, bm, deliverable=f"{DECK_STEM}.pdf", shipped_jsonl=Path(a.shipped_ledger) if a.shipped_ledger else None)
    print("\n[starz-benchmark] done")
    for k, v in outputs.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
