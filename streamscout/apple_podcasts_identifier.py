#!/usr/bin/env python3
"""
apple_podcasts_identifier.py  —  Apple Podcasts show -> every episode URL
=========================================================================
An Apple "show" is a podcast; we enumerate its episodes and emit ONE row per
episode holding the full podcasts.apple.com link:

    SHOW                        URL                                              PLAT
    Baby, This is Keke Palmer   https://podcasts.apple.com/us/podcast/<slug>/    Apple Podcasts
                                id<showId>?i=<episodeId>
    ... (one row per episode) ...

How (Apple's public iTunes API — no login, no key)
--------------------------------------------------
  • Title -> show: GET itunes.apple.com/search?media=podcast&term=<title>
    (best case-insensitive match on the collection name).
  • Show -> episodes: GET itunes.apple.com/lookup?id=<collectionId>
    &entity=podcastEpisode — each episode carries a `trackViewUrl` that IS the
    podcasts.apple.com/...id<showId>?i=<trackId> link (we just trim Apple's
    `&uo=` tracking suffix so it matches the canonical share URL).
  • URL hint: paste any podcasts.apple.com podcast/episode link (or an
    `id<digits>` / `?i=<digits>`) to skip the search.

Note on very long shows: the iTunes lookup returns up to 200 episodes. Shows
with more than 200 episodes will be capped (the tool warns when it hits 200);
paste the podcasts.apple.com show link and we still return that first 200. (A
paginated amp-api mode can be added later if a client needs 200+.)

Usage
-----
    python3 apple_podcasts_identifier.py --title "Baby, This is Keke Palmer"
    python3 apple_podcasts_identifier.py --url https://podcasts.apple.com/us/podcast/id1668446854
    python3 apple_podcasts_identifier.py                     # interactive
"""

import argparse
import csv
import difflib
import gzip
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SEARCH = "https://itunes.apple.com/search"
LOOKUP = "https://itunes.apple.com/lookup"
PLATFORM = "Apple Podcasts"
COUNTRY = "US"
LOOKUP_CAP = 200

_ID = re.compile(r"id(\d+)")               # /id1668446854
_EPI = re.compile(r"[?&]i=(\d+)")          # ?i=1000788416415


# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def similarity(a, b):
    """0..1 title similarity, case-folded & robust to word order/typos."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    if ta == tb or ta <= tb or tb <= ta:
        return 1.0
    jacc = len(ta & tb) / len(ta | tb)
    ratio = difflib.SequenceMatcher(
        None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(jacc, ratio)


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def get_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def clean_url(u):
    """Trim Apple's tracking tail so the link matches the canonical share URL:
    '.../id123?i=456&uo=4' -> '.../id123?i=456'."""
    u = u or ""
    m = re.search(r"(\?i=\d+)", u)
    if m:
        return u[:m.end()]
    return re.sub(r"[?&]uo=\d+", "", u)


# ── discovery + enumeration ───────────────────────────────────────────────────
def find_show(title):
    """(collectionId, name) for the best case-insensitive podcast match."""
    j = get_json(SEARCH, {"media": "podcast", "term": title,
                          "country": COUNTRY, "limit": 25})
    results = [r for r in (j.get("results") or []) if r.get("collectionId")]
    if not results:
        return None, None
    best = max(results, key=lambda r: similarity(title, r.get("collectionName", "")))
    return best.get("collectionId"), best.get("collectionName")


def show_episodes(collection_id):
    """(name, [(trackId, trackName, url)]) for a podcast's episodes (<=200)."""
    j = get_json(LOOKUP, {"id": collection_id, "media": "podcast",
                          "entity": "podcastEpisode", "limit": LOOKUP_CAP,
                          "country": COUNTRY})
    results = j.get("results") or []
    name = ""
    eps = []
    for r in results:
        wt = r.get("wrapperType")
        if wt in ("track", "collection") and r.get("collectionName") and not name:
            # the leading item is the podcast itself
            if r.get("kind") != "podcast-episode" and not r.get("episodeUrl"):
                name = r.get("collectionName", "")
        if r.get("kind") == "podcast-episode" or wt == "podcastEpisode":
            url = clean_url(r.get("trackViewUrl", ""))
            if url:
                eps.append((r.get("trackId"), r.get("trackName", ""), url))
    if not name:
        name = (results[0].get("collectionName") if results else "") or ""
    return name, eps


def parse_url(url):
    """(collection_id, episode_id) from a podcasts.apple.com link (episode_id
    may be None). Both are numeric ids."""
    if not url:
        return None, None
    cid = _ID.search(url)
    epi = _EPI.search(url)
    return (cid.group(1) if cid else None), (epi.group(1) if epi else None)


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def _rows(episodes):
    rows = []
    for tid, tname, url in episodes:
        rows.append({"season": "", "episode": "", "title": tname or "",
                     "identifier": url, "watch_url": url})
    return rows


def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve an Apple Podcasts show to every episode's podcasts.apple.com URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:"https://podcasts.apple.com/us/podcast/<slug>/id<id>?i=<eid>",
         watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (podcasts are a flat
    episode list). All anonymous — no login, no API key.
    """
    # 1) URL hint
    if url:
        cid, eid = parse_url(url)
        if cid:
            if eid and kind == "movie":
                clean = clean_url(url)
                return (title or "", [{"season": "", "episode": "", "title": "",
                        "identifier": clean, "watch_url": clean}])
            name, eps = show_episodes(cid)
            return (name or title or "", _rows(eps))

    # 2) movie by title -> single best-matching episode
    if kind == "movie" and title:
        j = get_json(SEARCH, {"media": "podcast", "entity": "podcastEpisode",
                              "term": title, "country": COUNTRY, "limit": 25})
        results = [r for r in (j.get("results") or []) if r.get("trackViewUrl")]
        if not results:
            return (title, [])
        best = max(results, key=lambda r: similarity(title, r.get("trackName", "")))
        u = clean_url(best["trackViewUrl"])
        return (title, [{"season": "", "episode": "", "title": best.get("trackName", ""),
                         "identifier": u, "watch_url": u}])

    # 3) series by title -> show -> all episodes
    if title:
        cid, name = find_show(title)
        if not cid:
            return (title or "", [])
        nm, eps = show_episodes(cid)
        return (nm or name or title or "", _rows(eps))

    return (title or "", [])


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Apple Podcasts show -> every episode URL (iTunes API).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="a podcasts.apple.com podcast/episode URL")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What Apple Podcasts show?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on Apple Podcasts. Try --url with the show link.")
        return 2
    if len(rows) >= LOOKUP_CAP:
        print(f"  (note: Apple's lookup caps at {LOOKUP_CAP} episodes — this show "
              f"may have more.)")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "apple").lower()).strip("-")
    out = os.path.join(args.outdir, f"applepodcasts_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} episode(s) on Apple Podcasts")
    for r in rows[:15]:
        print(f"   {r['identifier']}   {r['title'][:60]}")
    if len(rows) > 15:
        print(f"   ... and {len(rows) - 15} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
