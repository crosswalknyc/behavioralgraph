#!/usr/bin/env python3
"""
paramount_episode_identifier.py  —  Paramount+ title -> watch video ids (no login)
==================================================================================
Paramount+ watch URLs look like  https://www.paramountplus.com/shows/video/<id>/
(and /movies/video/<id>/ for films).  The <id> is what we return, grouped by
season for series:

    Landman   6hPvmRO1ZRVTbgEqEI8_DdZoX7Y5AciH   Paramount TV Studios   Paramount Plus   Season 1
    Landman   x5NEbffXFD_NfJ135CpYteQWjyoe4tdI   Paramount TV Studios   Paramount Plus   Season 2

How (no login): Paramount+'s public per-season episode feed is
    /shows/<slug>/xhr/episodes/page/<p>/size/100/xs/0/season/<N>/
which returns JSON with content_id / season_number / episode_number /
episode_title for every episode.  The site is behind Akamai, which 406s Python's
TLS fingerprint, so we shell out to `curl` (real TLS) for every request.

Discovery: the show slug is just the title ("Landman" -> "landman"), confirmed by
fetching /shows/<slug>/.  Movies are catalog-JS-rendered, so movie-by-title is
best-effort (slug guess); pasting a paramountplus.com URL always works.

Usage
-----
    python3 paramount_episode_identifier.py --type series --title "Landman" --seasons 1,2
    python3 paramount_episode_identifier.py --type series --url https://www.paramountplus.com/shows/landman/
    python3 paramount_episode_identifier.py --type movie  --url https://www.paramountplus.com/movies/video/<id>/
    python3 paramount_episode_identifier.py                 # interactive
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BASE = "https://www.paramountplus.com"
VIDEO_ID = r"[A-Za-z0-9_]{16,}"
_SENTINEL = "__HTTP_CODE__"
_OG_TITLE = re.compile(
    r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I)
MAX_SEASONS = 40  # safety cap when enumerating "all"


# ── helpers ───────────────────────────────────────────────────────────────────
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


def curl(url, json_accept=False, timeout=45):
    """Fetch via curl (bypasses Akamai's Python-TLS 406). Returns (code, body)."""
    accept = ("application/json, text/plain, */*" if json_accept
              else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    cmd = ["curl", "-s", "--compressed", "-A", UA,
           "-H", "Accept: " + accept,
           "-H", "Accept-Language: en-US,en;q=0.9",
           "-H", "Referer: https://www.paramountplus.com/"]
    if json_accept:
        cmd += ["-H", "X-Requested-With: XMLHttpRequest"]
    cmd += ["-w", "\n" + _SENTINEL + "%{http_code}", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout).stdout
    except Exception:
        return 0, ""
    code = 0
    if _SENTINEL in out:
        out, _, c = out.rpartition(_SENTINEL)
        try:
            code = int(c.strip())
        except ValueError:
            code = 0
        out = out[:-1] if out.endswith("\n") else out
    return code, out


def show_name(html, fallback=""):
    m = _OG_TITLE.search(html or "")
    if m:
        t = m.group(1)
        t = re.sub(r"^\s*watch\s+", "", t, flags=re.I)
        t = re.sub(r"\s+movies?\s*(?:-|on)\s*paramount\+?.*$", "", t, flags=re.I)
        t = re.sub(r"\s+on\s+paramount\+?.*$", "", t, flags=re.I)
        if t.strip():
            return t.strip()
    return fallback


# ── series ────────────────────────────────────────────────────────────────────
def _fetch_season(slug, season):
    """All episode dicts for one season (handles paging + rate-limit retry)."""
    episodes, page, total = [], 0, None
    while True:
        url = ("%s/shows/%s/xhr/episodes/page/%d/size/100/xs/0/season/%d/"
               % (BASE, slug, page, season))
        data = None
        for attempt in range(4):
            _, body = curl(url, json_accept=True)
            try:
                data = json.loads(body)
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("success") is False:
                time.sleep(0.8 * (attempt + 1))  # "Request Exceeds Limits"
                data = None
                continue
            break
        result = (data or {}).get("result")
        if not isinstance(result, dict):
            break
        batch = result.get("data") or []
        episodes.extend(batch)
        total = result.get("total")
        page += 1
        if not batch or total is None or len(episodes) >= total or page > 20:
            break
        time.sleep(0.3)
    return episodes


def _row_from_episode(e):
    vid = e.get("content_id") or ""
    if not vid:
        return None
    return {
        "season": str(e.get("season_number") or e.get("seasonNumber") or ""),
        "episode": str(e.get("episode_number") or e.get("episodeNumber") or ""),
        "title": e.get("episode_title") or e.get("label") or "",
        "identifier": vid,
        "watch_url": "%s/shows/video/%s/" % (BASE, vid),
    }


def _resolve_series(slug, seasons):
    rows = []
    want = sorted(seasons) if seasons else range(1, MAX_SEASONS + 1)
    for s in want:
        eps = _fetch_season(slug, s)
        if not eps:
            if seasons is None:
                break          # first empty season ends "all" enumeration
            continue           # a requested season may just be empty
        for e in eps:
            r = _row_from_episode(e)
            if r:
                rows.append(r)
        time.sleep(0.3)
    return rows


def _series_slug(title, url):
    """Return (slug, show_name) or (None, name). Confirms via /shows/<slug>/."""
    slug = None
    if url:
        m = re.search(r"/shows/([a-z0-9-]+)/", url)
        if m and m.group(1) not in ("video",):
            slug = m.group(1)
    if not slug and title:
        slug = slugify(title)
    if not slug:
        return None, title or ""
    code, html = curl("%s/shows/%s/" % (BASE, slug))
    if code == 200 and "/xhr/episodes" not in html and _OG_TITLE.search(html):
        # 200 with a real show page — accept it
        return slug, show_name(html, title or "")
    if code == 200:
        return slug, show_name(html, title or "")
    return None, title or ""


# ── movies (best-effort by title; reliable via pasted URL) ────────────────────
def _resolve_movie(title, url):
    vid = None
    if url:
        m = (re.search(r"/(?:movies|shows)/video/(" + VIDEO_ID + r")/", url)
             or re.search(r"/movies/[a-z0-9-]+/(" + VIDEO_ID + r")/", url))
        if m:
            vid = m.group(1)
    name = title or ""
    if not vid and title:
        code, html = curl("%s/movies/%s/" % (BASE, slugify(title)))
        if code == 200:
            name = show_name(html, title)
            m = re.search(r"/movies/video/(" + VIDEO_ID + r")/", html)
            if m:
                vid = m.group(1)
    if not vid:
        return name, []
    return name, [{"season": "", "episode": "", "title": "",
                   "identifier": vid,
                   "watch_url": "%s/movies/video/%s/" % (BASE, vid)}]


# ── reusable resolver (imported by title_lookup.py) ───────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Paramount+ title to watch video ids.

    Returns (show_name, rows); each row is
        {season, episode, title, identifier, watch_url}
    (season/episode/title are "" for a movie).  `seasons` is a set or None (all).
    """
    if kind == "movie":
        return _resolve_movie(title, url)

    slug, name = _series_slug(title, url)
    if not slug:
        return (title or "", [])
    rows = _resolve_series(slug, seasons)
    return (name or title or "", rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Paramount+ title -> watch video ids (no login).")
    ap.add_argument("--type", choices=["movie", "series"])
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="a paramountplus.com URL")
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
        print("  ! Nothing found. For series, check the title; for movies, pass "
              "--url https://www.paramountplus.com/movies/video/<id>/")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "paramount").lower()).strip("-")
    out = os.path.join(args.outdir, f"paramount_{kind or 'series'}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "SEASON", "EPISODE", "EPISODE_TITLE",
                    "WATCH_ID", "WATCH_URL", "PLATFORM"])
        for r in rows:
            w.writerow([show, r["season"], r["episode"], r["title"],
                        r["identifier"], r["watch_url"], "Paramount Plus"])

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
