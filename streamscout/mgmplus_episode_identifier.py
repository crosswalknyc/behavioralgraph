#!/usr/bin/env python3
"""
mgmplus_episode_identifier.py  —  MGM+ title -> watch-path fragments (no login)
==============================================================================
MGM+ (mgmplus.com, formerly Epix) is a Next.js site whose watch URLs carry the
human slug, so the "portion we actually need" for clickstream is the path,
not an opaque id:

  MOVIE   full  https://www.mgmplus.com/movie/project-hail-mary-2026/watch
          keep  movie/project-hail-mary-2026/watch          (one row, no season)

  SERIES  full  https://www.mgmplus.com/series/robin-hood/watch/season/1/episode/1
          keep  robin-hood/watch/season/1/episode           (one row PER SEASON —
          a "season shell": the episode number is dropped, so the fragment
          matches every episode of that season in clickstream)

Note the asymmetry the sheet uses: movies keep the `movie/` prefix; series
drop the `series/` prefix and the trailing episode number.

How (all anonymous HTTP, no login):
  • Title -> slug: the public sitemap (sitemap/sitemap.xml) lists every
    /movie/<slug> and /series/<slug>. We match the query title to a slug
    (ignoring a trailing release year like `-2026`).
  • Series -> seasons: the series page embeds a Next.js RSC payload with a
    `seasons` array (each season has a `number`) and `numberOfSeasons`; we emit
    one season-shell fragment per season.
  • Movie: one row, the slug's /watch path.
  • URL hint: paste any mgmplus.com movie/series URL to skip the search.

Usage
-----
    python3 mgmplus_episode_identifier.py --type series --title "Robin Hood"
    python3 mgmplus_episode_identifier.py --type movie  --title "Project Hail Mary"
    python3 mgmplus_episode_identifier.py --url https://www.mgmplus.com/series/robin-hood/watch/season/1/episode/1
    python3 mgmplus_episode_identifier.py                 # interactive
"""

import argparse
import csv
import gzip
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.mgmplus.com"
SITEMAP = "https://www.mgmplus.com/sitemap/sitemap.xml"
PLATFORM = "MGM Plus"

_MOVIE_URL = re.compile(r"/movie/([a-z0-9-]+)")
_SERIES_URL = re.compile(r"/series/([a-z0-9-]+)")
_YEAR_TAIL = re.compile(r"-(?:19|20)\d{2}$")

_SITEMAP_CACHE = None


# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def slug_tokens(slug):
    """Tokens of a slug with any trailing release year dropped."""
    return tokens(_YEAR_TAIL.sub("", slug or ""))


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.getcode(), r.geturl(), raw.decode("utf-8", "replace")
    except Exception:
        return None, url, ""


def _unescape(html):
    """The RSC payload embeds JSON with escaped quotes/slashes."""
    return (html or "").replace('\\"', '"').replace('\\/', '/')


# ── discovery (title -> slug via the public sitemap) ──────────────────────────
def sitemap():
    """[(kind, slug)] for every /movie/<slug> and /series/<slug>. Cached."""
    global _SITEMAP_CACHE
    if _SITEMAP_CACHE is None:
        _, _, xml = fetch(SITEMAP)
        out = []
        for kind, pat in (("movie", _MOVIE_URL), ("series", _SERIES_URL)):
            for slug in pat.findall(xml or ""):
                out.append((kind, slug))
        # de-dupe, keep order
        _SITEMAP_CACHE = list(dict.fromkeys(out))
    return _SITEMAP_CACHE


def find_slug(title, kind):
    """Best matching slug for a title within the requested kind, or None.
    Exact token match (ignoring a trailing year) wins; else subset; shortest
    slug breaks ties (so "Robin Hood" doesn't grab "Robin Hood of the Pecos")."""
    want = tokens(title)
    if not want:
        return None
    exact, subset = [], []
    for k, slug in sitemap():
        if k != kind:
            continue
        st = slug_tokens(slug)
        if st == want:
            exact.append(slug)
        elif set(want) <= set(st):
            subset.append(slug)
    if exact:
        return min(exact, key=len)
    if subset:
        return min(subset, key=len)
    return None


