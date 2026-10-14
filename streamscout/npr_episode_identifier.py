#!/usr/bin/env python3
"""
npr_episode_identifier.py  —  NPR show -> every episode's npr.org play URL
==========================================================================
An NPR "show" is a podcast/program (Planet Money, Up First, Fresh Air, ...);
we enumerate its episodes and emit ONE row per episode holding the canonical
npr.org episode page — the native NPR play URL:

    SHOW          URL                                                        PLAT
    Planet Money  https://www.npr.org/2026/10/09/nx-s1-5996901/days-of-...   NPR
    ... (one row per episode) ...

How (all anonymous HTTP — no login, no API key)
-----------------------------------------------
  • Title -> feed: GET itunes.apple.com/search?media=podcast&term=<title>.
    Apple's public directory returns a `feedUrl` for every podcast; NPR shows
    carry `https://feeds.npr.org/<id>/podcast.xml`. We keep the best title match
    ONLY when its feed is an NPR feed (host ends in npr.org) — so this resolver
    stays native to NPR and never drags in a non-NPR show. (NPR's own on-site
    search returns an unranked nav list, so it is not reliable for matching;
    Apple's directory gives us the exact feed id with a real relevance score.)
  • Feed -> episodes: GET feeds.npr.org/<id>/podcast.xml and emit one row per
    <item>, using the item's <link> (the npr.org episode page) as the id.
  • URL hint: paste any of —
      feeds.npr.org/<id>/podcast.xml            (the feed itself)
      npr.org/podcasts/<id>/<slug>              (the show page)
      npr.org/<yyyy>/<mm>/<dd>/<story>/<slug>   (a single episode, --type movie)

Usage
-----
    python3 npr_episode_identifier.py --title "Planet Money"
    python3 npr_episode_identifier.py --url https://feeds.npr.org/510289/podcast.xml
    python3 npr_episode_identifier.py --url https://www.npr.org/podcasts/510318/up-first
    python3 npr_episode_identifier.py                     # interactive
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
import xml.etree.ElementTree as ET
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ITUNES_SEARCH = "https://itunes.apple.com/search"
FEED_URL = "https://feeds.npr.org/%s/podcast.xml"
PLATFORM = "NPR"
COUNTRY = "US"

_FEED_ID = re.compile(r"feeds\.npr\.org/(\d+)/")          # feeds.npr.org/510289/
_PODCAST_PAGE = re.compile(r"npr\.org/podcasts/(\d+)")    # /podcasts/510318/up-first
_EPISODE_PAGE = re.compile(r"npr\.org/\d{4}/\d{2}/\d{2}/") # /2026/10/09/<story>/<slug>


# ── tiny helpers ──────────────────────────────────────────────────────────────
try:
    from match_gate import is_relevant           # shared over-match relevance floor
except ImportError:                              # keep the sibling importable
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from match_gate import is_relevant


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


def get_text(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
    except Exception:
        return ""


def _is_npr_feed(feed_url):
    """True when feed_url is hosted on npr.org (feeds.npr.org/...)."""
    try:
        host = urllib.parse.urlparse(feed_url or "").hostname or ""
    except Exception:
        return False
    return host == "npr.org" or host.endswith(".npr.org")


# ── discovery ─────────────────────────────────────────────────────────────────
def find_feed(title):
    """(feed_url, show_name) for the best NPR podcast match, or (None, None).

    Scans Apple's podcast directory for the title, keeps only results whose
    feedUrl is an NPR feed, and returns the most title-similar one that also
    clears the shared relevance floor."""
    j = get_json(ITUNES_SEARCH, {"media": "podcast", "term": title,
                                 "country": COUNTRY, "limit": 25})
    npr_results = [r for r in (j.get("results") or [])
                   if _is_npr_feed(r.get("feedUrl"))]
    if not npr_results:
        return None, None
    best = max(npr_results,
               key=lambda r: similarity(title, r.get("collectionName", "")))
    if not is_relevant(title, best.get("collectionName", "")):   # relevance floor
        return None, None
    return best.get("feedUrl"), best.get("collectionName")


# ── feed enumeration ──────────────────────────────────────────────────────────
def _items_via_et(xml_text):
    """[(link, title)] per <item>, parsed with ElementTree. [] on parse error."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    channel = root.find("channel")
    if channel is None:
        return None
    out = []
    for item in channel.findall("item"):
        link_el = item.find("link")
        title_el = item.find("title")
        link = (link_el.text or "").strip() if link_el is not None else ""
        etitle = (title_el.text or "").strip() if title_el is not None else ""
        if link:
            out.append((link, etitle))
    return out


