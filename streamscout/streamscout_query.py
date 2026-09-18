#!/usr/bin/env python3
"""streamscout_query.py — the non-interactive "drive-thru window" for StreamScout.

Prometheus (or any automation) calls this instead of driving the interactive
menu. Give it a title and one or more platforms; it runs the **same resolvers
the menu uses** and returns content_map-ready rows:

    [{"SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"}, ...]

Nothing new is resolved here — this just calls ``streamscout.resolver_lookup()``
(the exact code path behind the menu) without the prompts, attaches the
PRODUCTION tag the same way the CSV writer does, and shapes each row to the
content_map schema. One platform failing never sinks the batch (hands-off).

Typical uses
------------
    from streamscout_query import streamscout_query

    # a book (single title)
    rows = streamscout_query("Cruel Saints", platforms="books", kind="series")

    # a whole franchise → stamp every row with the franchise SHOW in one call
    rows = []
    for t in ["Merciless Saints", "Cruel Saints", "Ruthless Saints"]:
        rows += streamscout_query(t, platforms="books", show="Merciless Saints")

    # a podcast's audience (listeners)
    rows = streamscout_query("The Daily", platforms="podcasts", kind="series")

CLI (handy for spot checks):
    python3 streamscout_query.py --title "Cruel Saints" --platforms books
    python3 streamscout_query.py --title "Hades" --platforms games --show Hades
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import streamscout as ss  # noqa: E402  (needs the path insert above)

# Convenience platform groups so a caller can say platforms="podcasts" for a
# listener profile, "video" for viewers, etc. All are resolver-based. Peacock is
# slug/ClickHouse (needs the company network) and is intentionally NOT here —
# query it through the interactive tool.
GROUPS = {
    "video":    ["hulu", "netflix", "appletv", "paramount", "max", "disney",
                 "starz", "hallmark", "amazon", "mgmplus", "britbox", "youtube"],
    "podcasts": ["spotify", "applepodcasts", "iheart", "pandora",
                 "amazonpodcasts", "siriusxm"],
    "audio":    ["audible"],
    "books":    ["books"],
    "games":    ["games"],
}
GROUPS["all"] = [p for g in ("video", "podcasts", "audio", "books", "games")
                 for p in GROUPS[g]]


def _platform_keys(platforms):
    """Expand a platform key / list / group name into concrete resolver keys."""
    if platforms is None:
        platforms = ["all"]
    if isinstance(platforms, str):
        platforms = [platforms]
    keys = []
    for p in platforms:
        p = (p or "").strip().lower()
        if p in GROUPS:
            keys.extend(GROUPS[p])
        elif p in ss.RESOLVER_PLATFORMS:
            keys.append(p)
        else:
            raise ValueError(f"unknown platform/group: {p!r} "
                             f"(groups: {', '.join(sorted(GROUPS))})")
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def streamscout_query(title, platforms=None, kind="series", seasons=None,
                      url=None, show=None):
    """Resolve ``title`` on the given platform(s); return content_map rows.

    Parameters
    ----------
    title : str
        The title (or a franchise's individual title) to resolve.
    platforms : str | list | None
        A platform key ("books"), a list of keys, or a group name
        ("video" | "podcasts" | "audio" | "books" | "games" | "all").
        Default: "all".
    kind : str
        "series" (default) or "movie".
    seasons : iterable | None
        None = all seasons; or an iterable of season numbers (video series).
    url : str | None
        Optional pasted product/episode URL hint (for the URL-hint resolvers).
    show : str | None
        If given, overrides SHOW on every returned row — pass the franchise name
        to roll a whole series under one SHOW in a single call.

    Returns
    -------
    list[dict]  with keys SHOW, URL, PRODUCTION, PLATFORM, SEASON.
    """
    out, seen = [], set()
    for platform in _platform_keys(platforms):
        try:
            rows = ss.resolver_lookup(platform, title, kind, seasons, url=url)
        except Exception:  # noqa: BLE001 — hands-off: skip a failing platform
            continue
        for r in rows:
            row_show = (show or r.get("show") or title or "").strip().upper()
            rec = {
                "SHOW": row_show,
                "URL": r.get("identifier", "") or "",
                "PRODUCTION": ss.production_for(row_show, kind),
                "PLATFORM": r.get("platform") or ss.platform_label(platform),
                "SEASON": ss.season_label(r.get("season", "")),
            }
            key = (rec["SHOW"], rec["SEASON"], rec["URL"], rec["PLATFORM"])
            if rec["URL"] and key not in seen:
                seen.add(key)
                out.append(rec)
    return out


def _main():
    import argparse
    import csv
    import json
    ap = argparse.ArgumentParser(
        prog="streamscout_query.py",
        description="Non-interactive StreamScout query → content_map rows.")
    ap.add_argument("--title", required=True)
    ap.add_argument("--platforms", default="all",
                    help="key, comma-list, or group "
                         "(video|podcasts|audio|books|games|all)")
    ap.add_argument("--kind", default="series", choices=["series", "movie"])
    ap.add_argument("--show", default=None,
                    help="override SHOW on every row (e.g. the franchise name)")
    ap.add_argument("--csv", action="store_true",
                    help="print CSV instead of JSON")
    args = ap.parse_args()
    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    rows = streamscout_query(args.title, platforms=plats, kind=args.kind,
                             show=args.show)
    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([r["SHOW"], r["URL"], r["PRODUCTION"], r["PLATFORM"],
                        r["SEASON"]])
    else:
        print(json.dumps(rows, indent=2))
    print(f"\n{len(rows)} row(s)", file=sys.stderr)


if __name__ == "__main__":
    _main()
