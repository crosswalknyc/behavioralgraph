#!/usr/bin/env python3
"""
youtube_episode_identifier.py  —  YouTube show -> every episode watch URL (no login)
====================================================================================
A YouTube "show" is a CHANNEL (e.g. "Baby, This is Keke Palmer", a Wondery
video podcast).  We enumerate the channel's **Videos** tab — the long-form
uploads — and emit ONE row per episode holding the full watch URL:

    SHOW                        URL                                    PLAT
    Baby, This is Keke Palmer   https://www.youtube.com/watch?v=<id>   YouTube
    ... (one row per episode) ...

Why the Videos tab (and not "all uploads")
------------------------------------------
A channel's raw uploads mix three things: long-form **videos** (the episodes),
**Shorts** (vertical clips/teasers), and **live** streams — each on its own
tab.  "All episodes" means the long-form Videos tab; Shorts are promo clips,
not episodes, so they're excluded by default.  Pass --include-shorts (or
include_shorts=True) to also fold in every Short via the uploads playlist.

How (all anonymous HTTP, no login, no API key)
----------------------------------------------
  • Title -> channel: YouTube search (youtube.com/results) — its ytInitialData
    lists channelRenderer results; we pick the best title match.
  • Channel -> episodes: load /channel/<id>/videos, read ytInitialData for the
    first page of lockupViewModel items, then page through the rest with
    YouTube's own InnerTube continuation endpoint (youtubei/v1/browse) using the
    public key + client version embedded in the page.  This is exactly what the
    site itself calls when you scroll, so it returns every video reliably.
  • URL hint: paste any youtube.com URL to skip the search —
      /channel/UC... , /@handle , /c/... , /user/... , watch?v=... (its owning
      channel is enumerated), or playlist?list=... (that playlist is emitted).

Usage
-----
    python3 youtube_episode_identifier.py --title "Baby, This is Keke Palmer"
    python3 youtube_episode_identifier.py --url https://www.youtube.com/@BabyThisIsKekePalmer
    python3 youtube_episode_identifier.py --url "https://www.youtube.com/playlist?list=PL..."
    python3 youtube_episode_identifier.py --title "Some Show" --include-shorts
    python3 youtube_episode_identifier.py                     # interactive
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
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.youtube.com"
PLATFORM = "YouTube"
WATCH = "https://www.youtube.com/watch?v=%s"

# ── tiny helpers ──────────────────────────────────────────────────────────────
def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def similarity(a, b):
    """0..1 title similarity, robust to word order/typos (subset => 1.0)."""
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


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
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


def post_json(url, body, timeout=25):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


# ── ytInitialData / ytcfg extraction ──────────────────────────────────────────
def _balanced(html, start_idx):
    """Return the JSON object beginning at the first '{' at/after start_idx."""
    s = html.index("{", start_idx)
    depth, i, instr, esc = 0, s, False, False
    while i < len(html):
        c = html[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return html[s:i + 1]
        i += 1
    return ""


def yt_initial_data(html):
    m = re.search(r'ytInitialData\s*=\s*', html)
    if not m:
        return {}
    try:
        return json.loads(_balanced(html, m.end() - 1))
    except Exception:
        return {}


def ytcfg(html):
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    ver = (re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', html)
           or re.search(r'"clientVersion":"([\d.]+)"', html))
    return (key.group(1) if key else None,
            ver.group(1) if ver else "2.20240101.00.00")


# ── walkers over the InnerTube tree ───────────────────────────────────────────
def _lockup_title(lockup):
    """Best-effort video title from a lockupViewModel."""
    md = lockup.get("metadata", {})
    out = []

    def w(o):
        if isinstance(o, dict):
            c = o.get("content")
            if isinstance(c, str) and len(c) > 2:
                out.append(c)
            for v in o.values():
                w(v)
        elif isinstance(o, list):
            for v in o:
                w(v)
    w(md)
    return out[0] if out else ""


def collect_videos(obj, out, seen):
    """Append (videoId, title) for every LOCKUP_CONTENT_TYPE_VIDEO, in order.

    Also handles the older videoRenderer/gridVideoRenderer shapes as a fallback.
    """
    if isinstance(obj, dict):
        if (obj.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO"
                and obj.get("contentId")):
            vid = obj["contentId"]
            if vid not in seen:
                seen.add(vid)
                out.append((vid, _lockup_title(obj)))
        for key in ("videoRenderer", "gridVideoRenderer",
                    "playlistVideoRenderer"):
            r = obj.get(key)
            if isinstance(r, dict) and r.get("videoId"):
                vid = r["videoId"]
                if vid not in seen:
                    seen.add(vid)
                    title = ""
                    t = r.get("title", {})
                    if isinstance(t, dict):
                        title = (t.get("simpleText")
                                 or "".join(run.get("text", "")
                                            for run in t.get("runs", [])))
                    out.append((vid, title))
        for v in obj.values():
            collect_videos(v, out, seen)
    elif isinstance(obj, list):
        for v in obj:
            collect_videos(v, out, seen)


def continuation_token(obj):
    """Next-page token; handles both the new ViewModel and old Renderer shapes."""
    if isinstance(obj, dict):
        civm = obj.get("continuationItemViewModel")
        if isinstance(civm, dict):
            try:
                return (civm["continuationCommand"]["innertubeCommand"]
                        ["continuationCommand"]["token"])
            except Exception:
                pass
        cir = obj.get("continuationItemRenderer")
        if isinstance(cir, dict):
            try:
                return (cir["continuationEndpoint"]
                        ["continuationCommand"]["token"])
            except Exception:
                pass
        for v in obj.values():
            t = continuation_token(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = continuation_token(v)
            if t:
                return t
    return None


def _enumerate(page_url, max_pages=200):
    """Enumerate every (videoId, title) reachable from a channel-tab or playlist
    page, following InnerTube continuations. Returns an ordered, de-duped list."""
    _, _, html = fetch(page_url)
    if not html:
        return []
    apikey, cver = ytcfg(html)
    data = yt_initial_data(html)
    out, seen = [], set()
    collect_videos(data, out, seen)
    tok = continuation_token(data)
    if not apikey:
        return out
    ctx = {"client": {"clientName": "WEB", "clientVersion": cver,
                      "hl": "en", "gl": "US"}}
    browse = "%s/youtubei/v1/browse?key=%s" % (BASE, apikey)
    pages = 0
    while tok and pages < max_pages:
        j = post_json(browse, {"context": ctx, "continuation": tok})
        if not j:
            break
        before = len(out)
        collect_videos(j, out, seen)
        tok = continuation_token(j)
        pages += 1
        if len(out) == before and not tok:
            break
    return out


# ── channel / playlist identity ───────────────────────────────────────────────
_CHANNEL_ID = re.compile(r"(UC[0-9A-Za-z_-]{22})")
_LIST_ID = re.compile(r"[?&]list=([0-9A-Za-z_-]+)")
_VIDEO_ID = re.compile(r"(?:v=|/shorts/|youtu\.be/)([0-9A-Za-z_-]{11})")


def channel_name(cid_or_html):
    """Channel display name from a channel id (fetches) or a page's HTML."""
    html = cid_or_html
    if _CHANNEL_ID.fullmatch(cid_or_html or ""):
        _, _, html = fetch("%s/channel/%s/videos?hl=en&gl=US"
                           % (BASE, cid_or_html))
    m = (re.search(r'"channelMetadataRenderer":\{"title":"([^"]+)"', html or "")
         or re.search(r'<meta property="og:title" content="([^"]+)"', html or "")
         or re.search(r'"author":"([^"]+)"', html or ""))
    return (m.group(1).encode().decode("unicode_escape") if m else "").strip()


