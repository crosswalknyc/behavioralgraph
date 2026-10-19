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
import re
import sys
import urllib.parse

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
      .counts (per-platform seed counts), .client_message (relay verbatim),
      .unverified (the missing platforms whose scan could NOT run from here:
      a resolver raised instead of answering; 'could not look' is never
      'not there'), .failures ({platform: reason}).
    """

    def __init__(self, title, missing, counts=None, unverified=None, failures=None):
        self.title = title
        self.missing = list(missing)
        self.counts = dict(counts or {})
        self.unverified = [p for p in (unverified or []) if p in self.missing]
        self.failures = dict(failures or {})
        self.missing_labels = [ss.platform_label(p) for p in self.missing]
        self.unverified_labels = [ss.platform_label(p) for p in self.unverified]
        self.client_message = client_message(title, self.missing_labels)
        human = ", ".join(
            "%s (%s)" % (ss.platform_label(p),
                         "could not be checked from here" if p in self.unverified else "0 seeds")
            for p in self.missing)
        super().__init__(
            "explicit platform scope not satisfiable for %r: %s. Refusing to "
            "build so no profile runs on the wrong platform. Seed these "
            "platform(s) first (or drop them from the request)." % (title, human))


def _hint_for(platform, hints, url):
    """The URL / identifier hint for one platform: a per-platform entry in
    ``hints`` (what the content map already holds there) beats the shared
    ``url``. A hint lets a resolver open the catalog page directly instead
    of discovering the title first."""
    if isinstance(hints, dict):
        h = hints.get(platform)
        if h:
            return str(h)
    return url


def query_report(title, platforms=None, kind="series", seasons=None,
                 url=None, show=None, include_clips=None, limit=None, hints=None):
    """``streamscout_query`` with the full account: the rows, the per-platform
    seed counts, and ``failed`` ({platform: reason}) for every platform whose
    resolver RAISED instead of answering. A raised resolver is not a zero:
    the geo redirect, a refused lookup, a timeout all land here, so a caller
    can hold its stored placement instead of reading the title as gone."""
    options = {"include_clips": include_clips, "limit": limit}
    keys, explicit = _platform_keys(platforms, return_explicit=True)
    out, seen = [], set()
    counts = {k: 0 for k in keys}
    failed = {}
    for platform in keys:
        try:
            rows = ss.resolver_lookup(platform, title, kind, seasons,
                                      url=_hint_for(platform, hints, url),
                                      options=options)
        except Exception as e:  # noqa: BLE001 (hands-off: the platform is unverified, the batch goes on)
            failed[platform] = ("%s: %s" % (type(e).__name__, e))[:240]
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
    return {"rows": out, "counts": counts, "failed": failed,
            "keys": list(keys), "explicit": sorted(explicit)}


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
                      strict=False, notify=False, notify_context=None, hints=None):
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
    hints : dict | None
        Per-platform URL / identifier hints ({"hulu": "<watch uuid>"}), the
        placements the content map already holds. A hinted resolver opens
        the catalog page directly; discovery runs only for the rest.

    Returns
    -------
    list[dict]  with keys SHOW, URL, PRODUCTION, PLATFORM, SEASON.

    Raises
    ------
    PlatformScopeError
        In strict mode, when an explicitly-requested platform yields zero seeds.
        Carries ``.client_message`` (relay verbatim), ``.missing_labels`` and
        ``.unverified`` (the missing platforms whose resolver raised rather
        than answered; a caller keeps its stored placement for those).
    """
    rep = query_report(title, platforms=platforms, kind=kind, seasons=seasons,
                       url=url, show=show, include_clips=include_clips,
                       limit=limit, hints=hints)
    out, counts, failed = rep["rows"], rep["counts"], rep["failed"]
    explicit = set(rep["explicit"])

    if strict and explicit:
        missing = [p for p in explicit if counts.get(p, 0) == 0]
        if missing:
            err = PlatformScopeError(title, missing, counts,
                                     unverified=[p for p in missing if p in failed],
                                     failures=failed)
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


