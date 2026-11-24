"""Brand Partnership IQ: a pasted campaign link, read on its own page
(Jenna 2026-11-24).

Emi Hill's Zarna Garg x Maybelline order carried 52 campaign posts on
Instagram, Facebook, TikTok and YouTube, and the valuation counted none
of them: the links rode the research asks as context (the first 12
only) and nothing turned a link into a post the reach could count, so
the people the campaign reached came from three press pieces.

A link the reader pastes is a campaign post in its own right, on any
platform. This module says which platform a link is on (from the URL
itself, never from the talent's listed channels), which account it
belongs to when the URL carries it, the day it went up when the post
id carries it, and the counts its own page shows (views, likes,
comments). A count the page does not show is None, never a guess. An
account's size is read from the account's own page the same way.

Pure parsing plus one fetch wrapper. A fetch is best effort inside a
short time box; a page that cannot be read returns its platform and
nothing else. No fetch runs under REGRESSION_TEST_MODE unless one is
passed in, or when BPIQ_LINK_PAGE_READS=0.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

FETCH_TIMEOUT_S = 12
FETCH_WORKERS = 8
MAX_LINKS = 120          # a message of links is read whole; this only bounds a runaway paste
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# host -> the platform name the engine's platform rows and rate card use
_HOSTS = (
    (("instagram.com", "instagr.am"), "Instagram"),
    (("facebook.com", "fb.watch", "fb.com", "fb.me"), "Facebook"),
    (("tiktok.com",), "TikTok"),
    (("youtube.com", "youtu.be", "youtube-nocookie.com"), "YouTube"),
    (("x.com", "twitter.com"), "X (Twitter)"),
    (("threads.net", "threads.com"), "Threads"),
    (("pinterest.com", "pin.it"), "Pinterest"),
    (("snapchat.com",), "Snapchat"),
    (("linkedin.com", "lnkd.in"), "LinkedIn"),
    (("reddit.com", "redd.it"), "Reddit"),
    (("twitch.tv",), "Twitch"),
)
_URL_RE = re.compile(r"https?://[^\s,;<>\"']+", re.I)
_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_IG_EPOCH_MS = 1314220021721       # the id clock Instagram media ids count from
_X_EPOCH_MS = 1288834974657        # the snowflake clock X post ids count from


def _host(url: str) -> str:
    try:
        h = urllib.parse.urlsplit(str(url or "").strip()).hostname or ""
    except ValueError:
        return ""
    h = h.lower()
    return h[4:] if h.startswith("www.") else h


def platform_of(url: str) -> str:
    """The platform a link is on, from its address; '' for anything else."""
    h = _host(url)
    if not h:
        return ""
    for hosts, name in _HOSTS:
        if any(h == d or h.endswith("." + d) for d in hosts):
            return name
    return ""


def social_links(links) -> list:
    """The pasted links that sit on a social platform, verbatim, once each
    (case and trailing-slash insensitive), in the order given."""
    out, seen = [], set()
    for raw in list(links or [])[:MAX_LINKS]:
        for u in (_URL_RE.findall(str(raw or "")) or []):
            u = u.rstrip(").,;")
            if not platform_of(u):
                continue
            k = link_key(u)
            if k in seen:
                continue
            seen.add(k)
            out.append(u)
    return out


def link_key(url: str) -> str:
    """One identity per post: scheme, www, case of the host, a trailing
    slash and tracking parameters do not make two posts."""
    try:
        p = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return str(url or "").strip().lower()
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    host = {"m.facebook.com": "facebook.com", "m.youtube.com": "youtube.com", "mobile.twitter.com": "x.com",
            "twitter.com": "x.com", "vm.tiktok.com": "tiktok.com"}.get(host, host)
    keep = []
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False):
        if k.lower() in ("v", "story_fbid", "id", "fbid", "list"):
            keep.append(f"{k}={v}")
    path = p.path.rstrip("/")
    return f"{host}{path}" + (("?" + "&".join(sorted(keep))) if keep else "")


def account_of(url: str) -> str:
    """The account a post belongs to when its address names it."""
    try:
        p = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    parts = [s for s in p.path.split("/") if s]
    plat = platform_of(url)
    if not parts:
        return ""
    if plat in ("TikTok", "Threads", "YouTube") and parts[0].startswith("@"):
        return parts[0][1:]
    if plat == "X (Twitter)" and len(parts) >= 3 and parts[1] == "status":
        return parts[0]
    if plat == "Facebook" and parts[0] not in ("video.php", "watch", "story.php", "permalink.php", "photo.php",
                                               "reel", "share", "groups", "events", "photo"):
        return parts[0]
    if plat == "Instagram" and len(parts) >= 3 and parts[1] in ("p", "reel", "tv"):
        return parts[0]
    return ""


def _ig_code(url: str) -> str:
    parts = [s for s in urllib.parse.urlsplit(url).path.split("/") if s]
    for i, s in enumerate(parts):
        if s in ("p", "reel", "reels", "tv") and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def published_from_id(url: str) -> Optional[_dt.date]:
    """The day a post went up, when its id carries the clock (TikTok, X,
    Instagram). None otherwise."""
    try:
        plat = platform_of(url)
        path = urllib.parse.urlsplit(url).path
        if plat == "TikTok":
            m = re.search(r"/(?:video|photo)/(\d{15,22})", path)
            if m:
                return _dt.datetime.fromtimestamp(int(m.group(1)) >> 32, _dt.timezone.utc).date()
        if plat == "X (Twitter)":
            m = re.search(r"/status/(\d{10,22})", path)
            if m:
                return _dt.datetime.fromtimestamp(((int(m.group(1)) >> 22) + _X_EPOCH_MS) / 1000.0, _dt.timezone.utc).date()
        if plat == "Instagram":
            code = _ig_code(url)
            if code and len(code) <= 12 and all(c in _IG_ALPHABET for c in code):
                n = 0
                for c in code:
                    n = n * 64 + _IG_ALPHABET.index(c)
                ms = (n >> 23) + _IG_EPOCH_MS
                d = _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).date()
                if _dt.date(2012, 1, 1) <= d <= _dt.date.today() + _dt.timedelta(days=2):
                    return d
    except (ValueError, OverflowError, OSError):
        return None
    return None


_SCALE = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}


def count_of(text) -> Optional[int]:
    """'400K' -> 400000, '2.7K' -> 2700, '1,234' -> 1234, '12' -> 12."""
    m = re.match(r"^\s*([\d][\d,]*(?:\.\d+)?)\s*([kmbKMB]?)\s*$", str(text or ""))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "")) * _SCALE[m.group(2).lower()]
    except ValueError:
        return None
    return int(round(v)) if v > 0 else None


def _date(v) -> Optional[_dt.date]:
    s = str(v or "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 9:
        try:
            return _dt.datetime.fromtimestamp(int(s), _dt.timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _stats_block(html_text: str, after: str) -> dict:
    i = html_text.find(after)
    if i < 0:
        return {}
    j = html_text.find('"stats":{', i)
    if j < 0:
        return {}
    k = html_text.find("}", j)
    blob = html_text[j + len('"stats":'):k + 1]
    try:
        doc = json.loads(blob)
        return doc if isinstance(doc, dict) else {}
    except ValueError:
        return {}


def parse_post_page(platform: str, html_text: str) -> dict:
    """The counts a post's own page shows: views, likes, comments, the day
    it went up and the account, each None when the page does not show
    it. Never a guess."""
    h = str(html_text or "")
    out = {"views": None, "likes": None, "comments": None, "published": None, "account": ""}
    if platform == "YouTube":
        m = re.search(r'"videoDetails":\{.*?"viewCount":"(\d+)"', h, re.S) or re.search(r'"viewCount":"(\d+)"', h)
        if m:
            out["views"] = int(m.group(1))
        m = re.search(r'"uploadDate":"([^"]+)"', h) or re.search(r'"publishDate":"([^"]+)"', h)
        if m:
            out["published"] = _date(m.group(1))
        m = re.search(r'"canonicalBaseUrl":"/@([^"/]+)"', h)
        if m:
            out["account"] = m.group(1)
    elif platform == "TikTok":
        st = _stats_block(h, '"itemStruct"')
        for key, field in (("playCount", "views"), ("diggCount", "likes"), ("commentCount", "comments")):
            v = st.get(key)
            if isinstance(v, (int, float)) and v > 0:
                out[field] = int(v)
        m = re.search(r'"itemStruct":\{.*?"createTime":"?(\d{9,11})"?', h, re.S)
        if m:
            out["published"] = _date(m.group(1))
        m = re.search(r'"itemStruct":\{.*?"uniqueId":"([^"]+)"', h, re.S)
        if m:
            out["account"] = m.group(1)
    elif platform == "Facebook":
        m = re.search(r'<meta property="og:title" content="([^"]*)"', h)
        title = _html.unescape(m.group(1)) if m else ""
        m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+views?\b", title)
        if m:
            out["views"] = count_of(m.group(1))
        m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+reactions?\b", title)
        if m:
            out["likes"] = count_of(m.group(1))
        m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+comments?\b", title)
        if m:
            out["comments"] = count_of(m.group(1))
        m = re.search(r'"publish_time":(\d{9,11})', h) or re.search(r'"creation_time":(\d{9,11})', h)
        if m:
            out["published"] = _date(m.group(1))
    elif platform == "Instagram":
        m = re.search(r'<meta (?:property|name)="og:description" content="([^"]*)"', h)
        desc = _html.unescape(m.group(1)) if m else ""
        m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+likes?\b", desc)
        if m:
            out["likes"] = count_of(m.group(1))
        m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+comments?\b", desc)
        if m:
            out["comments"] = count_of(m.group(1))
        m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+(?:views|plays)\b", desc)
        if m:
            out["views"] = count_of(m.group(1))
        m = re.search(r" - ([A-Za-z0-9._]+) on ", desc)
        if m:
            out["account"] = m.group(1)
    else:
        m = re.search(r'<meta (?:property|name)="og:description" content="([^"]*)"', h)
        desc = _html.unescape(m.group(1)) if m else ""
        for word, field in (("views", "views"), ("likes", "likes"), ("comments", "comments")):
            m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+" + word + r"\b", desc)
            if m:
                out[field] = count_of(m.group(1))
    return out


def parse_account_page(platform: str, html_text: str) -> Optional[int]:
    """An account's size as its own page shows it, or None."""
    h = str(html_text or "")
    if platform == "TikTok":
        m = re.search(r'"followerCount":(\d+)', h)
        return int(m.group(1)) if m and int(m.group(1)) > 0 else None
    if platform == "YouTube":
        m = re.search(r'"subscriberCountText":"([\d.,]+\s*[KMB]?)\s+subscribers"', h) \
            or re.search(r'"subscriberCountText":\{"accessibility":\{"accessibilityData":\{"label":"([\d.,]+\s*[KMB]?)\s+subscribers', h) \
            or re.search(r'"subscriberCountText":\{[^}]*"simpleText":"([\d.,]+\s*[KMB]?)\s+subscribers', h) \
            or re.search(r'"content":"([\d.,]+\s*[KMB]?)\s+subscribers"', h)
        return count_of(m.group(1)) if m else None
    m = re.search(r'<meta (?:property|name)="og:description" content="([^"]*)"', h)
    desc = _html.unescape(m.group(1)) if m else ""
    m = re.search(r"([\d][\d,.]*\s*[KMBkmb]?)\s+(?:Followers|followers)\b", desc)
    return count_of(m.group(1)) if m else None