def channel_from_url(url):
    """Resolve a pasted youtube.com URL to (kind, id) where kind is
    'channel' | 'playlist' | 'video'. Follows @handle / vanity / watch pages to
    their owning channel id."""
    if not url:
        return None, None
    m = _LIST_ID.search(url)
    if m and not m.group(1).startswith(("RD", "UL")):   # skip mix/radio ids
        return "playlist", m.group(1)
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", url)
    if m:
        return "channel", m.group(1)
    # @handle, /c/<name>, /user/<name>, watch?v=... -> fetch and read channelId
    _, _, html = fetch(url if url.startswith("http") else "https://" + url)
    m = (re.search(r'"channelId":"(UC[0-9A-Za-z_-]{22})"', html or "")
         or re.search(r'"externalId":"(UC[0-9A-Za-z_-]{22})"', html or "")
         or _CHANNEL_ID.search(html or ""))
    if m:
        return "channel", m.group(1)
    m = _VIDEO_ID.search(url)
    if m:
        return "video", m.group(1)
    return None, None


def discover_channel(title):
    """(channelId, name) for the best-matching channel from YouTube search."""
    q = urllib.parse.quote(title)
    _, _, html = fetch("%s/results?search_query=%s&sp=EgIQAg%%253D%%253D&hl=en&gl=US"
                       % (BASE, q))          # sp=...EQIQAg = filter to channels
    data = yt_initial_data(html)
    cand = []

    def w(o):
        if isinstance(o, dict):
            cr = o.get("channelRenderer")
            if isinstance(cr, dict) and cr.get("channelId"):
                nm = (cr.get("title", {}) or {}).get("simpleText", "")
                cand.append((cr["channelId"], nm))
            for v in o.values():
                w(v)
        elif isinstance(o, list):
            for v in o:
                w(v)
    w(data)
    if not cand:                              # fall back to unfiltered search
        _, _, html = fetch("%s/results?search_query=%s&hl=en&gl=US" % (BASE, q))
        w(yt_initial_data(html))
    if not cand:
        return None, None
    best = max(cand, key=lambda c: similarity(title, c[1]))
    return best