# ── page parsing ──────────────────────────────────────────────────────────────
def _title_for(page, slug, fallback):
    m = re.search(r'"title":"([^"]+)","shortName":"%s"' % re.escape(slug), page)
    if m:
        return m.group(1)
    # pretty-print the slug (minus trailing year) as a last resort
    return fallback or _YEAR_TAIL.sub("", slug).replace("-", " ").title()


def season_numbers(page):
    """Season numbers from a series page's RSC payload. Season objects are the
    only ones followed by `"extras"`, so `"number":N,"extras"` isolates them."""
    nums = sorted({int(n) for n in re.findall(r'"number":(\d+),"extras"', page)})
    if nums:
        return nums
    m = re.search(r'"numberOfSeasons":(\d+)', page)     # fallback: 1..N
    return list(range(1, int(m.group(1)) + 1)) if m else [1]


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve an MGM+ title to watch-path fragments.

    Returns (show_name, rows). Movie -> one row; series -> one season-shell row
    per season:
        {season:"1", episode:"", title:"",
         identifier:"robin-hood/watch/season/1/episode",
         watch_url:"https://www.mgmplus.com/series/robin-hood/watch/season/1/episode/1"}
    `seasons` (set[int] or None) filters series rows. Paste any mgmplus.com
    movie/series URL via `url` to skip the sitemap search.
    """
    slug = None

    # 1) URL hint — the path tells us kind + slug directly.
    if url:
        ms, mm = _SERIES_URL.search(url), _MOVIE_URL.search(url)
        if mm:
            slug, kind = mm.group(1), "movie"
        elif ms:
            slug, kind = ms.group(1), "series"

    # 2) title search via sitemap
    if not slug and title:
        slug = find_slug(title, kind)
    if not slug:
        return (title or "", [])

    # 3a) MOVIE — one row, the /watch path (keeps the `movie/` prefix)
    if kind == "movie":
        _, _, page = fetch("%s/movie/%s" % (BASE, slug))
        page = _unescape(page)          # RSC payload embeds escaped JSON
        show = _title_for(page, slug, title)
        return (show, [{
            "season": "", "episode": "", "title": show,
            "identifier": "movie/%s/watch" % slug,
            "watch_url": "%s/movie/%s/watch" % (BASE, slug),
        }])

    # 3b) SERIES — one season-shell row per season (drops `series/` + ep number)
    _, _, page = fetch("%s/series/%s" % (BASE, slug))
    page = _unescape(page)              # RSC payload embeds escaped JSON
    show = _title_for(page, slug, title)
    rows = []
    for n in season_numbers(page):
        if seasons and n not in seasons:
            continue
        rows.append({
            "season": str(n), "episode": "", "title": "",
            "identifier": "%s/watch/season/%d/episode" % (slug, n),
            "watch_url": "%s/series/%s/watch/season/%d/episode/1" % (BASE, slug, n),
        })
    return (show, rows)


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="MGM+ title -> watch-path fragments (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1  |  1,2  |  1-4 (series only)")
    ap.add_argument("--url", help="a mgmplus.com movie/series URL (skips search)")
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
        print("  ! Not found on MGM+. Try --url with the mgmplus.com link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "mgmplus").lower()).strip("-")
    out = os.path.join(args.outdir, f"mgmplus_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            season = f"Season {r['season']}" if r["season"] else ""
            w.writerow([show, r["identifier"], PLATFORM, season])

    print(f"\n{show}  ->  {len(rows)} row(s) on MGM+")
    for r in rows[:20]:
        extra = f" (Season {r['season']})" if r["season"] else ""
        print(f"   {r['identifier']}{extra}")
    if len(rows) > 20:
        print(f"   ... and {len(rows) - 20} more")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
