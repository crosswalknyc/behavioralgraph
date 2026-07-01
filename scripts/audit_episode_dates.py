"""Audit episode lists in every SVOD CSV against Claude-validated ground truth.

For each CSV in s3://svod-acquisition/:
  1. Parse the PER-EPISODE ATTRIBUTION section to extract (episode_num, air_date)
  2. Parse the header to get show name + platform
  3. Ask Claude (with web_search) for the canonical episode list for that
     specific season
  4. Diff CSV vs canonical and emit a per-file verdict

Multi-episode same-day drops (binge-then-weekly hybrids like Reacher S2/S3)
are explicitly preserved — every individual episode must show up, even if 3
share the same air date.

Usage:
  cd bg-webapp && python3 scripts/audit_episode_dates.py
  cd bg-webapp && python3 scripts/audit_episode_dates.py --filter Reacher
  cd bg-webapp && python3 scripts/audit_episode_dates.py --filter Reacher --json out.json
"""
from __future__ import annotations

import argparse
import csv as _csv
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import boto3

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if ENV_PATH.exists():
    for _line in ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        k, v = _line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ.setdefault("USE_CLAUDE_REASONING", "1")

BUCKET = os.environ.get("SVOD_BUCKET", "svod-acquisition")


def parse_csv(text: str) -> dict:
    """Extract show, platform, genre, and per-episode rows from a CSV."""
    info = {"show": None, "platform": None, "genre": None,
            "analysis_range": None, "episodes": []}
    rdr = _csv.reader(io.StringIO(text))
    rows = list(rdr)
    in_per_episode = False
    for i, r in enumerate(rows):
        first = (r[0] if r else "").strip()
        # Header lookups
        if i < 25:
            val = next((c.strip() for c in r[1:] if c and c.strip()), "")
            label = first.lower()
            if label.startswith("show/content tracked"):
                info["show"] = val
            elif label.startswith("platform tracked"):
                info["platform"] = val
            elif label.startswith("genre"):
                info["genre"] = val
            elif label.startswith("analysis date range"):
                info["analysis_range"] = val
        # Section tracking
        if "PER-EPISODE ATTRIBUTION" in (",".join(r)).upper():
            in_per_episode = True
            continue
        if in_per_episode:
            if not first or first.startswith(",") or first.startswith("("):
                # Empty/comment row inside the section is fine, keep going
                continue
            if first.lower().startswith("signup timing") or (first.isupper() and "EPISODE" not in first.upper() and len(first) > 4):
                in_per_episode = False
                continue
            m = re.match(r"^Episode\s+(\d+)$", first)
            if m:
                date = (r[1] if len(r) > 1 else "").strip()
                info["episodes"].append({"ep": int(m.group(1)), "date": date})
    # De-dup (CSV often has Episode N appear twice — once in PER-EPISODE
    # ATTRIBUTION with date, once in SIGNUP TIMING PER EPISODE without).
    seen = set()
    dedup = []
    for e in info["episodes"]:
        key = (e["ep"], e["date"])
        if key in seen:
            continue
        seen.add(key)
        # Skip the "no date" row (it's the timing section header)
        if not e["date"]:
            continue
        dedup.append(e)
    info["episodes"] = sorted(dedup, key=lambda e: (e["ep"], e["date"]))
    return info