def enabled() -> bool:
    return os.environ.get("BPIQ_LINK_PAGE_READS", "1").strip().lower() not in ("0", "false", "no", "off")


def _fetch(url: str, timeout: float = FETCH_TIMEOUT_S) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
                                               "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:     # noqa: S310 (a public post page)
        if "/login" in str(r.geturl() or ""):
            return ""
        return r.read(3_000_000).decode("utf-8", "ignore")


def _fetcher(fetch: Optional[Callable]) -> Optional[Callable]:
    if fetch is not None:
        return fetch
    if os.environ.get("REGRESSION_TEST_MODE") or not enabled():
        return None
    return _fetch


def read_link(url: str, fetch: Optional[Callable] = None) -> dict:
    """One pasted link: its platform, account, day and the counts its own
    page shows. `read` is True only when the page gave a count. Never
    raises."""
    plat = platform_of(url)
    day = published_from_id(url)
    rec = {"url": str(url or "").strip(), "platform": plat, "account": account_of(url),
           "views": None, "likes": None, "comments": None,
           "published": day.isoformat() if day else None, "read": False}
    f = _fetcher(fetch)
    if not plat or f is None:
        return rec
    try:
        got = parse_post_page(plat, f(url) or "")
    except Exception:
        return rec
    for k in ("views", "likes", "comments"):
        if got.get(k):
            rec[k] = int(got[k])
    if got.get("published") and not rec["published"]:
        rec["published"] = got["published"].isoformat()
    if got.get("account") and not rec["account"]:
        rec["account"] = got["account"]
    rec["read"] = bool(rec["views"] or rec["likes"] or rec["comments"])
    return rec


