#!/usr/bin/env python3
"""
hallmark_episode_identifier.py  —  Hallmark+ title -> episode item ids (no login)
==================================================================================
Hallmark Plus (hallmarkplus.com) is an Accedo One SPA whose playback URLs look
like  https://www.hallmarkplus.com/playback/item/<CODE>  where <CODE> encodes
the show + season + episode, e.g. MSTMNA002001 = show "MSTMNA", season 002,
episode 001.  We return  item/<CODE>  grouped by season:

    Mistletoe Murders   item/MSTMNA001001   Lionsgate   Hallmark Plus   Season 1
    Mistletoe Murders   item/MSTMNA002001   Lionsgate   Hallmark Plus   Season 2

How (no login): Hallmark+ ships a public JSON catalog API (no key/cookie):
    search   /api/core/search?q=<title>&page=1&pageSize=20&locale=en
             -> TV series are type=COLLECTION, subtype=TV_SHOW; `id` is the show
    seasons  /api/core/catalog/collection/<showId>?locale=en
             -> data[] of season collections (collection.id, collection.season.number)
    episodes /api/core/catalog/collection/<seasonId>?via=1.200.<showId>&page=1
             &pageSize=100&locale=en   -> data[] episodes (id, episode.number, title)

MAIN vs BONUS: a real episode's id is  <showLetters><SSS><EEE>  (e.g. MSTMNA002001);
bonus/extra material uses a DIFFERENT letter prefix (e.g. PNPRPA...) and carries
"Bonus Content" in the title -- and it even continues the episode.number count
(7, 8, ...), so we filter by the id prefix, NOT by episode.number.

Usage
-----
    python3 hallmark_episode_identifier.py --type series --title "Mistletoe Murders" --seasons 1,2
    python3 hallmark_episode_identifier.py --type series --url https://www.hallmarkplus.com/playback/item/MSTMNA002001
    python3 hallmark_episode_identifier.py                # interactive
"""

import argparse
import csv
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
BASE = "https://www.hallmarkplus.com/api/core"
PLATFORM = "Hallmark Plus"
ITEM_URL = "https://www.hallmarkplus.com/playback/item/%s"
# an episode item code: <letters><6 digits> (letters+SSS+EEE); bonus uses other letters
_CODE = re.compile(r"([A-Za-z]{3,})(\d{6})$")


# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def parse_seasons(raw):
    raw = (raw or "").strip().lower()
    if raw in ("", "all", "*", "a"):
        return None
    out = set()
    for part in raw.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out or None


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def getj(url, timeout=25):
    """GET a JSON endpoint; return parsed JSON ({} on failure)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.hallmarkplus.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def _letters(code):
    """Leading letter prefix of an id, e.g. 'MSTMNA00' -> 'MSTMNA'."""
    m = re.match(r"^([A-Za-z]+)", code or "")
    return m.group(1) if m else ""


# ── discovery ─────────────────────────────────────────────────────────────────
def _iter_items(obj):
    """Yield every dict that looks like a catalog item (has an id + type)."""
    if isinstance(obj, dict):
        if obj.get("id") and (obj.get("type") or obj.get("subtype")):
            yield obj
        for v in obj.values():
            yield from _iter_items(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_items(v)


def search(title, kind="series"):
    """Return (id, name) for the best-matching title, or (None, name)."""
    q = urllib.parse.quote(title or "")
    data = getj(f"{BASE}/search?q={q}&page=1&pageSize=20&locale=en")
    want = set(tokens(title))
    shows, movies = [], []
    for it in _iter_items(data):
        sub = str(it.get("subtype", "")).upper()
        typ = str(it.get("type", "")).upper()
        name = it.get("title") or ""
        score = len(want & set(tokens(name)))
        if typ == "COLLECTION" and sub in ("TV_SHOW", "SERIES", "SHOW"):
            shows.append((score, len(name), it.get("id"), name))
        elif sub in ("MOVIE", "FILM") or typ in ("VIDEO", "ITEM", "ASSET"):
            movies.append((score, len(name), it.get("id"), name))
    pool = movies if kind == "movie" else shows
    if not pool:  # fall back to the other bucket if the preferred one is empty
        pool = shows or movies
    if not pool:
        return None, (title or "")
    # best token overlap, then shortest title (prefer the exact series, not a spin-off)
    pool.sort(key=lambda t: (-t[0], t[1]))
    _, _, cid, name = pool[0]
    return cid, (name or title or "")


def show_seasons(show_id):
    """Return (show_title, [(seasonId, seasonNumber), ...]) for a show."""
    data = getj(f"{BASE}/catalog/collection/{show_id}?locale=en")
    name = ((data.get("collection") or {}).get("title")
            if isinstance(data, dict) else "") or ""
    seasons = []
    for it in (data.get("data") or []):
        col = it.get("collection", it) if isinstance(it, dict) else {}
        sid = col.get("id")
        num = ((col.get("season") or {}).get("number")
               if isinstance(col.get("season"), dict) else None)
        if sid is None:
            continue
        if num is None:  # derive season number from the id digits if absent
            m = _CODE.search(sid)
            num = int(m.group(2)[:3]) if m else None
        seasons.append((sid, num))
    return name, seasons


def season_episodes(season_id, show_id):
    """All episode dicts for one season (handles paging)."""
    out, page = [], 1
    total = None
    while True:
        url = (f"{BASE}/catalog/collection/{season_id}"
               f"?via=1.200.{show_id}&page={page}&pageSize=100&locale=en")
        data = getj(url)
        if total is None:
            total = (((data.get("collection") or {}).get("season") or {})
                     .get("episodeCount"))
        batch = data.get("data") or []
        out.extend(batch)
        if (not batch or page > 20
                or (isinstance(total, int) and len(out) >= total)):
            break
        page += 1
    return out


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Hallmark+ title to episode item ids.

    Returns (show_name, rows); each row is
        {season, episode, title, identifier, watch_url}
    with identifier = "item/<CODE>".  MAIN episodes only (bonus filtered out).
    """
    show_id = None
    base = None
    show = title or ""

    # 1) URL / pasted-code hint — derive the show id from an item code.
    if url:
        m = re.search(r"item/([A-Za-z0-9]+)", url) or _CODE.search(url)
        code = m.group(1) if m and m.lastindex else (m.group(0) if m else None)
        if code:
            base = _letters(code)
            if base:
                show_id = base + "00"   # Hallmark show-collection id = <letters>00

    # 2) title search
    if not show_id and title:
        show_id, show = search(title, kind)
        if show_id:
            base = _letters(show_id)

    if not show_id:
        return (show, [])
    if base is None:
        base = _letters(show_id)

    # 3) MOVIE — single item row (best-effort; series is the common case)
    if kind == "movie":
        code = None
        if url:
            m = re.search(r"item/([A-Za-z0-9]+)", url)
            code = m.group(1) if m else None
        code = code or show_id
        return (show, [{"season": "", "episode": "", "title": "",
                        "identifier": "item/%s" % code,
                        "watch_url": ITEM_URL % code}])

    # 4) SERIES — walk seasons, keep MAIN episodes only.
    name, season_list = show_seasons(show_id)
    if name:
        show = name
    rows = []
    for sid, snum in season_list:
        if seasons and (snum is None or snum not in seasons):
            continue
        for e in season_episodes(sid, show_id):
            eid = e.get("id") or ""
            # main episode: id is <showLetters><6 digits>; bonus uses other letters
            if not re.match(r"^%s\d{6}$" % re.escape(base), eid):
                continue
            if "bonus content" in (e.get("title") or "").lower():
                continue
            epnum = (e.get("episode") or {}).get("number")
            if epnum is None:
                mm = _CODE.search(eid)
                epnum = int(mm.group(2)[3:]) if mm else ""
            rows.append({
                "season": str(snum) if snum is not None else "",
                "episode": str(epnum),
                "title": e.get("title") or "",
                "identifier": "item/%s" % eid,
                "watch_url": ITEM_URL % eid,
            })
    return (show, rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Hallmark+ title -> episode item ids (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="a hallmarkplus.com playback/item URL")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    seasons = parse_seasons(args.seasons) if args.seasons is not None else None
    if not kind and not args.url:
        a = ask("Movie or Series?  [m/s]: ").lower()
        kind = "movie" if a.startswith("m") else "series"
    if not args.url and not title:
        title = ask("What title?: ")
    if kind == "series" and args.seasons is None and not args.url:
        seasons = parse_seasons(ask("Which season(s)?  1 | 1,3 | 1-4 | all: "))
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind or "series",
                             seasons=seasons)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Fetch/parse failed: {e!r}"); return 2
    if not rows:
        print("  ! Nothing found. Check the title, or pass "
              "--url https://www.hallmarkplus.com/playback/item/<CODE>.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "hallmark").lower()).strip("-")
    out = os.path.join(args.outdir, f"hallmark_{kind or 'series'}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "URL", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], PLATFORM])

    print(f"\nWrote {len(rows)} item id(s) for {show!r}.")
    for r in rows[:60]:
        tag = f" S{r['season']} E{r['episode']}" if r["season"] else ""
        print(f"   {show}{tag}  ->  {r['identifier']}  ({r['title']})" if tag
              else f"   {show}  ->  {r['identifier']}")
    if len(rows) > 60:
        print(f"   ... and {len(rows) - 60} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
