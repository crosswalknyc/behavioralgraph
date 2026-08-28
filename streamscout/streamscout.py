#!/usr/bin/env python3
"""
StreamScout  (streamscout.py)  —  interactive per-title identifier lookup
=========================================================================
Ask a few questions in the terminal, then fetch the UNIQUE URL slug /
identifier segment(s) for that title and write them to a fresh CSV on the
Desktop.

StreamScout is a self-contained unit: this file plus its sibling per-platform
resolver modules and production_tags.py all live together in the streamscout/
folder. Run it directly; the resolvers are loaded by name from this same
folder, so keep them together.

Flow
----
  1. Movie or Series?
  2. What title?
  3. (Series only) Which season / seasons?  e.g. 1  |  1,3,5  |  1-4  |  all
  4. Which platform?

Two kinds of platform
---------------------
  SLUG-IN-URL  (live ClickHouse, no login):  the human slug is in the URL, so
  we extract it directly from clickstream.clickstream_final.
      peacock    watch/asset/movies/<slug>/<uuid>
                 watch/asset/tv/<series>/<id>/seasons/<N>/episodes/<ep>/<uuid>

  RESOLVER  (opaque id -> per-episode watch ids via a dedicated module):
      hulu       hulu_episode_identifier.resolve()      (HTTP, no login)
      netflix    netflix_title_identifier.resolve()     (browser, .env.local login)
      peacock    peacock_episode_identifier.resolve()   (HTTP, no login) — tried
                 FIRST, with the clickstream slug query above as a fallback.
      appletv    apple_tv_identifier.resolve()          (HTTP, no login) — one
                 show id per series (Apple keys every episode to one shell id).
      paramount  paramount_episode_identifier.resolve() (curl/HTTP, no login) —
                 per-season episode video ids from Paramount+'s own JSON feed.
      max        max_episode_identifier.resolve()       (browser, .env.local
                 login) — per-season episode watch UUIDs from Max's JSON API;
                 needs a visible window (its SPA won't render headless).
      disney     disney_episode_identifier.resolve()    (HTTP, no login) —
                 per-season episode play UUIDs from Disney+'s public entity
                 page (episodes + seoSeasons in the embedded __NEXT_DATA__).
                 Discovery is no-login (cache -> web search); if that's empty or
                 low-confidence it ESCALATES to a logged-in Disney+ search
                 (.env.local DISNEY_*) whose result tiles give the exact entity.
      starz      starz_episode_identifier.resolve()     (HTTP, no login) —
                 per-season episode play ids from Starz's public metadata
                 service (playdata.starz.com); URL col = starz.com/us/en/play/<id>.

Season/episode granularity: full for Netflix, Hulu, Peacock, Max and Disney+
(all read the full episode tree straight from the platform). Peacock's direct resolver returns
every episode/season (not just what was watched); it only falls back to the
clickstream slug query when a title isn't on Peacock's site right now. The other
slug platforms are series-level only.

Every platform writes ONE consistent CSV:
    SHOW, URL, PRODUCTION, PLATFORM, SEASON
where URL is the extracted watch segment/identifier (e.g. watch/81268514 for
Netflix, the UUID for Hulu/Peacock/Max) and SEASON reads "Season 1", "Season 2"
(blank for movies and single-id series like Apple TV).

Search window (SLUG platforms): trailing N days (default 365). Override with
--days, or pin --start / --end (YYYY-MM-DD).

Usage  (run from the repo root, or from inside the streamscout/ folder)
-----
    /opt/homebrew/bin/python3 streamscout/streamscout.py
    /opt/homebrew/bin/python3 streamscout/streamscout.py --days 120
    /opt/homebrew/bin/python3 streamscout/streamscout.py --start 2026-07-01 --end 2026-07-31
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import production_tags

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# ── platform registry ─────────────────────────────────────────────────────────
# Each SLUG platform provides a "movie" and/or "series" spec:
#   extract   : re2 capture pattern run over lower(URL); groups map to `groups`
#   prefilter : coarse re2 URL filter, "{tok}" gets the title-token regex
#   groups    : names for each capture group, in order (must include "slug")
# Dots in domains are written as "." (any char) on purpose — avoids backslash
# escaping headaches and matches the literal dot in practice.
SLUG_PLATFORMS = {
    "peacock": {
        "label": "Peacock",
        "movie": {
            "extract": "watch/asset/movies/([^/?#]+)/(" + UUID + ")",
            "prefilter": "watch/asset/movies/[^/?#]*{tok}[^/?#]*/" + UUID,
            "groups": ["slug", "id"],
        },
        "series": {
            "extract": ("watch/asset/tv/([^/?#]+)/[^/?#]+/seasons/([0-9]+)/"
                        "episodes/([^/?#]+)/(" + UUID + ")"),
            "prefilter": "watch/asset/tv/[^/?#]*{tok}[^/?#]*/",
            "groups": ["slug", "season", "episode", "id"],
        },
    },
}

# RESOLVER platforms have their URL as an opaque id, so a dedicated resolver
# (imported as a module) walks the platform to return per-episode watch ids.
#   hulu     -> hulu_episode_identifier.resolve()   (HTTP, no login)
#   netflix  -> netflix_title_identifier.resolve()  (browser, uses .env.local)
RESOLVER_PLATFORMS = {
    "hulu": {"label": "Hulu", "module": "hulu_episode_identifier"},
    "netflix": {"label": "Netflix", "module": "netflix_title_identifier"},
    "appletv": {"label": "Apple TV", "module": "apple_tv_identifier"},
    "paramount": {"label": "Paramount+", "module": "paramount_episode_identifier"},
    "max": {"label": "HBO MAX", "module": "max_episode_identifier"},
    "disney": {"label": "Disney Plus", "module": "disney_episode_identifier"},
    "starz": {"label": "Starz", "module": "starz_episode_identifier"},
}

# title -> episode-watch-id resolver modules (used for both pure RESOLVER
# platforms and Peacock, which tries its direct resolver before clickstream).
RESOLVER_MODULE = {
    "hulu": "hulu_episode_identifier",
    "netflix": "netflix_title_identifier",
    "peacock": "peacock_episode_identifier",
    "appletv": "apple_tv_identifier",
    "paramount": "paramount_episode_identifier",
    "max": "max_episode_identifier",
    "disney": "disney_episode_identifier",
    "starz": "starz_episode_identifier",
}
# resolvers that accept a pasted URL / UUID as a discovery hint
URL_HINT_RESOLVERS = {"hulu", "peacock", "appletv", "paramount", "max", "disney",
                      "starz"}

PLATFORM_ORDER = ["peacock", "hulu", "netflix", "appletv", "paramount",
                  "max", "disney", "starz"]

# ── PRODUCTION (studio) tag ────────────────────────────────────────────────────
# Sourced centrally for EVERY platform by production_tags.py:
#   manual overrides (production_tags.OVERRIDES)  ->  on-disk cache  ->  Wikidata
#   (first "production company" P272, normalised via production_tags.ALIASES).
# Wikidata is open/CC0 — no API key, no signup. Pin/adjust codes in that file.
def production_for(show: str, kind: str = "series") -> str:
    """Primary production-house code for a title (blank if unknown)."""
    return production_tags.production_for(show, kind)


# ── low-confidence alert for Jenna  (LOCAL-ONLY PLACEHOLDER) ───────────────────
# IMPORTANT: This is a deliberately self-contained stub that lives ONLY inside
# this tool while we build it. It does NOT send anything to the dashboard,
# Render, Slack, email, or the chatbot, and makes NO network calls. It just
# records a flag locally (a JSONL file next to this script) and prints to this
# terminal. When we later wire this into the chatbot / Prometheus, replace the
# body of `deliver_alert()` with the real notifier and leave the rest as-is.
JENNA_ALERT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "jenna_alerts.local.jsonl")


def assess_confidence(rows, title, kind, seasons):
    """Return (confident: bool, reason: str) for a finished lookup."""
    if not rows:
        return False, "no matching URLs found for this title"
    named = any((r.get("show") or "").strip() for r in rows)
    if named and not any(r.get("exact") == "yes" for r in rows):
        return False, "only near-title matches (no exact title match)"
    if kind == "series" and seasons:
        got = {str(r.get("season")) for r in rows if str(r.get("season") or "")}
        missing = [str(s) for s in sorted(seasons) if str(s) not in got]
        if missing:
            return False, "no episodes found for requested season(s): " + \
                          ", ".join(missing)
    return True, ""


def deliver_alert(payload: dict) -> None:
    """LOCAL ONLY. Persist the flag next to this script. DO NOT add any network
    / dashboard / chatbot delivery here yet — that's the future integration."""
    try:
        with open(JENNA_ALERT_LOG, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def notify_jenna(reason, title, platform, label, kind, seasons, rows) -> None:
    """Flag a low-confidence lookup for Jenna (local placeholder, sends nowhere)."""
    payload = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "assignee": "Jenna",
        "status": "needs_review",
        "reason": reason,
        "title": title,
        "platform": platform,
        "kind": kind,
        "seasons": sorted(seasons) if seasons else "all",
        "result_count": len(rows),
    }
    deliver_alert(payload)
    print("\n  ! LOW CONFIDENCE — flagged for Jenna to review "
          "(local only, not sent anywhere yet).")
    print(f"      why: {reason}")
    print(f"      query: {kind} {title!r} on {label}")


