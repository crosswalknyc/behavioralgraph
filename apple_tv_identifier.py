#!/usr/bin/env python3
"""
apple_tv_identifier.py  —  Apple TV title -> show identifier (no login)
=======================================================================
Apple TV is different from the episodic platforms: a whole series lives under a
single "shell" URL keyed by one show id, e.g.

    https://tv.apple.com/us/show/ted-lasso/umc.cmc.vtoh0mn0xn7t3c643xqonfzy

and every episode of every season sits under that same id.  We return ONE row
per title, whose identifier is the id WITHOUT the `umc.cmc.` prefix:

    Ted Lasso    vtoh0mn0xn7t3c643xqonfzy    WBD    Apple TV

Discovery (no login): Apple's `/<store>/search?term=<title>` page ships a
server-side JSON blob (`serialized-server-data`) of `SearchCardComponent`
entries -- each has {id: "umc.cmc.…", title, type ("TV Show"/"Movie")}.  We pick
the best title/type match and read the id straight off it.  Paste any
tv.apple.com URL to skip search.

Usage
-----
    python3 apple_tv_identifier.py --type series --title "Ted Lasso"
    python3 apple_tv_identifier.py --type movie  --title "Wolfs"
    python3 apple_tv_identifier.py --url https://tv.apple.com/us/show/ted-lasso/umc.cmc.vtoh0mn0xn7t3c643xqonfzy
    python3 apple_tv_identifier.py                 # interactive
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

try:
    import json
except ImportError:  # pragma: no cover
    json = None

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UMC = re.compile(r"umc\.cmc\.[a-z0-9]+")
_SERVER_DATA = re.compile(
    r'id=["\']serialized-server-data["\'][^>]*>(.*?)</script>', re.S)
_OG_URL = re.compile(
    r'property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', re.I)
STORE = "us"


# ── helpers ───────────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return r.getcode(), r.geturl(), raw.decode("utf-8", "replace")


def strip_prefix(umc_id):
    """'umc.cmc.vtoh…' -> 'vtoh…'."""
    return umc_id.split("umc.cmc.", 1)[-1] if umc_id else ""


# ── discovery: Apple TV search ────────────────────────────────────────────────
def search(term):
    """Return [{id, title, type}] SearchCardComponent hits for a query term."""
    url = ("https://tv.apple.com/%s/search?term=%s"
           % (STORE, urllib.parse.quote(term)))
    try:
        _, _, html = fetch(url)
    except Exception:
        return []
    m = _SERVER_DATA.search(html)
    if not (m and json):
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []

    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            idv = o.get("id")
            title = o.get("title")
            if (isinstance(idv, str) and idv.startswith("umc.cmc.")
                    and title and idv not in seen):
                seen.add(idv)
                out.append({"id": idv, "title": title,
                            "type": o.get("type") or ""})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


def pick(cands, title, kind):
    """Best candidate for (title, kind); None if nothing plausible."""
    if not cands:
        return None
    want = tokens(title)
    want_type = "Movie" if kind == "movie" else "TV Show"

    def rank(c):
        ct = tokens(c["title"])
        exact = ct == want
        subset = bool(want) and set(want) <= set(ct)
        typ = (c.get("type") == want_type)
        return (exact, typ, subset)

    best = max(cands, key=rank)
    r = rank(best)
    # accept an exact/subset title match, or (failing that) the top relevance hit
    if r[0] or r[2]:
        return best
    return cands[0]


def canonical_url(umc_id, kind):
    """Resolve the pretty canonical show/movie URL for an id (best effort)."""
    path = "movie" if kind == "movie" else "show"
    base = "https://tv.apple.com/%s/%s/%s" % (STORE, path, umc_id)
    try:
        _, final, html = fetch(base)
        m = _OG_URL.search(html)
        if m and "umc.cmc" in m.group(1):
            return m.group(1)
        if final and "umc.cmc" in final:
            return final
    except Exception:
        pass
    return base


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve an Apple TV title to its show identifier.

    Returns (show_name, rows) with a SINGLE row (Apple keys a whole series to
    one id):  {season:"", episode:"", title:"", identifier:<id-no-prefix>,
    watch_url:<canonical>}.  `seasons` is ignored.  Paste a tv.apple.com URL via
    `url` to skip search.
    """
    umc_full, show = None, title or ""

    if url:
        m = UMC.search(url)
        if m:
            umc_full = m.group(0)

    if not umc_full and title:
        best = pick(search(title), title, kind)
        if best:
            umc_full = best["id"]
            show = best["title"]

    if not umc_full:
        return (show, [])

    watch = canonical_url(umc_full, kind)
    if not show:  # last resort: derive a name from the canonical slug
        sm = re.search(r"/(?:show|movie)/([^/]+)/umc\.cmc\.", watch)
        if sm:
            show = sm.group(1).replace("-", " ").title()

    return (show, [{"season": "", "episode": "", "title": "",
                    "identifier": strip_prefix(umc_full), "watch_url": watch}])


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Apple TV title -> show identifier (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--url", help="a tv.apple.com URL (skips search)")
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
        print("  ! Not found on Apple TV. Try --url with the tv.apple.com link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "apple").lower()).strip("-")
    out = os.path.join(args.outdir, f"appletv_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "IDENTIFIER", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["identifier"], r["watch_url"], "Apple TV"])

    print(f"\n{show}  ->  {rows[0]['identifier']}")
    print(f"   {rows[0]['watch_url']}")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
