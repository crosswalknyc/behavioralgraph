#!/usr/bin/env python3
"""
pandora_episode_identifier.py  —  Pandora podcast show -> every episode URL
===========================================================================
A Pandora "podcast" (PC:<id>) is a show; we enumerate its episodes (PE:<id>)
and emit ONE row per episode holding the full pandora.com watch link:

    SHOW                        URL                                              PLAT
    Baby, This is Keke Palmer   https://www.pandora.com/podcast/<show-slug>/     Pandora
    10|                                <ep-slug>/PE:<episodeId>
    ... (one row per episode) ...

How (Pandora's own web API — anonymous, no account)
---------------------------------------------------
Pandora's site is a token-gated SPA, so we bootstrap the same way the web
player does — no login, no user account:
  1. GET pandora.com            -> sets a `csrftoken` cookie.
  2. POST /api/v1/auth/anonymousLogin  (X-CsrfToken header) -> an `authToken`.
  3. POST /api/v3/sod/search    -> best PC:<id> for the show title.
    20|  4. POST /api/v1/graphql/graphql  podcast(id){ episodes(pagination){...} }
     -> paged 50-at-a-time via the returned `cursor`. Each episode carries a
     ready-made `urlPath` (…/<show>/<ep-slug>/PE:<id>) — Pandora's OWN canonical
     path, so we never have to guess a slug (Pandora even spells things its own
     way, e.g. "…a-2nd-chance-at-life"). Full URL = pandora.com + urlPath.

  • URL hint: paste any pandora.com podcast/episode link (…/PC:<id> or
    …/PE:<id>) to skip the search.

Usage
    30|-----
    python3 pandora_episode_identifier.py --title "Baby, this is Keke Palmer"
    python3 pandora_episode_identifier.py --url https://www.pandora.com/podcast/baby-this-is-keke-palmer/PC:1001055145
    python3 pandora_episode_identifier.py                     # interactive
"""

import argparse
import csv
import difflib
import gzip
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.pandora.com"
PLATFORM = "Pandora"
PAGE_LIMIT = 50           # Pandora caps GraphQL episode pagination at 50
MAX_PAGES = 60            # safety cap (=> up to 3000 episodes)

_PC = re.compile(r"(PC:\d+)")
_PE = re.compile(r"(PE:\d+)")

_EPISODES_QUERY = (
    "query($id:String!,$pg:PodcastEpisodePagination){"
    " podcast(id:$id){ name totalEpisodeCount"
    " episodes(pagination:$pg){ totalCount cursor items{ id name urlPath } } } }"
)


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