# ── small helpers ─────────────────────────────────────────────────────────────
def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def token_regex(title: str) -> str:
    """'Wicked For Good' -> 'wicked[^a-z0-9]+for[^a-z0-9]+good'."""
    toks = tokens(title)
    return "[^a-z0-9]+".join(re.escape(t) for t in toks)


def slug_to_show(slug: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (slug or "").lower()).upper().strip()


def parse_seasons(raw: str):
    """'all' -> None; '1,3-5' -> {1,3,4,5}."""
    raw = (raw or "").strip().lower()
    if raw in ("", "all", "*", "a"):
        return None
    out: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out or None


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def ask_choice(prompt: str, choices: dict[str, str]) -> str:
    while True:
        ans = ask(prompt).lower()
        if ans in choices:
            return choices[ans]
        print(f"  Please enter one of: {', '.join(sorted(set(choices.values())))}")


# ── SLUG platforms (live ClickHouse) ──────────────────────────────────────────
def get_client():
    import clickhouse_connect
    # This unit lives in a subfolder (streamscout/) of the behavioralgraph repo;
    # the `migration` package sits at the repo root, so make sure the repo root
    # is importable no matter which directory the tool is launched from. (Only
    # used by Peacock's clickstream fallback — the other platforms need none of
    # this.)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from migration.clickhouse_connector import (
        CH_HOST, CH_PORT, CH_USER, CH_PASSWORD, CH_DATABASE,
    )
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER,
        password=CH_PASSWORD, database=CH_DATABASE,
        connect_timeout=30, send_receive_timeout=7200,
        settings={"max_execution_time": 7200, "max_threads": 48},
    )


