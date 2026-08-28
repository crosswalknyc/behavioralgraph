#!/usr/bin/env python3
"""
starz_episode_identifier.py  —  Starz title -> per-episode play ids (no login)
=============================================================================
Starz's web app is a client-rendered Angular SPA, but the content catalog it
draws from is a PUBLIC metadata service (no auth, no subscription):

    https://playdata.starz.com/metadata-service/play/partner/Web/v8/content

A play URL is  https://www.starz.com/us/en/play/<contentId>  and that numeric
<contentId> IS the metadata `contentId`.  So for "Power" we return one row per
episode, keyed by its play id, grouped by season — matching the sheet:

    Power   starz.com/us/en/play/21423   CBS Studios   Starz   Season 1
    Power   starz.com/us/en/play/23688   CBS Studios   Starz   Season 2
    ...

How it works (all anonymous HTTP):
  • Title -> id: fetch the top-level catalog (`content?lang=en-US` returns every
    Movie + "Series with Season") and match the query title/seriesName.
  • Series -> episodes: `content?contentType=Episode` returns every catalog
    episode with its `seasonNumber`, `order` and `topContentId` (the series id).
    We keep the ones whose `topContentId` == our series id, grouped by season.
  • Movie: one row, play id == the movie's own contentId.
  • URL hint: paste a starz.com/.../play/<id> (episode or movie) or
    /content/<id> (series) to skip the title search.

Identifier column = "starz.com/us/en/play/<id>" (the path, as the sheet shows).

Usage
-----
    python3 starz_episode_identifier.py --type series --title "Power"
    python3 starz_episode_identifier.py --type series --title "Power" --seasons 1,2
    python3 starz_episode_identifier.py --type movie  --title "..."
    python3 starz_episode_identifier.py --url https://www.starz.com/us/en/play/23688
    python3 starz_episode_identifier.py                 # interactive
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://playdata.starz.com/metadata-service/play/partner/Web/v8"
PLATFORM = "Starz"

# contentTypes the metadata service uses
TYPE_MOVIE = "Movie"
TYPE_SERIES = "Series with Season"
TYPE_EPISODE = "Episode"

_PLAY_ID = re.compile(r"/play/(\d+)")
_CONTENT_ID = re.compile(r"/(?:content|movies|series)/(?:[a-z0-9-]+/)?(\d+)")
_ANY_ID = re.compile(r"(\d{3,})")

# one-run cache so title + episode listings aren't re-fetched per query
_CATALOG_CACHE = None
_EPISODES_CACHE = None


# ── helpers ───────────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _get(path, params):
    q = urllib.parse.urlencode(params)
    url = f"{BASE}/{path}?{q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return data.get("playContentArray", {}).get("playContents", []) or []


def play_path(content_id):
    """Numeric id -> 'starz.com/us/en/play/<id>' (the sheet's URL column)."""
    return f"starz.com/us/en/play/{content_id}"


def play_url(content_id):
    return f"https://www.starz.com/us/en/play/{content_id}"


# ── catalog / lookups ─────────────────────────────────────────────────────────
def catalog():
    """Every top-level title (Movies + 'Series with Season'). Cached per run."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = _get("content", {
            "lang": "en-US",
            "includes": "contentId,contentType,title,seriesName,properCaseTitle",
        })
    return _CATALOG_CACHE


def all_episodes():
    """Every catalog episode with season/order/topContentId. Cached per run."""
    global _EPISODES_CACHE
    if _EPISODES_CACHE is None:
        _EPISODES_CACHE = _get("content", {
            "lang": "en-US", "contentType": TYPE_EPISODE,
            "includes": "contentId,seasonNumber,order,episodeNumber,"
                        "topContentId,seriesName,title",
        })
    return _EPISODES_CACHE


def content_by_id(content_id):
    """Single content item (episode/series/movie) with type + parent."""
    items = _get("content", {
        "lang": "en-US", "contentIds": str(content_id),
        "includes": "contentId,contentType,title,seriesName,seasonNumber,"
                    "topContentId,episodeCount",
    })
    return items[0] if items else None


def _title_of(item):
    return (item.get("seriesName") or item.get("properCaseTitle")
            or item.get("title") or "")


def find_title(title, kind):
    """Best top-level (series|movie) match for a query. Returns item or None."""
    want = tokens(title)
    want_type = TYPE_MOVIE if kind == "movie" else TYPE_SERIES
    exact, subset = [], []
    for it in catalog():
        if it.get("contentType") != want_type:
            continue
        ct = tokens(_title_of(it))
        if ct == want:
            exact.append(it)
        elif want and set(want) <= set(ct):
            subset.append(it)
    if exact:
        # shortest title wins (avoids "Power Book II" for "Power")
        return min(exact, key=lambda it: len(_title_of(it)))
    if subset:
        return min(subset, key=lambda it: len(_title_of(it)))
    return None


def episodes_for_series(series_id):
    """All episodes whose topContentId == series_id, sorted season/order."""
    out = [e for e in all_episodes() if e.get("topContentId") == series_id]
    out.sort(key=lambda e: (e.get("seasonNumber") or 0,
                            e.get("order") if e.get("order") is not None
                            else (e.get("episodeNumber") or 0),
                            e.get("contentId") or 0))
    return out


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Starz title to per-episode play ids (movie -> one row).

    Returns (show_name, rows) with rows like
        {season:"2", episode:"", title:<ep title>,
         identifier:"starz.com/us/en/play/23688", watch_url:<full url>}.
    `seasons` (set[int] or None) filters series rows. Paste a starz.com URL via
    `url` (play/<id> episode|movie, or content/<id> series) to skip search.
    """
    series_id = None
    movie_id = None
    show = title or ""

    # 1) URL hint — figure out what kind of id was pasted
    if url:
        mp = _PLAY_ID.search(url)
        mc = _CONTENT_ID.search(url)
        cid = None
        if mp:
            cid = int(mp.group(1))
        elif mc:
            cid = int(mc.group(1))
        else:
            ma = _ANY_ID.search(url)
            cid = int(ma.group(1)) if ma else None
        if cid is not None:
            item = content_by_id(cid)
            ctype = (item or {}).get("contentType")
            if ctype == TYPE_EPISODE:
                series_id = (item or {}).get("topContentId")
                show = (item or {}).get("seriesName") or show
            elif ctype == TYPE_SERIES:
                series_id = cid
                show = _title_of(item) or show
            elif ctype == TYPE_MOVIE:
                movie_id = cid
                show = _title_of(item) or show
                kind = "movie"
            else:  # unknown id — treat a play/<id> as movie, else as series
                if mp:
                    movie_id, kind = cid, "movie"
                else:
                    series_id = cid

    # 2) title search
    if series_id is None and movie_id is None and title:
        best = find_title(title, kind)
        if best:
            show = _title_of(best)
            if best.get("contentType") == TYPE_MOVIE:
                movie_id = best.get("contentId")
            else:
                series_id = best.get("contentId")

    # 3a) movie -> single row
    if movie_id is not None:
        return (show, [{
            "season": "", "episode": "", "title": show,
            "identifier": play_path(movie_id), "watch_url": play_url(movie_id),
        }])

    # 3b) series -> per-episode rows
    if series_id is not None:
        rows = []
        for e in episodes_for_series(series_id):
            sn = e.get("seasonNumber")
            if seasons and (sn is None or sn not in seasons):
                continue
            cid = e.get("contentId")
            rows.append({
                "season": str(sn) if sn is not None else "",
                "episode": str(e.get("episodeNumber") or ""),
                "title": e.get("title") or "",
                "identifier": play_path(cid), "watch_url": play_url(cid),
            })
        return (show, rows)

    return (show, [])


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Starz title -> per-episode play ids (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1  |  1,2  |  1-4 (series only)")
    ap.add_argument("--url", help="a starz.com play/content URL (skips search)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not kind and not args.url:
        a = ask("Movie or Series?  [m/s]: ").lower()
        kind = "movie" if a.startswith("m") else "series"
    if not args.url and not title:
        title = ask("What title?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    seasons = None
    if args.seasons and (kind or "series") == "series":
        seasons = set()
        for part in args.seasons.replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit():
                    seasons.update(range(int(a), int(b) + 1))
            elif part.isdigit():
                seasons.add(int(part))
        seasons = seasons or None

    try:
        show, rows = resolve(title=title, url=args.url,
                             kind=kind or "series", seasons=seasons)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on Starz. Try --url with the starz.com link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "starz").lower()).strip("-")
    out = os.path.join(args.outdir, f"starz_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            season = f"Season {r['season']}" if r["season"] else ""
            w.writerow([show, r["identifier"], PLATFORM, season])

    print(f"\n{show}  ->  {len(rows)} row(s) on Starz")
    for r in rows[:20]:
        extra = f" (Season {r['season']})" if r["season"] else ""
        print(f"   {r['identifier']}{extra}")
    if len(rows) > 20:
        print(f"   ... and {len(rows) - 20} more")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