def _normalize_known(known):
    """Map stored platform labels/keys ("Netflix", "netflix") to resolver keys,
    best-effort. Unknown entries are dropped. Returns a de-duped key list."""
    if not known:
        return []
    if isinstance(known, str):
        known = [known]
    label_to_key = {}
    for k in ss.RESOLVER_PLATFORMS:
        label_to_key[k] = k
        label_to_key[ss.platform_label(k).lower()] = k
    out, seen = [], set()
    for item in known:
        key = label_to_key.get((str(item) or "").strip().lower())
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def current_placements(title, platforms="video", kind="series", seasons=None,
                       url=None, known=None, hints=None):
    """Live-scan where ``title`` lives RIGHT NOW — regardless of whether it is
    already in the content_map.

    This closes the "pre-existing seed suppressed the scan" gap: Gilmore Girls
    sat in the content_map on **Netflix**, so nobody ever scanned **Hulu**, where
    it actually lives now. StreamScout's resolvers always hit each platform's
    LIVE catalog, so a fresh scan is authoritative for *current* placement.
    Promy should call this on **every** seeding pass — a content_map hit must NOT
    skip the scan.

    Parameters mirror ``streamscout_query`` (title, platforms/group, kind,
    seasons, url). ``known`` is the set of platforms the content_map already has
    stored for this title (keys or display labels, e.g. ``["Netflix"]``); when
    given, the report reconciles current placement against it.

    ``hints`` ({platform: url or identifier}) are the placements the map
    already holds; a hinted resolver opens its page directly.

    Returns a dict (never raises on a single platform failure — hands-off)::

        {
          "title": ...,
          "scanned":        [platform keys actually scanned],
          "present":        {platform: seed_count},     # where it lives NOW
          "present_labels": [display names of present platforms],
          "absent":         [scanned platforms that answered with 0 seeds],
          "unverified":     [platforms whose resolver raised: could not be
                             checked from here; NOT absent, NOT gone],
          "failures":       {platform: reason},
          "counts":         {platform: n},
          # only when `known` is passed:
          "added":          [present now, not previously stored],  # the Hulu case
          "gone":           [stored before, answered absent now],  # left the platform
          "unchanged":      [stored before, still present],
        }
    """
    requested = _platform_keys(platforms)
    known_keys = _normalize_known(known)
    # Scan the UNION of requested + stored platforms so reconciliation against
    # what's already in the content_map is complete (we can confirm a stored
    # platform is "gone" only if we actually scanned it).
    scan_keys = list(dict.fromkeys(list(requested) + known_keys))
    options = {"include_clips": None, "limit": None}
    counts = {k: 0 for k in scan_keys}
    failures = {}
    for platform in scan_keys:
        try:
            rows = ss.resolver_lookup(platform, title, kind, seasons,
                                      url=_hint_for(platform, hints, url),
                                      options=options)
        except Exception as e:  # noqa: BLE001 (hands-off: the platform stays unverified)
            failures[platform] = ("%s: %s" % (type(e).__name__, e))[:240]
            continue
        seen = set()
        for r in rows:
            ident = r.get("identifier", "") or ""
            if ident and ident not in seen:
                seen.add(ident)
                counts[platform] += 1
    present = {k: counts[k] for k in scan_keys if counts[k] > 0}
    unverified = [k for k in scan_keys if k in failures and counts[k] == 0]
    report = {
        "title": title,
        "scanned": list(scan_keys),
        "present": present,
        "present_labels": [ss.platform_label(k) for k in present],
        "absent": [k for k in scan_keys if counts[k] == 0 and k not in failures],
        "unverified": unverified,
        "failures": failures,
        "counts": counts,
    }
    if known is not None:
        present_set = set(present)
        known_set = set(known_keys)
        report["added"] = [k for k in scan_keys
                           if k in present_set and k not in known_set]
        # a stored platform is gone only when its scan RAN and found nothing
        report["gone"] = [k for k in known_keys
                          if k not in present_set and k not in failures]
        report["unchanged"] = [k for k in known_keys if k in present_set]
    return report


# ── viewers-profile term hygiene ──────────────────────────────────────────────
# Profile IQ has two ways to seed a profile:
#   • PERSON / BRAND  → host-map + name variations (the generic "touchpoints":
#     gilmore-girls, gilmore+girls, Gilmore%20Girls, …) — who's *searching/
#     posting* the title.
#   • VIEWERS / LISTENERS / PLAYERS → StreamScout watch URLs from the content_map
#     — who *actually watched/listened/played*.
# A viewers profile must ride ONLY on the watch URLs. If the generic name
# variations leak in, the profile stops being "Hulu viewers" and drifts back
# toward "anyone who mentioned Gilmore Girls." These helpers let Promy strip them.

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def name_variations(title):
    """The generic brand/name **touchpoint** terms for a title — the host-map
    style name variations (spacing, delimiters, URL-encoding, case) that a
    PERSON/BRAND profile uses. A VIEWERS/LISTENERS/PLAYERS profile must NOT use
    these; this set exists so Promy can DETECT and STRIP them.

    Returns a lowercased set for case-insensitive membership tests. For
    "Gilmore Girls": {"gilmore girls", "gilmoregirls", "gilmore-girls",
    "gilmore_girls", "gilmore.girls", "gilmore+girls", "gilmore%20girls", …}.
    """
    base = (title or "").strip()
    if not base:
        return set()
    words = [w for w in re.split(r"\s+", base) if w]
    out = set()
    for d in ("", " ", "-", "_", ".", "+", "%20", "/", "·"):
        out.add(d.join(words).lower())
    out.add(base.lower())
    out.add(urllib.parse.quote(base).lower())      # Gilmore%20Girls
    out.add(urllib.parse.quote_plus(base).lower())  # Gilmore+Girls
    return out


