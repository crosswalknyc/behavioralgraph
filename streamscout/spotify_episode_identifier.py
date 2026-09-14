#!/usr/bin/env python3
"""
spotify_episode_identifier.py  —  Spotify podcast -> every episode URL
======================================================================
A Spotify "show" is a podcast; we enumerate ALL of its episodes and emit ONE
row per episode holding the full open.spotify.com link:

    SHOW                        URL                                        PLAT
    Baby, This is Keke Palmer   https://open.spotify.com/episode/<id>      Spotify
    ... (one row per episode) ...

Case-insensitivity (important)
------------------------------
Spotify is inconsistent about the CASE of the alpha characters it hands back
(show names, and the letters inside base-62 ids can arrive upper- or lower-cased
in clickstream). So this tool never relies on exact case:
  • Title search / matching is fully case-folded (we tokenise lower-case).
  • Pasted open.spotify.com / spotify: links are parsed case-insensitively.
  • A pasted id is matched to Spotify's catalog case-insensitively, and we always
    emit Spotify's OWN canonical id for each episode (so downstream case-folding
    lines them up no matter how clickstream cased them).

How (official Spotify Web API — no browser, no user login)
----------------------------------------------------------
Spotify's public web player is a token-gated SPA (nothing to scrape), so we use
the official Web API with the **Client Credentials** flow — an app id + secret,
NOT a personal login. Put these in the gitignored `.env.local` at the repo root:

    SPOTIFY_CLIENT_ID=xxxxxxxx
    SPOTIFY_CLIENT_SECRET=xxxxxxxx

Create them in ~2 min (free): https://developer.spotify.com/dashboard →
"Create app" (any name; redirect URI can be http://localhost) → copy the
Client ID and Client secret. Client-credentials tokens can read the public
catalog (search + a show's episodes); that's all we need.

  • Title -> show: GET /v1/search?type=show  (best case-insensitive match)
  • Show -> episodes: page GET /v1/shows/<id>/episodes?market=US&limit=50 until
    done — that returns EVERY episode.
  • URL hint: paste any open.spotify.com/show|episode link (or spotify:show:..)
    to skip the search.

Usage
-----
    python3 spotify_episode_identifier.py --title "Baby, This is Keke Palmer"
    python3 spotify_episode_identifier.py --url https://open.spotify.com/show/0kK48l715AImMNbutfHYYG
    python3 spotify_episode_identifier.py                     # interactive
"""

import argparse
import base64
import csv
import difflib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
PLATFORM = "Spotify"
MARKET = "US"
EPISODE_URL = "https://open.spotify.com/episode/%s"

# open.spotify.com/<type>/<id> or spotify:<type>:<id> — parsed case-INSENSITIVELY
# for the scheme/host/type; the base-62 id keeps its original case.
_URL_ID = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?|spotify:)"
    r"(show|episode)[:/]([0-9A-Za-z]{22})", re.I)


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


