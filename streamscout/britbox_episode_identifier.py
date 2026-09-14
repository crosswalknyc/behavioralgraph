#!/usr/bin/env python3
"""
britbox_episode_identifier.py  —  BritBox title -> watch-path shell (no login)
=============================================================================
BritBox (britbox.com) uses ONE stable URL for a whole title — the show/movie
page URL is the same one used for every season and episode (only a
`?usecaseid=` query differs, which we drop). So the "portion we need" for
clickstream is a single shell path per title:

  SERIES  full  https://www.britbox.com/us/show/The_Other_Bennet_Sister_167491?usecaseid=...
          keep  show/The_Other_Bennet_Sister_167491        (one SERIES-SHELL row;
          the URL doesn't change across seasons/episodes, so one row covers all)

  MOVIE   full  https://www.britbox.com/us/movie/This_is_Joan_Collins_p0bp4xk2
          keep  movie/This_is_Joan_Collins_p0bp4xk2         (one row)

A BritBox slug is `<Title_Words>_<id>` where <id> is a BBC pid ([bpm]xxxxxxx)
or a 5-8 digit number, e.g. `Vera_p053fylw`, `The_Other_Bennet_Sister_167491`.

How (all anonymous HTTP, no login):
  • Title -> slug: the public sitemap (dynamic-sitemap.xml) lists every
    /us/show/<slug> and /us/movie/<slug>. We match the query title to a slug
    after stripping the trailing id (and any `FS` / year disambiguators).
  • No per-episode walk is needed — one shell path is the whole title.
  • URL hint: paste any britbox.com show/movie URL to skip the search.

Usage
-----
    python3 britbox_episode_identifier.py --type series --title "Vera"
    python3 britbox_episode_identifier.py --type movie  --title "This Is Joan Collins"
    python3 britbox_episode_identifier.py --url https://www.britbox.com/us/show/Vera_p053fylw
    python3 britbox_episode_identifier.py                 # interactive
"""

import argparse
import csv
import gzip
import os
import re
import sys
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.britbox.com"
SITEMAPS = ("https://www.britbox.com/dynamic-sitemap.xml",
            "https://www.britbox.com/static-sitemap.xml")
PLATFORM = "BritBox"

# a BritBox slug's trailing id: a BBC pid ([bpm] + 7) or a 4-8 digit number
_ID_SEG = re.compile(r"^(?:[bpm][0-9a-z]{7}|\d{4,8})$", re.I)
# any /us/show|movie/<slug> in the sitemap or a pasted URL (slug ends at ?/#)
_URL_SLUG = re.compile(r"/us/(show|movie)/([^/?#\"'\s<]+)")

_SITEMAP_CACHE = None


# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


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


# ── slug helpers ──────────────────────────────────────────────────────────────
def _strip_id(slug):
    """Drop the trailing id segment from a slug ('Vera_p053fylw' -> 'Vera')."""
    parts = (slug or "").rsplit("_", 1)
    if len(parts) == 2 and _ID_SEG.match(parts[1]):
        return parts[0]
    return slug or ""


def slug_title_tokens(slug):
    """Match tokens for a slug: id stripped, plus `FS` and year disambiguators
    dropped so 'Are_You_Being_Served_2016_p05ml66l' matches 'Are You Being
    Served'."""
    base = _strip_id(slug)
    keep = [t for t in base.split("_")
            if t and t.upper() != "FS" and not re.fullmatch(r"(?:19|20)\d{2}", t)]
    return tokens(" ".join(keep))


def slug_to_title(slug):
    """Human title from a slug: id + `FS` stripped, underscores -> spaces
    (original casing preserved, e.g. 'This_is_Joan_Collins_...' -> 'This is
    Joan Collins')."""
    base = _strip_id(slug)
    words = [w for w in base.split("_") if w and w.upper() != "FS"]
    return " ".join(words).strip()


# ── discovery (title -> slug via the public sitemap) ──────────────────────────
def sitemap():
    """[(kind, slug)] for every /us/show/<slug> and /us/movie/<slug>. Cached."""
    global _SITEMAP_CACHE
    if _SITEMAP_CACHE is None:
        out = []
        for sm in SITEMAPS:
            _, _, xml = fetch(sm)
            for kind, slug in _URL_SLUG.findall(xml or ""):
                out.append(("movie" if kind == "movie" else "series", slug))
        _SITEMAP_CACHE = list(dict.fromkeys(out))          # de-dupe, keep order
    return _SITEMAP_CACHE


def find_slug(title, kind):
    """Best matching slug for a title within the requested kind, or None.
    Exact token match (after stripping the id) wins; else subset; shortest slug
    breaks ties (so 'Vera' picks 'Vera_p053fylw', not 'Vera_Postmortem_...')."""
    want = tokens(title)
    if not want:
        return None
    exact, subset = [], []
    for k, slug in sitemap():
        if k != kind:
            continue
        st = slug_title_tokens(slug)
        if st == want:
            exact.append(slug)
        elif set(want) <= set(st):
            subset.append(slug)
    if exact:
        return min(exact, key=len)
    if subset:
        return min(subset, key=len)
    return None


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a BritBox title to its single watch-path shell.

    Returns (show_name, rows) with ONE row (BritBox uses one URL per title):
        {season:"", episode:"", title:<show>,
         identifier:"show/The_Other_Bennet_Sister_167491",
         watch_url:"https://www.britbox.com/us/show/The_Other_Bennet_Sister_167491"}
    `seasons` is accepted for interface parity but ignored (the shell covers all
    seasons). Paste any britbox.com show/movie URL via `url` to skip the search.
    """
    slug, seg = None, ("movie" if kind == "movie" else "show")

    # 1) URL hint — the path gives kind + slug directly.
    if url:
        m = _URL_SLUG.search(url)
        if m:
            seg = m.group(1)
            kind = "movie" if seg == "movie" else "series"
            slug = m.group(2)

    # 2) title search via sitemap
    if not slug and title:
        slug = find_slug(title, kind)
        seg = "movie" if kind == "movie" else "show"
    if not slug:
        return (title or "", [])

    show = slug_to_title(slug) or (title or "")
    return (show, [{
        "season": "", "episode": "", "title": show,
        "identifier": "%s/%s" % (seg, slug),
        "watch_url": "%s/us/%s/%s" % (BASE, seg, slug),
    }])


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="BritBox title -> watch-path shell (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="(accepted but ignored; one shell per title)")
    ap.add_argument("--url", help="a britbox.com show/movie URL (skips search)")
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

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind or "series")
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on BritBox. Try --url with the britbox.com link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "britbox").lower()).strip("-")
    out = os.path.join(args.outdir, f"britbox_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} row(s) on BritBox")
    for r in rows:
        print(f"   {r['identifier']}")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