def run_slug_query(client, spec, title, seasons, start, end):
    tok = token_regex(title)
    url_re = spec["prefilter"].format(tok=tok)
    groups = spec["groups"]
    n = len(groups)
    gi = {name: i + 1 for i, name in enumerate(groups)}  # 1-based
    slug_i = gi["slug"]

    season_clause = ""
    if "season" in gi and seasons:
        inlist = ",".join("'%d'" % s for s in sorted(seasons))
        season_clause = "AND g[%d] IN (%s)" % (gi["season"], inlist)

    assigns = ", ".join("g[%d] AS c%d" % (i + 1, i + 1) for i in range(n))
    cols = ", ".join("c%d" % (i + 1) for i in range(n))
    sql = """
        SELECT {cols}, count() AS hits
        FROM (
            SELECT {assigns}
            FROM (
                SELECT extractGroups(lower(URL), %(extract)s) AS g
                FROM clickstream.clickstream_final
                WHERE DELIVERED BETWEEN toDate(%(s)s) AND toDate(%(e)s)
                  AND match(lower(URL), %(url_re)s)
            )
            WHERE length(g) = {n} AND match(g[{slug_i}], %(tok)s) {season_clause}
        )
        GROUP BY {cols}
        ORDER BY hits DESC
    """.format(cols=cols, assigns=assigns, n=n, slug_i=slug_i,
               season_clause=season_clause)

    rows = client.query(sql, parameters={
        "extract": spec["extract"], "url_re": url_re, "tok": tok,
        "s": start, "e": end,
    }).result_rows

    want = tokens(title)
    out = []
    for r in rows:
        vals = {groups[i]: r[i] for i in range(n)}
        slug = vals.get("slug", "")
        out.append({
            "show": slug_to_show(slug),
            "season": vals.get("season", "") or "",
            "episode": slug_to_show(vals.get("episode", "")) if vals.get("episode") else "",
            "slug": slug,
            "identifier": vals.get("id", "") or "",
            "hits": int(r[n]),
            "exact": "yes" if tokens(slug) == want else "no",
        })
    return out