def load_env(path=".env.local"):
    env = {}
    for p in (path, os.path.join(os.path.dirname(__file__), path),
              os.path.join(os.path.dirname(os.path.dirname(__file__)), path)):
        if os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    return env


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _request(url, data=None, headers=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception:
        return None, {}


def get_token(env=None):
    """A Client-Credentials access token, or None (with a printed hint)."""
    env = env or load_env()
    cid = env.get("SPOTIFY_CLIENT_ID")
    secret = env.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret:
        return None
    basic = base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    code, j = _request(TOKEN_URL, data=body, headers={
        "User-Agent": UA,
        "Authorization": "Basic %s" % basic,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    return j.get("access_token")


def api_get(path, token, **params):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = API + path + q
    _, j = _request(url, headers={"User-Agent": UA,
                                  "Authorization": "Bearer %s" % token})
    return j


# ── discovery + enumeration ───────────────────────────────────────────────────
def find_show(name, token):
    """(show_id, show_name) for the best case-insensitive show match, or (None,None)."""
    j = api_get("/search", token, q=name, type="show", market=MARKET, limit=20)
    items = ((j.get("shows") or {}).get("items")) or []
    items = [it for it in items if it and it.get("id")]
    if not items:
        return None, None
    best = max(items, key=lambda it: similarity(name, it.get("name", "")))
    return best.get("id"), best.get("name")


def show_name(show_id, token):
    j = api_get("/shows/%s" % show_id, token, market=MARKET)
    return j.get("name") or ""


def show_episodes(show_id, token, limit=50):
    """Every episode of a show as [(episode_id, name)], in Spotify's order."""
    out, offset = [], 0
    while True:
        j = api_get("/shows/%s/episodes" % show_id, token,
                    market=MARKET, limit=limit, offset=offset)
        items = j.get("items")
        if items is None:                     # error / bad token
            break
        got = 0
        for it in items:
            if it and it.get("id"):
                out.append((it["id"], it.get("name", "")))
                got += 1
        offset += len(items)
        if not j.get("next") or got == 0 and not items:
            break
        if offset >= (j.get("total") or offset):
            break
    return out


def parse_url(url):
    """(kind, id) from a pasted Spotify link/URI, case-insensitively. kind is
    'show' | 'episode'. The base-62 id is returned unchanged."""
    m = _URL_ID.search(url or "")
    if m:
        return m.group(1).lower(), m.group(2)
    return None, None


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def _rows(episodes):
    rows = []
    for eid, name in episodes:
        u = EPISODE_URL % eid
        rows.append({"season": "", "episode": "", "title": name or "",
                     "identifier": u, "watch_url": u})
    return rows


def resolve(title=None, url=None, kind="series", seasons=None):
    """Resolve a Spotify podcast to every episode's open.spotify.com URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<episode name>,
         identifier:"https://open.spotify.com/episode/<id>", watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (podcasts are a flat
    episode list). Needs SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in .env.local.
    """
    token = get_token()
    if not token:
        raise RuntimeError(
            "Spotify needs SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in "
            ".env.local — create a free app at "
            "https://developer.spotify.com/dashboard (Client Credentials).")

    # 1) URL hint (case-insensitive)
    if url:
        ukind, sid = parse_url(url)
        if ukind == "episode":
            nm = ""
            j = api_get("/episodes/%s" % sid, token, market=MARKET)
            nm = j.get("name", "")
            if kind == "movie":
                u = EPISODE_URL % sid
                return (title or nm or "", [{"season": "", "episode": "",
                        "title": nm, "identifier": u, "watch_url": u}])
            # an episode link for a series -> enumerate its show
            sid = ((j.get("show") or {}).get("id")) or None
            if not sid:
                u = EPISODE_URL % sid
                return (title or nm, [])
            ukind = "show"
        if ukind == "show":
            name = show_name(sid, token) or title or ""
            return (name, _rows(show_episodes(sid, token)))

    # 2) movie by title -> single best-matching episode
    if kind == "movie" and title:
        j = api_get("/search", token, q=title, type="episode",
                    market=MARKET, limit=20)
        items = ((j.get("episodes") or {}).get("items")) or []
        items = [it for it in items if it and it.get("id")]
        if not items:
            return (title, [])
        best = max(items, key=lambda it: similarity(title, it.get("name", "")))
        u = EPISODE_URL % best["id"]
        return (title, [{"season": "", "episode": "", "title": best.get("name", ""),
                         "identifier": u, "watch_url": u}])

    # 3) series by title -> show -> all episodes
    if title:
        sid, name = find_show(title, token)
        if not sid:
            return (title or "", [])
        return (name or title or "", _rows(show_episodes(sid, token)))

    return (title or "", [])


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Spotify podcast -> every episode URL (Web API).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="an open.spotify.com show/episode URL (skips search)")
    ap.add_argument("--seasons", help="(accepted but ignored; flat episode list)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What Spotify podcast / show?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {e}"); return 2
    if not rows:
        print("  ! Not found on Spotify. Try --url with the show link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "spotify").lower()).strip("-")
    out = os.path.join(args.outdir, f"spotify_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} episode(s) on Spotify")
    for r in rows[:15]:
        print(f"   {r['identifier']}   {r['title'][:70]}")
    if len(rows) > 15:
        print(f"   ... and {len(rows) - 15} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