def claude_validate(show: str, platform: str, ep_count: int,
                    first_date: str, last_date: str) -> dict | None:
    """Ask Claude for the canonical episode list for this season.

    Returns:
      {
        "episodes":       [{"ep": 1, "date": "YYYY-MM-DD", "title": "..."}, ...],
        "release_pattern": "weekly" | "binge" | "binge_then_weekly" | "split" | "unknown",
        "confidence":     "high" | "medium" | "low",
        "sources":        ["..."],
        "notes":          "..."
      }
    """
    try:
        from claude_client import claude_messages, is_claude_reasoning_enabled
    except Exception:
        return None
    if not is_claude_reasoning_enabled():
        return None

    prompt = (
        f"Find the canonical episode list for this specific season on this "
        f"specific platform. Use web_search to verify against Wikipedia, "
        f"IMDb, official platform pages, or trade press.\n\n"
        f"SHOW: {show}\n"
        f"PLATFORM: {platform}\n"
        f"CSV currently lists: {ep_count} episodes, first {first_date or '?'}, "
        f"last {last_date or '?'}\n\n"
        f"Return JSON ONLY:\n"
        f"{{\n"
        f'  "season_resolved": "<e.g. Season 3>",\n'
        f'  "release_pattern": "weekly" | "binge" | "binge_then_weekly" | "split" | "unknown",\n'
        f'  "episodes": [\n'
        f'    {{"ep": 1, "date": "YYYY-MM-DD", "title": "..."}},\n'
        f'    ...\n'
        f'  ],\n'
        f'  "confidence": "high" | "medium" | "low",\n'
        f'  "sources":    ["wikipedia url", "imdb url", ...],\n'
        f'  "notes":      "<anything unusual: multi-ep premiere, mid-season break, etc.>"\n'
        f"}}\n\n"
        f"CRITICAL: list EVERY episode individually even if multiple share an "
        f"air date (Reacher S3 dropped Eps 1-3 on 2025-02-20 — list all three). "
        f"If you can't find authoritative sources, set confidence=low and "
        f"explain why in notes. Do NOT make up episodes."
    )

    raw = claude_messages(
        system="You are a TV-release-data analyst. Respond with JSON only.",
        user=prompt,
        max_tokens=2000,
        temperature=0.1,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
    )
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def parse_date(s: str) -> datetime | None:
    """Accept M/D/YY, M/D/YYYY, or YYYY-MM-DD."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def diff_episodes(csv_eps: list, canonical_eps: list) -> dict:
    """Compare CSV episodes vs canonical, ignoring date-format differences."""
    csv_set = set()
    for e in csv_eps:
        d = parse_date(e.get("date", ""))
        if d:
            csv_set.add((e["ep"], d.date().isoformat()))
    can_set = set()
    for e in canonical_eps:
        d = parse_date(e.get("date", ""))
        if d:
            can_set.add((e["ep"], d.date().isoformat()))
    missing_in_csv  = sorted(can_set - csv_set)
    extra_in_csv    = sorted(csv_set - can_set)
    same_day_groups = {}
    for ep, d in can_set:
        same_day_groups.setdefault(d, []).append(ep)
    multi_drops = {d: sorted(eps) for d, eps in same_day_groups.items() if len(eps) > 1}
    return {
        "csv_count":       len(csv_set),
        "canonical_count": len(can_set),
        "missing_in_csv":  missing_in_csv,
        "extra_in_csv":    extra_in_csv,
        "multi_drop_days": multi_drops,
        "exact_match":     csv_set == can_set,
    }


def verdict(diff: dict, confidence: str) -> str:
    if diff["exact_match"]:
        return "OK"
    if diff["csv_count"] < diff["canonical_count"]:
        return "MISSING_EPISODES"
    if diff["extra_in_csv"]:
        return "EXTRA_OR_WRONG_DATES"
    return "DATE_MISMATCH"


def process_one(s3, key: str) -> dict:
    res = {"key": key, "show": None, "platform": None,
           "csv_eps": [], "canonical_eps": [], "diff": None,
           "verdict": "?", "confidence": "?", "release_pattern": "?",
           "sources": [], "notes": "", "error": None}
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", "replace")
        parsed = parse_csv(body)
        res["show"] = parsed["show"]
        res["platform"] = parsed["platform"]
        res["csv_eps"] = parsed["episodes"]
        if not parsed["show"] or not parsed["platform"]:
            res["verdict"] = "SKIP_NO_HEADER"
            return res
        first = parsed["episodes"][0]["date"] if parsed["episodes"] else ""
        last = parsed["episodes"][-1]["date"] if parsed["episodes"] else ""
        truth = claude_validate(parsed["show"], parsed["platform"],
                                len(parsed["episodes"]), first, last)
        if not truth or not isinstance(truth.get("episodes"), list):
            res["verdict"] = "CLAUDE_FAILED"
            return res
        res["canonical_eps"] = truth.get("episodes") or []
        res["confidence"] = truth.get("confidence", "?")
        res["release_pattern"] = truth.get("release_pattern", "?")
        res["sources"] = truth.get("sources") or []
        res["notes"] = truth.get("notes", "")
        res["diff"] = diff_episodes(res["csv_eps"], res["canonical_eps"])
        res["verdict"] = verdict(res["diff"], res["confidence"])
        return res
    except Exception as e:
        res["verdict"] = "ERROR"
        res["error"] = f"{type(e).__name__}: {e}"
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="")
    ap.add_argument("--max-workers", type=int, default=3,
                    help="parallel Claude calls (web_search counts against RPM)")
    ap.add_argument("--json", default="",
                    help="write full audit json to this file (useful for follow-up fixes)")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    s3 = boto3.client("s3")
    pager = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in pager.paginate(Bucket=BUCKET):
        for o in (page.get("Contents") or []):
            k = o["Key"]
            if k.endswith(".csv") and ("/" not in k or k.startswith("purgatory/")):
                if not args.filter or args.filter.lower() in k.lower():
                    keys.append(k)

    print(f"📦 Auditing {len(keys)} CSV{'s' if len(keys)!=1 else ''}")
    if args.filter:
        print(f"   filter: {args.filter!r}")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(process_one, s3, k): k for k in keys}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            verdict_str = r["verdict"]
            short = (r["show"] or r["key"])[:50]
            d = r.get("diff") or {}
            csv_n = d.get("csv_count", "?")
            can_n = d.get("canonical_count", "?")
            icon = {"OK": "✅", "MISSING_EPISODES": "❌", "EXTRA_OR_WRONG_DATES": "⚠️",
                    "DATE_MISMATCH": "⚠️", "CLAUDE_FAILED": "❓", "SKIP_NO_HEADER": "⚪",
                    "ERROR": "💥"}.get(verdict_str, "?")
            print(f"  [{i:3d}/{len(keys)}] {icon} {verdict_str:<22} {short:<50} "
                  f"CSV={csv_n} truth={can_n} conf={r['confidence']}")
            if r.get("error"):
                print(f"                  └─ {r['error']}")
            if d.get("missing_in_csv"):
                miss = ", ".join(f"Ep{n}@{dt}" for n, dt in d["missing_in_csv"][:6])
                print(f"                  └─ MISSING: {miss}{'…' if len(d['missing_in_csv'])>6 else ''}")

    print()
    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    print("📊 SUMMARY")
    for v, n in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
        print(f"   {v:<22} {n}")

    if args.json:
        # Strip non-JSON-friendly fields
        for r in results:
            d = r.get("diff")
            if d and "multi_drop_days" in d:
                d["multi_drop_days"] = {k: v for k, v in d["multi_drop_days"].items()}
        Path(args.json).write_text(json.dumps(results, indent=2, default=str))
        print(f"\n💾 Full audit JSON written to: {args.json}")


if __name__ == "__main__":
    main()