# ── RESOLVER platforms (import a per-platform resolver module) ─────────────────
def resolver_lookup(platform, title, kind, seasons, url=None):
    """Call the platform's resolve() and return unified row dicts."""
    import importlib
    mod = importlib.import_module(RESOLVER_MODULE[platform])
    if platform in URL_HINT_RESOLVERS:  # accepts a pasted URL / UUID hint
        show, rrows = mod.resolve(title=title, url=url, kind=kind, seasons=seasons)
    else:
        show, rrows = mod.resolve(title=title, kind=kind, seasons=seasons)
    want = tokens(title)
    out = []
    for r in rrows:
        out.append({
            "show": (show or title).upper().strip(),
            "season": r.get("season", "") or "",
            "episode": r.get("episode", "") or "",
            "episode_title": r.get("title", "") or "",
            "identifier": r.get("identifier", "") or "",
            "watch_url": r.get("watch_url", "") or "",
            "hits": "",
            "exact": "yes" if tokens(show) == want else "no",
        })
    return out


# ── unify a SLUG-platform row into the common schema ──────────────────────────
def unify_slug_row(platform, r):
    ident = r.get("identifier") or r.get("slug") or ""
    watch_url = ""
    if platform == "peacock" and ident:
        watch_url = f"https://www.peacocktv.com/watch/playback/vod/_/{ident}"
    # Peacock's "episode" text looks like "THE PHANTOM HOOK EPISODE 1":
    # split the trailing number off into EPISODE, keep the name as the title.
    ep_num, ep_title = "", r.get("episode", "") or ""
    m = re.search(r"\bEPISODE\s+(\d+)\s*$", ep_title)
    if m:
        ep_num = m.group(1)
        ep_title = ep_title[:m.start()].strip()
    return {
        "show": r["show"], "season": str(r.get("season") or ""),
        "episode": ep_num, "episode_title": ep_title,
        "identifier": ident, "watch_url": watch_url,
        "hits": r.get("hits", ""), "exact": r.get("exact", "no"),
    }


# ── main ──────────────────────────────────────────────────────────────────────
def platform_label(key: str) -> str:
    return (SLUG_PLATFORMS.get(key) or RESOLVER_PLATFORMS.get(key))["label"]