def read_links(links, fetch: Optional[Callable] = None) -> list:
    """Every pasted social link read on its own page, in the order given."""
    urls = social_links(links)
    if not urls:
        return []
    f = _fetcher(fetch)
    if f is None:
        return [read_link(u, fetch=None) for u in urls]
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        return list(ex.map(lambda u: read_link(u, fetch=f), urls))


_ACCOUNT_PAGE = {
    "Instagram": "https://www.instagram.com/{a}/",
    "TikTok": "https://www.tiktok.com/@{a}",
    "YouTube": "https://www.youtube.com/@{a}/about",
    "Facebook": "https://www.facebook.com/{a}/",
    "X (Twitter)": "https://x.com/{a}",
    "Threads": "https://www.threads.net/@{a}",
}


def read_account(platform: str, account: str, fetch: Optional[Callable] = None) -> Optional[int]:
    """An account's size from its own page, or None. Never raises."""
    a = str(account or "").strip().lstrip("@")
    tpl = _ACCOUNT_PAGE.get(platform)
    f = _fetcher(fetch)
    if not a or not tpl or f is None or not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", a):
        return None
    try:
        return parse_account_page(platform, f(tpl.format(a=a)) or "")
    except Exception:
        return None


__all__ = ["platform_of", "social_links", "link_key", "account_of", "published_from_id", "count_of",
           "parse_post_page", "parse_account_page", "read_link", "read_links", "read_account", "enabled",
           "MAX_LINKS"]
