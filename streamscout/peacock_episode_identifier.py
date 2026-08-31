#!/usr/bin/env python3
"""
peacock_episode_identifier.py  —  Peacock title -> episode watch UUIDs (by season)
==================================================================================
A DIRECT resolver for Peacock, so titles work even when they were never watched
in clickstream.  Peacock's public `/stream-tv/<slug>` landing page (NO login)
embeds an Apollo GraphQL state whose `ASSET/EPISODE` entries carry the full
episode tree -- their `url` looks like:

    /watch-online/tv/<slug>/<seriesId>/seasons/<N>/episodes/<ep-slug>/<UUID>

where the trailing <UUID> is exactly the playback id used at
`peacocktv.com/watch/playback/vod/_/<UUID>`.

Discovery: the slug is just the title ("Brilliant Minds" -> "brilliant-minds"),
so we guess `/stream-tv/<slug>` first, then fall back to a web search. Some
titles (esp. Telemundo/Spanish series like "La Casa de los Famosos") have NO
`/stream-tv/<slug>` page at all -- they only live at the canonical
`/watch-online/tv/<slug>/<seriesId>` URL. We handle those by (a) fetching a
pasted `/watch-online/tv/...` URL directly and (b) letting the web-search
fallback return that series-id page too.

Usage
-----
    python3 peacock_episode_identifier.py --type series --title "Brilliant Minds" --seasons 1,2
    python3 peacock_episode_identifier.py --type series --url https://www.peacocktv.com/stream-tv/poker-face
    python3 peacock_episode_identifier.py --type movie --title "The Wild Robot"
    python3 peacock_episode_identifier.py                # interactive
"""

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_NEXT = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_OG_TITLE = re.compile(
    r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_EPURL = re.compile(r"/seasons/(\d+)/episodes/([^/?#]+)/(" + UUID + ")")
_MOVIEURL = re.compile(r"/movies/([^/?#]+)/(" + UUID + ")")
_ANYUUID = re.compile(UUID)
PLAYBACK = "https://www.peacocktv.com/watch/playback/vod/_/%s"
SITEMAP = "https://www.peacocktv.com/sitemap.xml"
_MOVIE_INDEX = None  # lazily-built [(slug, uuid)] from the movies sitemap


# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


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


def fetch(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return r.getcode(), r.geturl(), raw.decode("utf-8", "replace")


# ── discovery ─────────────────────────────────────────────────────────────────
def _slug_from_url(u):
    m = re.search(r"/(?:stream-tv|watch-online/tv|watch/asset/tv|tv|movies)/"
                  r"([a-z0-9-]+)", u or "", re.I)
    return m.group(1) if m else None


# A "content page" URL that embeds the full episode tree: either a /stream-tv/
# landing page, or a canonical /watch-online/tv/<slug>/<seriesId> (the only home
# some Telemundo/Spanish titles have). watch/asset/tv/<slug>/<seriesId> works too.
_CONTENT_URL = re.compile(
    r"peacocktv\.com/(?:stream-tv/[a-z0-9-]+"
    r"|(?:watch-online|watch/asset)/tv/[a-z0-9-]+/\d+)", re.I)
# Show-page links we accept from a web-search result page.
_PAGE_LINK = re.compile(
    r"https?://www\.peacocktv\.com/(?:stream-tv/[a-z0-9-]+"
    r"|watch-online/tv/[a-z0-9-]+/\d+)", re.I)


def _fetch_tree(u):
    """Fetch a full Peacock content URL; return (final, html) iff it carries the
    __NEXT_DATA__ episode tree, else (None, None)."""
    try:
        u = u if u.startswith("http") else "https://" + u.lstrip("/")
        _, final, html = fetch(u)
        if _NEXT.search(html):
            return final, html
    except Exception:
        pass
    return None, None


def search_peacock_page(title):
    """Fallback: find a Peacock show page via web search. Returns a /stream-tv/
    or /watch-online/tv/<slug>/<seriesId> URL (the latter is what covers titles
    with no stream-tv page, e.g. "La Casa de los Famosos")."""
    want = set(tokens(title))

    def _slug_score(u):
        m = re.search(r"/(?:stream-tv|watch-online/tv)/([a-z0-9-]+)", u)
        return len(want & set(tokens(m.group(1)))) if m else 0

    for q in (f"{title} peacock watch-online tv",
              f"{title} peacock stream-tv",
              f"site:peacocktv.com {title}"):
        html = ""
        for engine in ("https://html.duckduckgo.com/html/",
                       "https://lite.duckduckgo.com/lite/"):
            try:
                _, _, html = fetch(engine,
                                   data=urllib.parse.urlencode({"q": q}).encode())
                break
            except Exception:
                time.sleep(0.8)
        if not html:
            continue
        # DDG wraps hrefs percent-encoded; search both raw + decoded text.
        blob = html + "\n" + urllib.parse.unquote(html)
        links = sorted(set(_PAGE_LINK.findall(blob)), key=lambda u: -_slug_score(u))
        if links and _slug_score(links[0]) > 0:
            return links[0]
    return None


def _load_page(title, url):
    """Return (final_url, html) for a page carrying the episode tree, else
    (None, None). Order: pasted content URL -> /stream-tv/<slug> guess ->
    web-search (stream-tv OR watch-online/tv/<seriesId>)."""
    # 1) a pasted full content URL (stream-tv, or watch-online/tv/<seriesId>).
    if url:
        m = _CONTENT_URL.search(url)
        if m:
            # try the exact pasted URL first (may point straight at /seasons/N),
            # then the trimmed canonical page.
            for cand in (url, m.group(0)):
                final, html = _fetch_tree(cand)
                if html:
                    return final, html
    # 2) guess /stream-tv/<slug> from the url slug or the title.
    slug = _slug_from_url(url) if url else None
    if not slug and title:
        slug = slugify(title)
    if slug:
        final, html = _fetch_tree("https://www.peacocktv.com/stream-tv/" + slug)
        if html:
            return final, html
    # 3) web-search fallback (covers titles with no stream-tv page at all).
    if title:
        link = search_peacock_page(title)
        if link:
            final, html = _fetch_tree(link)
            if html:
                return final, html
    return None, None


# ── parsing ───────────────────────────────────────────────────────────────────
def show_name(html, fallback=""):
    m = _OG_TITLE.search(html or "")
    if m:
        t = m.group(1)
        t = re.sub(r"^\s*watch\s+", "", t, flags=re.I)
        t = re.sub(r"\s*\|\s*peacock.*$", "", t, flags=re.I)
        # Peacock's per-season pages title as "<Show> Season N - Streaming
        # Online"; peel those marketing suffixes off (in any order) so we keep
        # just the clean series name.
        suffixes = (
            r"\s*[-–—|:]?\s*streaming\s+online\s*$",
            r"\s*[-–—|:]?\s*season\s+\d+\s*$",
            r"\s*[-–—|:]?\s*full\s+episodes\s*$",
            r"\s*\((?:tv\s+)?series\)\s*$",
        )
        prev = None
        while prev != t:
            prev = t
            for suf in suffixes:
                t = re.sub(suf, "", t, flags=re.I).strip()
        if t.strip():
            return t.strip()
    return fallback


def collect_episodes(data):
    """Walk the Apollo state for ASSET/EPISODE entries; parse url for the tree."""
    eps = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "ASSET/EPISODE":
                u = o.get("url") or o.get("slug") or ""
                m = _EPURL.search(u)
                if m:
                    uid = m.group(3)
                    eps[uid] = {
                        "season": int(m.group(1)),
                        "number": o.get("number"),
                        "name": o.get("episodeName") or o.get("title") or "",
                        "uuid": uid,
                    }
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return sorted(eps.values(),
                  key=lambda e: (e["season"],
                                 e["number"] if isinstance(e["number"], int) else 0))


def _movie_index():
    """Build (and cache) a [(slug, uuid)] list from Peacock's movies sitemap.

    Movie pages live at /watch-online/movies/<slug>/<UUID>, so the sitemap is a
    direct title -> playback-UUID index for every film currently on Peacock.
    """
    global _MOVIE_INDEX
    if _MOVIE_INDEX is not None:
        return _MOVIE_INDEX
    idx = []
    try:
        _, _, top = fetch(SITEMAP)
        maps = [c for c in re.findall(r"<loc>([^<]+)</loc>", top)
                if "content_page_movies" in c]
        for m in maps:
            _, _, body = fetch(m)
            for u in re.findall(r"<loc>([^<]+)</loc>", body):
                mm = _MOVIEURL.search(u)
                if mm:
                    idx.append((mm.group(1), mm.group(2)))
    except Exception:
        pass
    _MOVIE_INDEX = idx
    return idx


def movie_uuid(title=None, url=None):
    """Return a single movie playback UUID, or None.

    From an explicit URL we read the UUID directly; from a title we require an
    EXACT slug match in the movies sitemap (a trailing release year is allowed),
    so we never return a look-alike film.
    """
    if url:
        m = _MOVIEURL.search(url) or re.search(r"/vod/_/(" + UUID + ")", url)
        if m:
            return m.group(m.lastindex)
        m = _ANYUUID.search(url)
        if m:
            return m.group(0)
    if title:
        want = tokens(title)
        for slug, uid in _movie_index():
            st = tokens(slug)
            if st == want:                       # exact title match
                return uid
        for slug, uid in _movie_index():         # allow a trailing year
            st = tokens(slug)
            if len(st) == len(want) + 1 and st[:-1] == want and st[-1].isdigit():
                return uid
    return None


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Peacock title to episode/movie watch UUIDs.

    Returns (show_name, rows); each row is
        {season, episode, title, identifier, watch_url}
    with the identifier being the playback UUID (season/episode "" for a movie).
    """
    if kind == "movie":
        vid = movie_uuid(title=title, url=url)
        if vid:
            return (title or "", [{"season": "", "episode": "", "title": "",
                                   "identifier": vid,
                                   "watch_url": PLAYBACK % vid}])
        return (title or "", [])

    final, html = _load_page(title, url)
    if not html:
        return (title or "", [])
    data_m = _NEXT.search(html)
    data = json.loads(data_m.group(1)) if data_m else {}
    show = show_name(html, title or "")

    rows = []
    for e in collect_episodes(data):
        if seasons is not None and e["season"] not in seasons:
            continue
        rows.append({
            "season": str(e["season"]),
            "episode": str(e["number"]) if e["number"] is not None else "",
            "title": e["name"], "identifier": e["uuid"],
            "watch_url": PLAYBACK % e["uuid"]})
    return (show, rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Peacock title -> episode watch UUIDs (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="a peacocktv.com URL: /stream-tv/<slug> OR "
                    "/watch-online/tv/<slug>/<seriesId>")
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
        if kind == "movie":
            print("  ! Not found on Peacock right now (licensing windows "
                  "rotate). Try --url with the peacocktv.com movie link.")
        else:
            print("  ! Couldn't find episodes. Re-run with --url — either "
                  "https://www.peacocktv.com/stream-tv/<slug> or the canonical "
                  "https://www.peacocktv.com/watch-online/tv/<slug>/<seriesId>.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "peacock").lower()).strip("-")
    out = os.path.join(args.outdir, f"peacock_{kind or 'series'}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "WATCH_ID", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], "Peacock"])

    print(f"\nWrote {len(rows)} watch id(s) for {show!r}.")
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