def _items_via_regex(xml_text):
    """Fallback item parse: split on <item>…</item>, read <link> inside each."""
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S):
        lm = re.search(r"<link>(.*?)</link>", block, re.S)
        if not lm:
            continue
        link = lm.group(1).strip()
        tm = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                       block, re.S)
        etitle = (tm.group(1).strip() if tm else "")
        if link:
            out.append((link, etitle))
    return out


def _channel_name(xml_text):
    m = re.search(r"<channel>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                  xml_text, re.S)
    return (m.group(1).strip() if m else "")


def feed_episodes(feed_url):
    """(show_name, [(link, title)]) for an NPR podcast feed (de-duped, in order)."""
    xml_text = get_text(feed_url)
    if not xml_text:
        return "", []
    name = _channel_name(xml_text)
    items = _items_via_et(xml_text)
    if items is None:                       # ET failed -> regex fallback
        items = _items_via_regex(xml_text)
    seen, eps = set(), []
    for link, etitle in items:
        if link in seen:
            continue
        seen.add(link)
        eps.append((link, etitle))
    return name, eps


def parse_url(url):
    """(kind, value) from a pasted NPR URL:
       ('feed', feed_url)    for a feeds.npr.org feed or an npr.org/podcasts page
       ('episode', url)      for a single npr.org/<date>/<story>/<slug> page
       (None, None)          otherwise."""
    if not url:
        return None, None
    m = _FEED_ID.search(url)
    if m:
        return "feed", FEED_URL % m.group(1)
    m = _PODCAST_PAGE.search(url)
    if m:
        return "feed", FEED_URL % m.group(1)
    if _EPISODE_PAGE.search(url):
        return "episode", url
    return None, None


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def _rows(episodes):
    rows = []
    for link, etitle in episodes:
        rows.append({"season": "", "episode": "", "title": etitle or "",
                     "identifier": link, "watch_url": link})
    return rows


def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve an NPR show to every episode's npr.org play URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:"https://www.npr.org/<yyyy>/<mm>/<dd>/<story>/<slug>",
         watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (podcasts are a flat
    episode list). All anonymous — no login, no API key.
    """
    # 1) URL hint
    if url:
        ukind, val = parse_url(url)
        if ukind == "episode":
            if kind == "movie":
                return (title or "", [{"season": "", "episode": "", "title": "",
                        "identifier": val, "watch_url": val}])
            # a series run from an episode link: fall through to needing a feed
        elif ukind == "feed":
            name, eps = feed_episodes(val)
            return (name or title or "", _rows(eps))

    # 2) title -> NPR feed
    if title:
        feed_url, name = find_feed(title)
        if not feed_url:
            return (title or "", [])
        nm, eps = feed_episodes(feed_url)
        show = nm or name or title or ""
        if kind == "movie":                 # single best-matching episode
            if not eps:
                return (show, [])
            best = max(eps, key=lambda e: similarity(title, e[1]))
            return (show, _rows([best]))
        return (show, _rows(eps))

    return (title or "", [])


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="NPR show -> every episode's npr.org play URL (no login).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="a feeds.npr.org feed, npr.org/podcasts show "
                                  "page, or npr.org episode URL")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What NPR show?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on NPR. Try --url with the feeds.npr.org feed or "
              "the npr.org/podcasts show link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "npr").lower()).strip("-")
    out = os.path.join(args.outdir, f"npr_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} episode(s) on NPR")
    for r in rows[:15]:
        print(f"   {r['identifier']}   {r['title'][:60]}")
    if len(rows) > 15:
        print(f"   ... and {len(rows) - 15} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