# ── anonymous Pandora session ─────────────────────────────────────────────────
class Pandora:
    """A no-login Pandora API session (csrf cookie + anonymous auth token)."""

    def __init__(self):
        self._cj = http.cookiejar.CookieJar()
        self._op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj))
        self.csrf = None
        self.auth = None

    def _open(self, req):
        with self._op.open(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw

    def _post(self, path, payload):
        data = json.dumps(payload).encode()
        headers = {"User-Agent": UA, "Content-Type": "application/json;charset=UTF-8",
                   "Accept-Encoding": "gzip, deflate"}
        if self.csrf:
            headers["X-CsrfToken"] = self.csrf
        if self.auth:
            headers["X-AuthToken"] = self.auth
        req = urllib.request.Request(BASE + path, data=data, headers=headers)
        raw = self._open(req)
        return json.loads(raw.decode("utf-8", "replace"))

    def login(self):
        """Bootstrap csrftoken cookie + anonymous auth token (idempotent)."""
        if self.auth:
            return
        try:
            self._open(urllib.request.Request(
                BASE + "/", headers={"User-Agent": UA,
                                     "Accept-Encoding": "gzip, deflate"}))
        except Exception:
            pass
        for c in self._cj:
            if c.name == "csrftoken":
                self.csrf = c.value
        j = self._post("/api/v1/auth/anonymousLogin", {})
        self.auth = j.get("authToken")

    def search_podcast(self, title):
        """(pcId, name) for the best case-insensitive podcast match."""
        self.login()
        j = self._post("/api/v3/sod/search", {
            "query": title or "", "types": ["PC"], "count": 20, "searchTime": 0})
        ann = j.get("annotations") or {}
        cands = []
        # ordered result ids first, then anything else in annotations
        for rid in (j.get("results") or []):
            pid = rid if isinstance(rid, str) else (rid or {}).get("pandoraId")
            if pid and pid.startswith("PC:"):
                cands.append(pid)
        for pid in ann:
            if pid.startswith("PC:") and pid not in cands:
                cands.append(pid)
        if not cands:
            return None, None
        best = max(cands, key=lambda p: similarity(title, (ann.get(p) or {}).get("name", "")))
        if not is_relevant(title, (ann.get(best) or {}).get("name", "")):  # floor
            return None, None
        return best, (ann.get(best) or {}).get("name")

    def podcast_name(self, pc_id):
        self.login()
        j = self._post("/api/v4/catalog/getDetails", {"pandoraId": pc_id})
        return ((j.get("annotations") or {}).get(pc_id) or {}).get("name")

    def podcast_episodes(self, pc_id):
        """(name, [(peId, title, urlPath)]) for every episode (paged)."""
        self.login()
        items, cursor, name, pages, seen = [], None, None, 0, set()
        while pages < MAX_PAGES:
            pg = {"limit": PAGE_LIMIT}
            if cursor:
                pg["cursor"] = cursor
            j = self._post("/api/v1/graphql/graphql", {
                "query": _EPISODES_QUERY, "variables": {"id": pc_id, "pg": pg}})
            pod = (j.get("data") or {}).get("podcast") or {}
            if not pod:
                break
            name = pod.get("name") or name
            eps = pod.get("episodes") or {}
            batch = eps.get("items") or []
            fresh = [e for e in batch if e.get("id") not in seen]
            for e in fresh:
                seen.add(e.get("id"))
            items.extend(fresh)
            cursor = eps.get("cursor")
            pages += 1
            if not fresh or not cursor:
                break
        out = [(e.get("id"), e.get("name", ""), e.get("urlPath", "")) for e in items]
        return name, out


def parse_url(url):
    """(pcId, peId) from a pandora.com link (either may be None)."""
    if not url:
        return None, None
    pc = _PC.search(url)
    pe = _PE.search(url)
    return (pc.group(1) if pc else None), (pe.group(1) if pe else None)


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def _rows(episodes):
    rows = []
    for _peid, etitle, path in episodes:
        if not path:
            continue
        u = BASE + path if path.startswith("/") else path
        rows.append({"season": "", "episode": "", "title": etitle or "",
                     "identifier": u, "watch_url": u})
    return rows


def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Pandora podcast to every episode's pandora.com URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:"https://www.pandora.com/podcast/<show>/<ep>/PE:<id>",
         watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (podcasts are a flat
    episode list). All anonymous — no login, no account.
    """
    pd = Pandora()

    # 1) URL hint
    if url:
        pc, pe = parse_url(url)
        if pe and kind == "movie":
            name = ""
            if pc:
                try:
                    name = pd.podcast_name(pc) or ""
                except Exception:
                    name = ""
            return (name or title or "", [{"season": "", "episode": "", "title": "",
                    "identifier": url, "watch_url": url}])
        if pc:
            name, eps = pd.podcast_episodes(pc)
            return (name or title or "", _rows(eps))

    # 2) movie by title -> single best-matching episode of the show
    if kind == "movie" and title:
        pc, name = pd.search_podcast(title)
        if not pc:
            return (title or "", [])
        nm, eps = pd.podcast_episodes(pc)
        if not eps:
            return (nm or name or title or "", [])
        best = max(eps, key=lambda e: similarity(title, e[1]))
        u = BASE + best[2] if best[2].startswith("/") else best[2]
        return (nm or name or title or "",
                [{"season": "", "episode": "", "title": best[1],
                  "identifier": u, "watch_url": u}])

    # 3) series by title -> podcast -> all episodes
    if title:
        pc, name = pd.search_podcast(title)
        if not pc:
            return (title or "", [])
        nm, eps = pd.podcast_episodes(pc)
        return (nm or name or title or "", _rows(eps))

    return (title or "", [])


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Pandora podcast -> every episode URL (anonymous web API).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="a pandora.com podcast/episode URL")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What Pandora podcast?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on Pandora. Try --url with the podcast link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "pandora").lower()).strip("-")
    out = os.path.join(args.outdir, f"pandora_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} episode(s) on Pandora")
    for r in rows[:15]:
        print(f"   {r['identifier']}   {r['title'][:50]}")
    if len(rows) > 15:
        print(f"   ... and {len(rows) - 15} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
