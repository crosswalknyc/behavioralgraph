#!/usr/bin/env python3
"""
amazon_episode_identifier.py  —  Amazon / Prime Video title -> id fragments
==================================================================================
Amazon lists a title under MANY URLs (amazon.com vs primevideo.com, "open" vs
"share", web vs app), but they all reduce to a couple of per-title ids. We store
the "portion we actually need" as normalized fragments — 3 forms per title:

  * detail/<ASIN>            e.g. detail/B09NF4J7XW   (amazon.com side)
  * detail/<GTI-26char>      e.g. detail/0KAW4T6OO... (primevideo.com side)
  * amzn1.dv.gti.<uuid>      the internal GTI encoding

(--all-asins additionally emits every offer ASIN: SD/HD/UHD/ad SKUs.)

How (no login): Amazon's public /gp/video/detail/<seasonASIN> page embeds a
`self` map that pairs every title on the page — the season shell AND each
episode — with its ASIN(s), compactGTI (the 26-char GTI), a uuid gti, a titleType
("season"/"episode") and a sequenceNumber (season # or episode #). A
seasonSelector block lists every season's ASIN, so we fetch one page per season
and emit the fragments for the season shell and for each episode.

Discovery: title -> a season ASIN via Amazon search (/s?i=instant-video) or a web
search that surfaces a primevideo GTI (whose page yields an ASIN). Pasting any
amazon.com/primevideo.com detail URL always works.

Usage
-----
  python3 amazon_episode_identifier.py --title "The Summer I Turned Pretty" --seasons 1,2
  python3 amazon_episode_identifier.py --url https://www.amazon.com/gp/video/detail/B09NF4J7XW
  python3 amazon_episode_identifier.py                # interactive
"""

import argparse
import csv
import gzip
import re
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PLATFORM = "Amazon"
ASIN_RE = r"B0[A-Z0-9]{8}"
GTI_RE = r"[A-Z0-9]{26}"

# one entry in the page's `self` map: ASIN -> {asins[], compactGTI, gti, seq, type}
_ENTRY = re.compile(
    r'"(' + ASIN_RE + r')":\{"asins":\[([^\]]*)\],'
    r'"compactGTI":"(' + GTI_RE + r')",'
    r'"gti":"(amzn1\.dv\.gti\.[0-9a-f-]{36})"'
    r'[^}]*?"sequenceNumber":(\d+),"titleType":"(season|episode)"')
# season selector: season number -> season-shell ASIN
_SEASONSEL = re.compile(
    r'detail/(' + ASIN_RE + r')[^"\']*?season_select_s(\d+)')
_OG_TITLE = re.compile(
    r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I)
# primevideo.com pages expose the current season-shell ASIN here
_PAGETYPEID = re.compile(r"pageTypeId=['\"]?(" + ASIN_RE + r")")

# The scattered URL forms all reduce to these id fragments (what we store):
#   detail/<ASIN>            amazon.com/gp/video/detail/<ASIN>  (one per offer)
#   detail/<GTI-26char>      primevideo.com/detail/<GTI>
#   amzn1.dv.gti.<uuid>      the internal GTI encoding

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


# ── parsing ───────────────────────────────────────────────────────────────────
def show_name(html, fallback=""):
    m = _OG_TITLE.search(html or "")
    if not m:
        tm = re.search(r"<title>([^<]+)</title>", html or "")
        t = tm.group(1) if tm else ""
    else:
        t = m.group(1)
    t = re.sub(r"^\s*watch\s+", "", t, flags=re.I)
    t = re.sub(r"\s*[-–—|:]\s*season\s+\d+.*$", "", t, flags=re.I)
    t = re.sub(r"\s*[-–—|]\s*prime\s+video.*$", "", t, flags=re.I)
    t = re.sub(r"^\s*prime\s+video\s*:\s*", "", t, flags=re.I)
    return t.strip() or fallback


def self_entries(html):
    """All entries from the page's `self` map, one per title (season/episode):
        {asin, asins[], gti(=compactGTI), uuid, seq, type}"""
    out, seen = [], set()
    for m in _ENTRY.finditer(html or ""):
        asin = m.group(1)
        if asin in seen:
            continue
        seen.add(asin)
        asins = re.findall(ASIN_RE, m.group(2)) or [asin]
        out.append({"asin": asin, "asins": asins, "gti": m.group(3),
                    "uuid": m.group(4), "seq": int(m.group(5)),
                    "type": m.group(6)})
    return out


def entity_fragments(ent, all_asins=True):
    """Normalized id fragments for one entity, in the order:
       detail/<ASIN>...  detail/<GTI-26>  amzn1.dv.gti.<uuid>"""
    asins = ent["asins"] if all_asins else [ent["asin"]]
    seen, frags = set(), []
    for a in asins:
        if a not in seen:
            seen.add(a)
            frags.append("detail/%s" % a)
    frags.append("detail/%s" % ent["gti"])
    frags.append(ent["uuid"])
    return frags


def season_asins(html):
    """{season_number: season_shell_ASIN} from the season selector."""
    out = {}
    for asin, s in _SEASONSEL.findall(html or ""):
        out[int(s)] = asin
    return out


def asin_from_pv(html):
    """A season-shell ASIN from a primevideo.com page (DVWebNode.pageTypeId)."""
    sa = season_asins(html)
    if sa:
        return sa[min(sa)]
    m = _PAGETYPEID.search(html or "")
    return m.group(1) if m else None


# ── discovery (title -> a season ASIN) ────────────────────────────────────────
def _asin_from_url(url):
    m = re.search(r"/(?:gp/video/detail|dp)/(" + ASIN_RE + r")", url or "")
    return m.group(1) if m else None


