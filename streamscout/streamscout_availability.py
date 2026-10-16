#!/usr/bin/env python3
"""streamscout_availability.py — advisory "is it currently on this platform?" check.

A safeguard against client error (Jess + Spike, 2026-10-16). When a profile is
scoped to specific platform(s) — "Gilmore Girls, Hulu viewers only" — this does a
lightweight **"where to watch {title}"** web lookup and reports, per requested
platform, whether the title appears to be **currently available** there. It's an
**advisory** (never blocks): the data is a public where-to-watch search, so treat
a "not available" as a heads-up to confirm, not a hard truth.

Why this exists (and why it's independent of resolving)
-------------------------------------------------------
The video resolvers already hit each platform's LIVE catalog, so a fresh
``streamscout_query(..., strict=True)`` already knows availability (0 seeds =>
not there). But a profile can be built from **preloaded content_map seeds**
without ever re-resolving — that's exactly how a stale "Gilmore Girls on Netflix"
seed set slipped through after the title left Netflix. This check needs **no
resolve**: one web search tells Promy "those preloaded Hulu seeds still line up"
or "heads-up: this doesn't look like it's on Netflix anymore" — regardless of how
the seeds were produced.

NOTE on history vs. now: this only answers **currently available?**. Old seeds for
a platform the title has since LEFT are kept on purpose — past-window viewership
behavior is legitimately queryable. This check is about whether StreamScout can
currently *access* a platform, not about pruning history.

Credentials
-----------
Reuses the **same shared web-search key** SiriusXM discovery uses — set ONE of:
    BRAVE_SEARCH_API_KEY=…                       # Brave Search API, or
    GOOGLE_SEARCH_API_KEY=…  GOOGLE_SEARCH_CX=…  # Google Programmable Search
No key configured => every platform comes back ``available=None`` ("unknown"),
still non-blocking.

Usage
-----
    from streamscout_availability import check_availability
    report = check_availability("Gilmore Girls", platforms=["hulu", "disney"])
    # -> [{"platform":"hulu","label":"Hulu","available":True,"note":...},
    #     {"platform":"disney","label":"Disney Plus","available":False,"note":...}]

    python3 streamscout_availability.py --title "Gilmore Girls" --platforms hulu,disney
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import streamscout as ss  # noqa: E402
# Reuse the exact web-search provider + env reader SiriusXM uses (same shared
# key, same Brave/Google selection, same env-var promotion) so the two can't
# drift apart.
from siriusxm_episode_identifier import (  # noqa: E402
    _websearch_results as _search, load_env as _load_env)
from streamscout_query import _platform_keys  # noqa: E402

# Streaming brand strings to look for in a "where to watch" result. Only video
# platforms are meaningful here; audio/book/game keys return available=None.
_AVAIL_ALIASES = {
    "netflix":   ["netflix"],
    "hulu":      ["hulu"],
    "disney":    ["disney+", "disney plus", "disneyplus"],
    "max":       ["hbo max", "hbomax", "max.com", " max "],
    "paramount": ["paramount+", "paramount plus"],
    "appletv":   ["apple tv", "apple tv+", "appletv", "tv.apple.com"],
    "amazon":    ["prime video", "amazon prime", "amazon video", "primevideo"],
    "starz":     ["starz"],
    "hallmark":  ["hallmark"],
    "mgmplus":   ["mgm+", "mgm plus", "mgmplus"],
    "britbox":   ["britbox"],
    "youtube":   ["youtube"],
}


def _search_blob(title, env):
    """Combined lowercased text from a where-to-watch search (or '' if no key)."""
    seen, parts = set(), []
    for q in ('where to watch "%s" streaming' % title,
              '"%s" streaming available on' % title):
        for blob in (_search(q, env) or []):
            b = (blob or "")
            if b and b not in seen:
                seen.add(b)
                parts.append(b)
    return " ".join(parts).lower()


def check_availability(title, platforms=None, env=None):
    """Advisory current-availability per requested platform via a web lookup.

    Returns a list of dicts, one per resolved platform key:
        {"platform": key, "label": display, "available": True|False|None,
         "note": human-readable}
    ``available`` is None when the check can't be made (no search key, search
    failed, or the platform isn't a streaming platform we can detect). This
    NEVER raises on a search problem and NEVER blocks — it's a heads-up only.
    """
    env = env or _load_env()
    keys = _platform_keys(platforms)
    blob = _search_blob(title, env)
    have_signal = bool(blob.strip())
    report = []
    for k in keys:
        label = ss.platform_label(k)
        aliases = _AVAIL_ALIASES.get(k)
        if not aliases:
            report.append({"platform": k, "label": label, "available": None,
                           "note": "availability check n/a for this platform"})
            continue
        if not have_signal:
            report.append({"platform": k, "label": label, "available": None,
                           "note": "no web-search key set — availability unknown"})
            continue
        hit = any(a in blob for a in aliases)
        report.append({
            "platform": k, "label": label, "available": hit,
            "note": ("appears currently available on %s" % label) if hit else
                    ("not found on %s in a where-to-watch search — confirm before "
                     "profiling %s viewers" % (label, label)),
        })
    return report


def warnings_from(report):
    """Just the advisory lines for platforms that look unavailable (available is
    False). Promy can surface these to the client; empty list == all clear."""
    return ["\u26a0\ufe0f %s: %s" % (r["label"], r["note"])
            for r in report if r.get("available") is False]


def _main():
    import argparse
    import json
    ap = argparse.ArgumentParser(
        prog="streamscout_availability.py",
        description="Advisory: is {title} currently on the requested platform(s)?")
    ap.add_argument("--title", required=True)
    ap.add_argument("--platforms", default="video",
                    help="key, comma-list, or group (default: video)")
    ap.add_argument("--json", action="store_true", help="print full JSON report")
    args = ap.parse_args()
    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    report = check_availability(args.title, platforms=plats)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report:
            mark = {True: "ok ", False: "??", None: " - "}[r["available"]]
            print(f"  [{mark}] {r['label']}: {r['note']}")
    warns = warnings_from(report)
    print(f"\n{len(warns)} advisory warning(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