def _term_keys(term):
    """Comparable keys for a candidate term: itself (raw + lowercased) plus any
    UUID(s) it contains — so a bare UUID matches a full watch URL and vice versa.
    """
    s = str(term or "").strip()
    keys = {s, s.lower()}
    for m in _UUID_RE.findall(s):
        keys.add(m.lower())
    return keys


def viewer_terms(rows):
    """The authoritative term set for a viewers/listeners/players profile: the
    StreamScout watch URLs/identifiers only, de-duped, in order. Pass the rows
    from ``streamscout_query()``; hand the result straight to Profile IQ.
    Accepts row dicts (reads "URL") or bare identifier strings.
    """
    seen, out = set(), []
    for r in rows or []:
        u = (r.get("URL", "") if isinstance(r, dict) else r) or ""
        u = str(u).strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def split_profile_terms(candidate_terms, title, rows=None):
    """Safety guard for viewers profiles. Split a candidate term list into
    ``(watch_terms, generic_touchpoints)``:

      * **generic_touchpoints** — name variations of ``title`` (brand/host-map
        style, e.g. 'gilmore-girls', 'gilmore+girls', 'Gilmore%20Girls') →
        DROPPED. A viewers profile must never seed on these.
      * **watch_terms** — kept. When ``rows`` is given this is STRICT: a term
        survives only if it matches an actual StreamScout identifier (bare-UUID
        ↔ full-URL aware), so nothing but real watch URLs gets through. Without
        ``rows`` it's lenient: anything that isn't a name variation is kept.

    Returns ``(watch_terms, generic_touchpoints)`` — both de-duped, order kept.
    """
    variants = name_variations(title)
    allow = None
    if rows is not None:
        allow = set()
        for u in viewer_terms(rows):
            allow |= _term_keys(u)
    kept, dropped, seen = [], [], set()
    for t in candidate_terms or []:
        s = str(t or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        if s.lower() in variants:          # a generic brand touchpoint
            dropped.append(s)
            continue
        if allow is not None and not (_term_keys(s) & allow):
            dropped.append(s)              # not a real StreamScout watch URL
            continue
        kept.append(s)
    return kept, dropped


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
    ap.add_argument("--placements", action="store_true",
                    help="don't build rows — just report where the title lives "
                         "RIGHT NOW (fresh live scan, ignores the content_map). "
                         "Use --known to reconcile against stored platforms.")
    ap.add_argument("--known", default=None,
                    help="with --placements: comma-list of platforms already in "
                         "the content_map (keys or labels, e.g. 'Netflix') to "
                         "reconcile against")
    ap.add_argument("--scrub-terms", action="store_true",
                    help="viewers-profile term hygiene: read candidate terms "
                         "from stdin (comma/newline separated), drop generic "
                         "brand touchpoints (name variations of --title), print "
                         "only the watch URLs. Dropped terms go to stderr.")
    ap.add_argument("--csv", action="store_true",
                    help="print CSV instead of JSON")
    args = ap.parse_args()
    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    if args.scrub_terms:
        blob = sys.stdin.read()
        cand = [t.strip() for t in re.split(r"[,\n\r]+", blob) if t.strip()]
        kept, dropped = split_profile_terms(cand, args.title)
        if dropped:
            print("dropped %d generic touchpoint(s): %s"
                  % (len(dropped), ", ".join(dropped)), file=sys.stderr)
        print("\n".join(kept) if not args.csv else ",".join(kept))
        print("\n%d watch term(s) kept" % len(kept), file=sys.stderr)
        return 0
    if args.placements:
        known = ([k.strip() for k in args.known.split(",") if k.strip()]
                 if args.known else None)
        report = current_placements(args.title, platforms=plats, kind=args.kind,
                                    known=known)
        print(json.dumps(report, indent=2))
        return 0
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