# ── reusable resolver (imported by streamscout.py) ────────────────────────────
def _rows_from_videos(videos):
    rows = []
    for vid, vtitle in videos:
        u = WATCH % vid
        rows.append({"season": "", "episode": "", "title": vtitle or "",
                     "identifier": u, "watch_url": u})
    return rows


def resolve(title=None, url=None, kind="series", seasons=None,
            include_shorts=False):
    """Resolve a YouTube show to every episode's watch URL.

    Returns (show_name, rows); each row is
        {season:"", episode:"", title:<video title>,
         identifier:"https://www.youtube.com/watch?v=<id>", watch_url:<same>}
    `seasons` is accepted for interface parity but ignored (YouTube channels
    have no season structure). Set include_shorts=True to also emit Shorts.
    Paste any youtube.com URL via `url` to skip the search.
    """
    cid = None
    show = title or ""

    # 1) URL hint
    if url:
        ukind, uid = channel_from_url(url)
        if ukind == "playlist":
            vids = _enumerate("%s/playlist?list=%s&hl=en&gl=US" % (BASE, uid))
            name = title or ""
            return (name or "YouTube playlist", _rows_from_videos(vids))
        if ukind == "video" and kind == "movie":
            u = WATCH % uid
            return (title or "", [{"season": "", "episode": "", "title": "",
                                   "identifier": u, "watch_url": u}])
        if ukind in ("channel", "video"):
            if ukind == "video":                # a watch link -> its channel
                _, _, wh = fetch(WATCH % uid + "&hl=en&gl=US")
                m = re.search(r'"channelId":"(UC[0-9A-Za-z_-]{22})"', wh or "")
                cid = m.group(1) if m else None
                show = show or channel_name(wh)
            else:
                cid = uid

    # 2) movie by title -> single best-matching video
    if not cid and kind == "movie" and title:
        q = urllib.parse.quote(title)
        _, _, html = fetch("%s/results?search_query=%s&hl=en&gl=US" % (BASE, q))
        vids, seen = [], set()
        collect_videos(yt_initial_data(html), vids, seen)
        if not vids:
            return (title, [])
        best = max(vids, key=lambda v: similarity(title, v[1]))
        u = WATCH % best[0]
        return (title, [{"season": "", "episode": "", "title": best[1],
                         "identifier": u, "watch_url": u}])

    # 3) series by title -> channel
    if not cid and title:
        cid, nm = discover_channel(title)
        if nm:
            show = nm
    if not cid:
        return (title or "", [])

    if not show or show == title:
        show = channel_name(cid) or show or title or ""

    # long-form episodes (Videos tab)
    videos = _enumerate("%s/channel/%s/videos?hl=en&gl=US" % (BASE, cid))
    if include_shorts:                          # fold in every upload (Shorts too)
        uploads = "UU" + cid[2:]
        extra = _enumerate("%s/playlist?list=%s&hl=en&gl=US" % (BASE, uploads))
        have = {v for v, _ in videos}
        videos += [(v, t) for v, t in extra if v not in have]

    return (show or title or "", _rows_from_videos(videos))


# ── main (standalone CLI) ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="YouTube show -> every episode watch URL (no login).")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="a youtube.com channel / @handle / playlist / "
                                  "watch URL (skips search)")
    ap.add_argument("--include-shorts", action="store_true",
                    help="also include Shorts (default: long-form episodes only)")
    ap.add_argument("--seasons", help="(accepted but ignored; one flat list)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    kind, title = args.type, args.title
    if not args.url and not title:
        title = ask("What YouTube show / channel?: ")
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=kind,
                             include_shorts=args.include_shorts)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Search/fetch failed: {e!r}"); return 2
    if not rows:
        print("  ! Not found on YouTube. Try --url with the channel link.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "youtube").lower()).strip("-")
    out = os.path.join(args.outdir, f"youtube_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([show, r["identifier"], PLATFORM, ""])

    print(f"\n{show}  ->  {len(rows)} video(s) on YouTube")
    for r in rows[:15]:
        print(f"   {r['identifier']}   {r['title'][:70]}")
    if len(rows) > 15:
        print(f"   ... and {len(rows) - 15} more (see CSV)")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