def choose_platform() -> str:
    print("4) Which platform?")
    labels = {}
    for i, key in enumerate(PLATFORM_ORDER, 1):
        lbl = platform_label(key)
        if key == "netflix":
            tag = "resolver · opens browser"
        elif key == "peacock":
            tag = "direct · clickstream fallback"
        elif key == "appletv":
            tag = "direct · one id per series"
        elif key == "paramount":
            tag = "direct · no login"
        elif key == "max":
            tag = "resolver · opens browser"
        elif key == "disney":
            tag = "direct · login fallback"
        elif key == "starz":
            tag = "direct · no login"
        elif key in RESOLVER_PLATFORMS:
            tag = "resolver"
        else:
            tag = "clickstream"
        print(f"     {i}. {lbl}  ({tag})")
        labels[str(i)] = key
        labels[key] = key
        labels[lbl.lower()] = key
    while True:
        ans = ask("   choose #, name: ").lower()
        if ans in labels:
            return labels[ans]
        print("   Please pick a listed number or platform name.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Interactive per-title identifier lookup.")
    ap.add_argument("--days", type=int, default=365,
                    help="SLUG platforms: trailing search window in days (default 365)")
    ap.add_argument("--start", default=None, help="explicit start YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="explicit end YYYY-MM-DD")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    print("\n=== Title Identifier Lookup ===\n")

    kind = ask_choice("1) Movie or Series?  [m/s]: ",
                      {"m": "movie", "movie": "movie", "s": "series", "series": "series"})
    title = ""
    while not title:
        title = ask("2) What title are you looking for?: ")
        if not tokens(title):
            print("  Please enter a title with letters/numbers.")
            title = ""
    seasons = None
    if kind == "series":
        seasons = parse_seasons(
            ask("3) Which season(s)?  e.g. 1  |  1,3,5  |  1-4  |  all: "))
    platform = choose_platform()

    end = args.end or date.today().isoformat()
    start = args.start or (datetime.strptime(end, "%Y-%m-%d").date()
                           - timedelta(days=args.days)).isoformat()

    label = platform_label(platform)
    print()
    if platform == "peacock":
        # Peacock: try the DIRECT resolver first (its public /stream-tv page
        # carries the full episode tree, and the movies sitemap maps slug->UUID
        # — so titles work even when never watched in clickstream). Fall back to
        # clickstream only if the direct resolver comes up empty.
        print(f"Resolving {label} directly from its site (no login) ...")
        try:
            rows = resolver_lookup(platform, title, kind, seasons)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {label} direct resolver failed ({e}); trying clickstream.")
            rows = []
        if not rows:
            print(f"  Not found on {label}'s site — falling back to clickstream "
                  f"{start} -> {end} ...")
            spec = SLUG_PLATFORMS["peacock"].get(kind)
            if spec:
                slug_rows = run_slug_query(get_client(), spec, title,
                                           seasons, start, end)
                rows = [unify_slug_row(platform, r) for r in slug_rows]
        if not rows:
            paste = ask("  Still nothing. Paste a peacocktv.com URL "
                        "(stream-tv / movie link) or press Enter to skip: ")
            if paste.strip():
                try:
                    rows = resolver_lookup(platform, title, kind, seasons,
                                           url=paste.strip())
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {label} resolver failed: {e}")
                    return 2
    elif platform in SLUG_PLATFORMS:
        plat = SLUG_PLATFORMS[platform]
        spec = plat.get(kind)
        if not spec:
            print(f"{label} has no {kind} URLs to search.")
            return 0
        if kind == "series" and seasons and "season" not in spec["groups"]:
            print(f"Note: {label} URLs don't carry a season number — "
                  f"returning series-level slugs (season filter ignored).")
        print(f"Searching {label} clickstream  {start} -> {end}  "
              f"(long windows can take a minute) ...")
        slug_rows = run_slug_query(get_client(), spec, title, seasons, start, end)
        rows = [unify_slug_row(platform, r) for r in slug_rows]
    else:  # resolver platform (hulu / netflix / appletv / paramount / max)
        if platform == "netflix":
            print(f"Resolving {label} via a logged-in browser "
                  f"(a window may open) ...")
        elif platform == "max":
            print(f"Resolving {label} via a logged-in browser "
                  f"(a window will open) ...")
        elif platform == "appletv":
            print(f"Resolving {label} show id (no login) ...")
        elif platform == "paramount":
            print(f"Resolving {label} episodes (no login) ...")
        elif platform == "disney":
            print(f"Resolving {label} episodes directly from its site "
                  f"(no login) ...")
        elif platform == "starz":
            print(f"Resolving {label} episodes from its public catalog "
                  f"(no login) ...")
        else:
            print(f"Resolving {label} episodes ...")
        try:
            rows = resolver_lookup(platform, title, kind, seasons)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {label} resolver failed: {e}")
            return 2
        # Disney+ SITUATIONAL escalation: the no-login path handles most titles,
        # but a client can ask for anything out of the blue. If the no-login
        # discovery came back empty or low-confidence, escalate to a logged-in
        # Disney+ session and use Disney's OWN search (authoritative), then parse
        # that entity page no-login as usual.
        if platform == "disney":
            confident, _why = assess_confidence(rows, title, kind, seasons)
            if not rows or not confident:
                print("  No-login discovery was uncertain — opening a logged-in "
                      "Disney+ search to confirm (a window will open) ...")
                try:
                    import importlib
                    dmod = importlib.import_module(RESOLVER_MODULE["disney"])
                    ent = dmod.login_discover_url(title, kind or "series",
                                                  headed=True)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! Disney+ login search failed: {e}")
                    ent = None
                if ent:
                    rows = resolver_lookup(platform, title, kind, seasons, url=ent)
        # Web-search / search discovery can miss obscure titles — let the user
        # paste the platform URL directly as a reliable fallback.
        if not rows and platform in URL_HINT_RESOLVERS:
            paste = ask(f"  Couldn't auto-find it. Paste a {label} URL "
                        f"(or press Enter to skip): ")
            if paste.strip():
                try:
                    rows = resolver_lookup(platform, title, kind, seasons,
                                           url=paste.strip())
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {label} resolver failed: {e}")
                    return 2

    # de-dupe on the full identity
    seen = {}
    for r in rows:
        key = (r["show"], r["season"], r["episode"], r["identifier"])
        seen.setdefault(key, r)
    rows = list(seen.values())

    # attach production tag now that we know each show name (shared across all
    # platforms: manual overrides -> cache -> TMDB first company)
    for r in rows:
        r["production"] = production_for(r["show"], kind)

    def sort_key(x):
        return (x["exact"] != "yes", str(x["season"]).zfill(3),
                _epnum(x["episode"]),
                -(x["hits"]) if isinstance(x["hits"], int) else 0,
                x["show"])

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "title"
    out_path = os.path.join(
        args.outdir, f"lookup_{platform}_{kind}_{safe}_{stamp}.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in sorted(rows, key=sort_key):
            w.writerow([r["show"], r["identifier"], r["production"], label,
                        season_label(r["season"])])

    print(f"\nFound {len(rows)} result(s) for {title!r} on {label}.")
    exact = [r for r in rows if r["exact"] == "yes"]
    if rows:
        print(f"  ({len(exact)} exact title match, {len(rows) - len(exact)} near match)")
    for r in sorted(rows, key=sort_key)[:25]:
        extra = f" S{r['season']} E{r['episode']}" if r["season"] else ""
        star = "*" if r["exact"] == "yes" else " "
        print(f"  {star} {r['show']}{extra}  ->  {r['identifier']}")
    if len(rows) > 25:
        print(f"  ... and {len(rows) - 25} more (see CSV)")
    print(f"\nCSV written to: {out_path}\n")

    # Low-confidence check → local Jenna alert (placeholder; sends nowhere yet).
    confident, reason = assess_confidence(rows, title, kind, seasons)
    if not confident:
        notify_jenna(reason, title, platform, label, kind, seasons, rows)
    return 0


def _epnum(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def season_label(s) -> str:
    """'2' -> 'Season 2';  '' / None -> '' (movies, single-id series)."""
    s = str(s or "").strip()
    if not s:
        return ""
    return f"Season {s}" if not s.lower().startswith("season") else s


if __name__ == "__main__":
    sys.exit(main())