def _gti_from_url(url):
    m = re.search(r"/detail/(" + GTI_RE + r")", url or "")
    return m.group(1) if m else None


def _web_search(query):
    """Return raw HTML from a DuckDuckGo HTML endpoint (best effort)."""
    for engine in ("https://html.duckduckgo.com/html/",
                   "https://lite.duckduckgo.com/lite/"):
        code, _, html = fetch(engine + "?" +
                              urllib.parse.urlencode({"q": query}))
        if code == 200 and html:
            return urllib.parse.unquote(html)
    return ""


def discover_asin(title):
    """Return a season ASIN for a title (or None), no login."""
    want = set(tokens(title))
    # A) Amazon instant-video search — match a detail link near matching text.
    code, _, html = fetch("https://www.amazon.com/s?" +
                          urllib.parse.urlencode({"k": title, "i": "instant-video"}))
    if code == 200 and html and len(html) > 20000:
        for m in re.finditer(r'/gp/video/detail/(' + ASIN_RE + r')', html):
            ctx = html[max(0, m.start() - 500):m.start() + 200].lower()
            if want and len(want & set(tokens(ctx))) >= max(2, len(want) - 1):
                return m.group(1)
    # B) Web search — surfaces an amazon ASIN or (usually) a primevideo GTI.
    for q in (f"{title} prime video", f"{title} primevideo detail",
              f"{title} amazon prime video watch"):
        blob = _web_search(q)
        if not blob:
            continue
        am = re.search(r'amazon\.com/gp/video/detail/(' + ASIN_RE + r')', blob)
        if am:
            return am.group(1)
        gm = re.search(r'primevideo\.com/(?:region/na/)?detail/(' + GTI_RE + r')', blob)
        if gm:  # GTI -> primevideo page -> season-shell ASIN
            _, _, pv = fetch("https://www.primevideo.com/detail/" + gm.group(1) + "/")
            a = asin_from_pv(pv)
            if a:
                return a
    return None


# ── reusable resolver ─────────────────────────────────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None, all_asins=False):
    """Return (show_name, rows). Each row is one normalized id fragment:
        {season, episode, title, identifier, watch_url}
    identifier is 'detail/<ASIN>', 'detail/<GTI-26>' or 'amzn1.dv.gti.<uuid>'.
    episode == "" marks the season shell."""
    entry_asin = _asin_from_url(url) if url else None
    show = title or ""
    if not entry_asin and url:
        gti = _gti_from_url(url)
        if gti:
            _, _, pv = fetch("https://www.primevideo.com/detail/%s/" % gti)
            entry_asin = asin_from_pv(pv)
    if not entry_asin and title:
        entry_asin = discover_asin(title)
    if not entry_asin:
        return (show, [])

    # entry page -> season->ASIN map (+ ensure the entry season is included)
    _, _, html = fetch("https://www.amazon.com/gp/video/detail/%s/" % entry_asin)
    show = show_name(html, title or "")
    smap = season_asins(html)
    for e in self_entries(html):        # the entry page's own season shell
        if e["type"] == "season":
            smap.setdefault(e["seq"], e["asin"])
    if not smap:                        # single-season fallback
        smap = {1: entry_asin}

    def _emit(rows, season, episode, ent):
        for frag in entity_fragments(ent, all_asins=all_asins):
            rows.append({"season": str(season), "episode": episode,
                         "title": "", "identifier": frag, "watch_url": ""})

    rows = []
    for n in sorted(smap):
        if seasons and n not in seasons:
            continue
        _, _, shtml = fetch("https://www.amazon.com/gp/video/detail/%s/" % smap[n])
        ents = self_entries(shtml)
        shell = next((e for e in ents if e["type"] == "season" and e["seq"] == n),
                     next((e for e in ents if e["type"] == "season"), None))
        if shell:
            _emit(rows, n, "", shell)
        for e in sorted((e for e in ents if e["type"] == "episode"),
                        key=lambda x: x["seq"]):
            _emit(rows, n, str(e["seq"]), e)
    return (show, rows)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Prime Video title -> the 4 canonical URL forms (no login).")
    ap.add_argument("--title")
    ap.add_argument("--seasons", help="e.g. 1 | 1,3 | 1-4 | all")
    ap.add_argument("--url", help="an amazon.com or primevideo.com detail URL")
    ap.add_argument("--production", default="", help="PRODUCTION column value")
    ap.add_argument("--all-asins", action="store_true",
                    help="include every offer ASIN (SD/HD/UHD/ad SKUs), not just "
                         "the primary one")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    title = args.title
    seasons = parse_seasons(args.seasons) if args.seasons is not None else None
    if not args.url and not title:
        title = ask("What title?: ")
    if args.seasons is None and not args.url:
        seasons = parse_seasons(ask("Which season(s)?  1 | 1,3 | 1-4 | all: "))
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, seasons=seasons,
                             all_asins=args.all_asins)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Fetch/parse failed: {e!r}"); return 2
    if not rows:
        print("  ! Nothing found. Pass --url with an amazon.com/primevideo.com "
              "detail link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "amazon").lower()).strip("-")
    out = os.path.join(args.outdir, f"amazon_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], args.production, PLATFORM,
                        f"Season {r['season']}"])

    n_items = len({(r["season"], r["episode"]) for r in rows})
    print(f"\nWrote {len(rows)} id fragments ({n_items} titles) for {show!r}.")
    for r in rows[:6]:
        ep = f"S{r['season']}E{r['episode']}" if r["episode"] else f"S{r['season']} shell"
        print(f"   {ep:12s} {r['identifier']}")
    if len(rows) > 6:
        print(f"   ... and {len(rows) - 6} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
