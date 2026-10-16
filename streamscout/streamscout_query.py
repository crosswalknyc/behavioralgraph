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
                 "amazonpodcasts", "siriusxm", "npr", "youtube"],
    "audio":    ["audible"],
    "books":    ["books"],
    "games":    ["games"],
}
GROUPS["all"] = [p for g in ("video", "podcasts", "audio", "books", "games")
                 for p in GROUPS[g]]


def client_message(title, labels):
    """The honest, client-facing line when a scoped platform has no matches —
    e.g. "We can't see Gilmore Girls on Hulu at this time. Notifying Crosswalk
    and will get back to you soon." Promy should relay this verbatim."""
    labels = [str(x) for x in (labels or []) if str(x).strip()]
    if not labels:
        where = "the requested platform"
    elif len(labels) == 1:
        where = labels[0]
    elif len(labels) == 2:
        where = "%s and %s" % (labels[0], labels[1])
    else:
        where = "%s, and %s" % (", ".join(labels[:-1]), labels[-1])
    return ("We can't see %s on %s at this time. Notifying Crosswalk and will "
            "get back to you soon." % (title, where))


class PlatformScopeError(Exception):
    """Raised in strict mode when a client EXPLICITLY asked for a platform but it
    produced zero seeds — so no profile is ever silently built on the wrong
    platform (e.g. "Hulu-only" quietly matching preloaded Netflix seeds). Carries
    the structured detail so callers/Promy can surface or branch on it:
      .title, .missing (platform keys), .missing_labels (display names),
      .counts (per-platform seed counts), .client_message (relay verbatim).
    """

    def __init__(self, title, missing, counts=None):
        self.title = title
        self.missing = list(missing)
        self.counts = dict(counts or {})
        self.missing_labels = [ss.platform_label(p) for p in self.missing]
        self.client_message = client_message(title, self.missing_labels)
        human = ", ".join("%s (0 seeds)" % lbl for lbl in self.missing_labels)
        super().__init__(
            "explicit platform scope not satisfiable for %r: %s. Refusing to "
            "build so no profile runs on the wrong platform. Seed these "
            "platform(s) first (or drop them from the request)." % (title, human))


def _platform_keys(platforms, return_explicit=False):
    """Expand a platform key / list / group name into concrete resolver keys.

    When ``return_explicit`` is True, also return the set of keys the caller named
    DIRECTLY (not via a group/"all") — those are the ones strict mode enforces.
    """
    if platforms is None:
        platforms = ["all"]
    if isinstance(platforms, str):
        platforms = [platforms]
    keys, explicit = [], set()
    for p in platforms:
        p = (p or "").strip().lower()
        if p in GROUPS:
            keys.extend(GROUPS[p])
        elif p in ss.RESOLVER_PLATFORMS:
            keys.append(p)
            explicit.add(p)
        else:
            raise ValueError(f"unknown platform/group: {p!r} "
                             f"(groups: {', '.join(sorted(GROUPS))})")
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return (out, explicit) if return_explicit else out


def streamscout_query(title, platforms=None, kind="series", seasons=None,
                      url=None, show=None, include_clips=None, limit=None,
                      strict=False, notify=False, notify_context=None):
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
    include_clips : bool | None
        YouTube only. False/None (default) = full episodes/videos only (the
        long-form Videos tab). True = also fold in the channel's Shorts tab
        (the clips). Ignored by every non-YouTube platform.
    limit : int | None
        YouTube only. The fair-survey cap: when a channel has more than `limit`
        videos, even-stride down to `limit` across the whole timespan (a
        representative spread, not just the most recent block). None uses the
        resolver default (600); 0 keeps every video. Ignored elsewhere.
    strict : bool
        Explicit-platform scope guard (default False). When True, every platform
        the caller named DIRECTLY (not via a group/"all") must return at least one
        seed; if any comes back empty, raise ``PlatformScopeError`` instead of
        returning a partial set. Use this for client requests that scope to
        specific platform(s) ("Hulu viewers only") so a profile is never silently
        built on the wrong platform (e.g. only Netflix seeds were loaded). Group
        names are not individually enforced — a group is a "whatever's here"
        bucket.
    notify : bool
        Only meaningful with ``strict=True``. When a scoped platform has no
        matches, also email Crosswalk (Jenna + Jess) that the build was refused —
        so a human picks it up instead of anyone quietly substituting another
        platform's seeds. The alert send never raises; the PlatformScopeError is
        raised either way.
    notify_context : dict | None
        Optional extra fields for the alert email (e.g. {"window": "...",
        "client": "...", "requested_by": "..."}).

    Returns
    -------
    list[dict]  with keys SHOW, URL, PRODUCTION, PLATFORM, SEASON.

    Raises
    ------
    PlatformScopeError
        In strict mode, when an explicitly-requested platform yields zero seeds.
        Carries ``.client_message`` (relay verbatim) and ``.missing_labels``.
    """
    options = {"include_clips": include_clips, "limit": limit}
    keys, explicit = _platform_keys(platforms, return_explicit=True)
    out, seen = [], set()
    counts = {k: 0 for k in keys}
    for platform in keys:
        try:
            rows = ss.resolver_lookup(platform, title, kind, seasons, url=url,
                                      options=options)
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
                counts[platform] += 1

    if strict and explicit:
        missing = [p for p in explicit if counts.get(p, 0) == 0]
        if missing:
            err = PlatformScopeError(title, missing, counts)
            if notify:
                # Loop a human in (email Jenna + Jess). NEVER let a mail problem
                # turn a clean refusal into a crash — the build is refused either
                # way, so no other platform's seeds get silently substituted.
                try:
                    import streamscout_notify as _notify
                    _notify.send_scope_alert(
                        title, err.missing_labels,
                        client_message=err.client_message,
                        context=notify_context)
                except Exception:  # noqa: BLE001
                    pass
            raise err
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
    ap.add_argument("--include-clips", action="store_true",
                    help="YouTube: include clips (fold in Shorts); default is "
                         "full episodes/videos only")
    ap.add_argument("--limit", type=int, default=None,
                    help="YouTube: fair-survey cap (even stride across the "
                         "timespan; default 600, 0 = no cap)")
    ap.add_argument("--strict", action="store_true",
                    help="explicit-platform scope guard: hard-fail if any "
                         "directly-named platform returns 0 seeds (so a scoped "
                         "'Hulu-only' request can't silently build on the wrong "
                         "platform)")
    ap.add_argument("--notify", action="store_true",
                    help="with --strict: on a scope failure, email Crosswalk "
                         "(Jenna + Jess) that the build was refused")
    ap.add_argument("--csv", action="store_true",
                    help="print CSV instead of JSON")
    args = ap.parse_args()
    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    try:
        rows = streamscout_query(args.title, platforms=plats, kind=args.kind,
                                 show=args.show,
                                 include_clips=(args.include_clips or None),
                                 limit=args.limit, strict=args.strict,
                                 notify=args.notify)
    except PlatformScopeError as e:
        # The client-facing line goes to stdout for relay; detail to stderr.
        print(e.client_message)
        print(f"PlatformScopeError: {e}", file=sys.stderr)
        return 3
    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([r["SHOW"], r["URL"], r["PRODUCTION"], r["PLATFORM"],
                        r["SEASON"]])
    else:
        print(json.dumps(rows, indent=2))
    print(f"\n{len(rows)} row(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
