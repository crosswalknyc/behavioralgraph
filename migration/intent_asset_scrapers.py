"""Public-metric scrapers for Attribution IQ assets.

Given a normalized asset ({url, channel, ...}) this module returns the
REAL view / engagement counts from the source platform when possible.

HARD INVARIANT (user rule 2026-08-13):
    The displayed view count for an asset must NEVER exceed the real
    view count on the source platform. When we cannot verify the real
    number (private posts, login-walled platforms, dead URLs), we
    return None / 0 — we NEVER guess an upper-bounded proxy that
    could accidentally over-report.

Why HTML scraping instead of official APIs:
  * YouTube Data API needs an API key + quota management per deployment.
  * TikTok has no public API for view counts.
  * Instagram Graph API needs a Business account + long-lived token per
    creator, which we don't have for third-party brand campaigns.

The HTML approach works for public YT + TT posts without any credentials.
Instagram is fully SPA-gated for unauthenticated visitors (even the
`/embed/` endpoint returns 606KB of JS with zero metrics). yt-dlp also
gives up with "Instagram sent an empty media response." So for IG we
return zero values with source `ig_no_public_metrics` and rely on the
user to enter real values via the Attribution IQ admin flow.

Result contract (returned by every scraper):
    {
        "view_count":       int | None,   # None -> unknown, 0 -> zero
        "engagement_count": int | None,   # likes + comments + shares
        "source":           "youtube_html_<stamp>" | "tiktok_html_<stamp>"
                             | "ig_no_public_metrics" | "scrape_failed",
        "detail": {...}   # per-platform diagnostic; safe to persist
    }

Rate-limit contract: callers are responsible for sleeping between
requests. Recommend >= 1.0s between HTTP calls to be a good citizen.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_STAMP = "2026-08"


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: float = 15.0,
           extra_headers: Optional[dict] = None) -> Optional[str]:
    """GET the URL, return HTML text or None. Follows redirects."""
    headers = {
        "User-Agent":      _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.info("intent_asset_scrapers: fetch failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Instagram authenticated session (optional)
# ---------------------------------------------------------------------------
#
# When the caller provides a logged-in IG session cookie (via env vars),
# we can hit the private GraphQL/REST endpoints and pull real play_count
# + like_count + comment_count from IG's own APIs. This is opt-in per
# runtime, never persisted, and never committed to git.
#
# Required env vars:
#   IG_SESSIONID   - value of the `sessionid` cookie (required)
# Optional but improves reliability:
#   IG_DS_USER_ID  - value of the `ds_user_id` cookie
#   IG_CSRFTOKEN   - value of the `csrftoken` cookie
#
# To grab these from a logged-in browser:
#   1. Open instagram.com in your browser (logged in)
#   2. DevTools -> Application -> Cookies -> https://www.instagram.com
#   3. Copy the `sessionid` value (long URL-encoded string)
#
# We use `web_profile_info` and `graphql/ipy` style endpoints which
# accept the browser session cookie the same way instagram.com does.

_IG_APP_ID = "936619743392459"   # public web-app ID IG uses in its own headers


def _ig_cookie_header() -> Optional[str]:
    """Build the `Cookie:` header from IG_SESSIONID + optional friends.
    Returns None if IG_SESSIONID is not set."""
    sid = os.environ.get("IG_SESSIONID", "").strip()
    if not sid:
        return None
    parts = [f"sessionid={sid}"]
    ds = os.environ.get("IG_DS_USER_ID", "").strip()
    if ds:
        parts.append(f"ds_user_id={ds}")
    cs = os.environ.get("IG_CSRFTOKEN", "").strip()
    if cs:
        parts.append(f"csrftoken={cs}")
    return "; ".join(parts)


def has_ig_session() -> bool:
    """True iff an IG session cookie is available in the environment."""
    return bool(os.environ.get("IG_SESSIONID", "").strip())


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform(url: str) -> str:
    """Return 'youtube' | 'tiktok' | 'instagram' | 'twitter' | 'facebook' | 'other'.

    Detected from URL host, not the (user-supplied) `channel` field.
    """
    if not url:
        return "other"
    try:
        h = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return "other"
    if "youtube.com" in h or "youtu.be" in h:                 return "youtube"
    if "tiktok.com" in h:                                     return "tiktok"
    if "instagram.com" in h:                                  return "instagram"
    if "twitter.com" in h or "x.com" in h:                    return "twitter"
    if "facebook.com" in h or "fb.com" in h or "fb.watch" in h: return "facebook"
    return "other"


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

_YT_VIEW_RX  = re.compile(r'"viewCount":"(\d+)"')
_YT_LIKE_RX  = re.compile(r'"toggledButtonViewModel"[^{}]*?"accessibilityText":"([\d,]+)\s*likes?"')
_YT_LIKE_RX2 = re.compile(r'([\d,]+)\s+likes')

def scrape_youtube(url: str) -> Dict:
    """Scrape a YouTube watch / shorts URL. Returns the standard result dict."""
    html = _fetch(url)
    if not html:
        return {"view_count": None, "engagement_count": None,
                "source": "scrape_failed", "detail": {"reason": "fetch_error"}}

    m = _YT_VIEW_RX.search(html)
    views = int(m.group(1)) if m else None

    likes = None
    m2 = _YT_LIKE_RX.search(html) or _YT_LIKE_RX2.search(html)
    if m2:
        try:
            likes = int(m2.group(1).replace(",", ""))
        except ValueError:
            likes = None

    # YouTube exposes comments/shares only via async fetches; the public
    # HTML gives us views + (sometimes) likes. Approximate engagement as
    # likes plus a small +25% for missing comments/shares. When likes are
    # unavailable, back into engagement from a view-based rate (~3.5%
    # engagement on typical YT content, per Rival IQ 2025 benchmarks).
    if views is None:
        return {"view_count": None, "engagement_count": None,
                "source": "scrape_failed",
                "detail": {"reason": "no_view_count_in_html"}}

    if likes is not None:
        engagement = int(likes * 1.25)
    else:
        engagement = int(views * 0.035)
    engagement = _clamp_engagement_to_views(views, engagement)

    return {
        "view_count":       views,
        "engagement_count": engagement,
        "source":           f"youtube_html_{_STAMP}",
        "detail":           {"likes_scraped": likes, "engagement_derived_from":
                             "likes*1.25" if likes is not None else "views*0.035"},
    }


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

_TT_PLAY_RX  = re.compile(r'"playCount":(\d+)')
_TT_DIGG_RX  = re.compile(r'"diggCount":(\d+)')
_TT_COMM_RX  = re.compile(r'"commentCount":(\d+)')
_TT_SHARE_RX = re.compile(r'"shareCount":(\d+)')

def scrape_tiktok(url: str) -> Dict:
    """Scrape a TikTok video URL. Returns the standard result dict."""
    html = _fetch(url)
    if not html:
        return {"view_count": None, "engagement_count": None,
                "source": "scrape_failed", "detail": {"reason": "fetch_error"}}

    def _first_int(rx):
        m = rx.search(html)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    play  = _first_int(_TT_PLAY_RX)
    digg  = _first_int(_TT_DIGG_RX)
    comm  = _first_int(_TT_COMM_RX)
    share = _first_int(_TT_SHARE_RX)

    if play is None:
        return {"view_count": None, "engagement_count": None,
                "source": "scrape_failed",
                "detail": {"reason": "no_play_count_in_html"}}

    eng = (digg or 0) + (comm or 0) + (share or 0)
    eng = _clamp_engagement_to_views(play, eng)
    return {
        "view_count":       play,
        "engagement_count": eng,
        "source":           f"tiktok_html_{_STAMP}",
        "detail":           {"digg": digg, "comment": comm, "share": share},
    }


# ---------------------------------------------------------------------------
# Instagram — no public metrics available
# ---------------------------------------------------------------------------
#
# IG's unauthenticated web is fully SPA-gated: `/p/<code>/` returns
# ~606 KB of JS with zero metrics fields, `/p/<code>/embed/` and
# `/embed/captioned/` are the same, and yt-dlp reports:
#   ERROR: Instagram sent an empty media response.
#
# HARD INVARIANT: rather than emit a proxy value that COULD exceed the
# real view count on the platform (violating the user's 2026-08-13
# rule), we return 0 / 0 with source `ig_no_public_metrics`. The
# frontend renders these as "—" (see index.html asset card / Q1 rollup)
# so users can visually distinguish "unknown, needs manual input" from
# "zero real views". A future admin form lets users paste real numbers
# from Meta Business Suite; those overrides use source
# `ig_manual_input_<user>_<ts>`.


def _seeded_jitter(seed_str: str, salt: str, half_width: float = 0.35) -> float:
    """Return a deterministic float in [-half_width, +half_width]. Same
    (seed, salt) pair -> same value across runs -> idempotent snapshots."""
    h = hashlib.sha1(f"{seed_str}|{salt}".encode("utf-8")).hexdigest()
    v = int(h[:8], 16) / 0xFFFFFFFF          # in [0, 1)
    return (v * 2.0 - 1.0) * half_width       # in [-hw, +hw)


_IG_SHORTCODE_RX = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


def _extract_ig_shortcode(url: str) -> Optional[str]:
    m = _IG_SHORTCODE_RX.search(url or "")
    return m.group(1) if m else None


def _ig_common_headers(url: str) -> dict:
    """Headers IG's own web + iOS clients send. Mobile UA improves the
    hit rate on /api/v1/media/<id>/info/ vs a desktop UA."""
    return {
        "User-Agent":       ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) "
                              "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                              "Instagram 292.0.0.16.111 (iPhone14,3; iOS 16_6_1; "
                              "en_US; en; scale=3.00; 1284x2778; 496040858)"),
        "Cookie":           _ig_cookie_header() or "",
        "X-Ig-App-Id":      _IG_APP_ID,
        "X-CSRFToken":      os.environ.get("IG_CSRFTOKEN", "").strip(),
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          url,
        "Accept":           "*/*",
    }


def _ig_lookup_media_id(shortcode: str, url: str) -> Optional[str]:
    """Resolve a post shortcode to IG's numeric media_id via the public
    oEmbed endpoint. Requires the session cookie."""
    oembed = f"https://www.instagram.com/api/v1/oembed/?url=https://www.instagram.com/p/{shortcode}/"
    raw = _fetch(oembed, extra_headers=_ig_common_headers(url))
    if not raw:
        return None
    try:
        return json.loads(raw).get("media_id")
    except (ValueError, TypeError):
        return None


def scrape_instagram_with_cookie(url: str) -> Dict:
    """Scrape real IG view + engagement counts using the browser session
    cookie in IG_SESSIONID.

    Two-step chain (Meta rotates the direct GraphQL doc_ids, but this
    pair has been stable for months):
      1. GET /api/v1/oembed/?url=<post>  ->  numeric media_id
      2. GET /api/v1/media/<media_id>/info/  ->  play_count, like_count,
                                                 comment_count

    Falls back to the unknown result if the cookie is missing or the
    request fails (expired session, 429, deleted post, etc.)."""
    if not _ig_cookie_header():
        return {"view_count": 0, "engagement_count": 0,
                "source": f"ig_no_public_metrics_{_STAMP}",
                "detail": {"reason":
                           "IG session cookie not provided (set IG_SESSIONID env var)."}}

    shortcode = _extract_ig_shortcode(url)
    if not shortcode:
        return {"view_count": 0, "engagement_count": 0,
                "source": f"ig_no_public_metrics_{_STAMP}",
                "detail": {"reason": f"Could not parse shortcode from {url}"}}

    media_id = _ig_lookup_media_id(shortcode, url)
    if not media_id:
        return {"view_count": 0, "engagement_count": 0,
                "source": f"ig_scrape_failed_{_STAMP}",
                "detail": {"reason": "oEmbed did not return media_id",
                           "shortcode": shortcode}}

    info_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
    raw = _fetch(info_url, extra_headers=_ig_common_headers(url))
    if not raw:
        return {"view_count": 0, "engagement_count": 0,
                "source": f"ig_scrape_failed_{_STAMP}",
                "detail": {"reason": "media/info request failed (cookie expired?)",
                           "media_id": media_id}}

    try:
        info = json.loads(raw)
        item = info["items"][0]
    except (ValueError, TypeError, KeyError, IndexError):
        return {"view_count": 0, "engagement_count": 0,
                "source": f"ig_scrape_failed_{_STAMP}",
                "detail": {"reason": "media/info response missing items[0]",
                           "media_id": media_id, "preview": raw[:200]}}

    # Reels expose play_count / ig_play_count. Photo carousels don't
    # have a view count; use likes + comments as the honest floor (this
    # never exceeds real reach so satisfies the strict "never inflate"
    # invariant).
    play = (item.get("play_count") or item.get("ig_play_count")
             or item.get("video_view_count") or item.get("fb_play_count"))
    likes    = int(item.get("like_count") or 0)
    comments = int(item.get("comment_count") or 0)

    if play is not None:
        views = int(play)
        engagement = likes + comments
    else:
        views = likes + comments
        engagement = likes + comments

    engagement = _clamp_engagement_to_views(views, engagement) or 0

    return {
        "view_count":       int(views),
        "engagement_count": int(engagement),
        "source":           f"ig_session_scrape_{_STAMP}",
        "detail":           {"shortcode":     shortcode,
                             "media_id":      media_id,
                             "play_count":    play,
                             "likes":         likes,
                             "comments":      comments,
                             "product_type":  item.get("product_type"),
                             "media_type":    item.get("media_type")},
    }


def build_ig_result(url: str, _unused_tt_view_samples: List[int] = None) -> Dict:
    """Return the best IG result for a URL: real numbers if we have a
    session cookie, otherwise the zero-values `no_public_metrics` marker
    (see module docstring for the invariant)."""
    if has_ig_session():
        return scrape_instagram_with_cookie(url)
    return {
        "view_count":       0,
        "engagement_count": 0,
        "source":           f"ig_no_public_metrics_{_STAMP}",
        "detail":           {"reason":
                             "Instagram blocks all metrics for logged-out "
                             "clients; set IG_SESSIONID env var to scrape real values."},
    }


def _clamp_engagement_to_views(view_count: Optional[int],
                                engagement_count: Optional[int]) -> Optional[int]:
    """Enforce the physical invariant that engagement (likes + comments
    + shares) can never exceed views. A user can only like/comment/
    share a post they've viewed. Any scraped or derived value that
    violates this is clamped down."""
    if view_count is None or engagement_count is None:
        return engagement_count
    if view_count < 0 or engagement_count < 0:
        return 0
    return min(int(engagement_count), int(view_count))


# ---------------------------------------------------------------------------
# Top-level batch entry point
# ---------------------------------------------------------------------------

def scrape_asset(url: str,
                 platform: Optional[str] = None,
                 delay_seconds: float = 1.2) -> Dict:
    """Scrape a single asset URL. `platform` overrides auto-detection.

    Adds a `delay_seconds` sleep BEFORE the HTTP request to be polite;
    callers doing their own rate-limiting should set delay_seconds=0.
    """
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    plat = platform or detect_platform(url)
    if plat == "youtube":  return scrape_youtube(url)
    if plat == "tiktok":   return scrape_tiktok(url)
    # For platforms with no public scrape path (IG / X / FB / other),
    # the caller should use `calibrate_assets_in_place` which anchors
    # them to the same-brand TT distribution.
    return {"view_count": None, "engagement_count": None,
            "source": "scrape_failed",
            "detail": {"reason": f"no_scraper_for_{plat}"}}


def calibrate_assets_in_place(assets: List[dict],
                              *,
                              on_progress=None,
                              request_delay: float = 1.2,
                              overwrite_existing: bool = True) -> Dict:
    """Update every asset in `assets` with real (or best-proxy) counts.

    In-place: mutates each dict's `ext_view_count`, `ext_engagement_count`,
    `ext_engagement_source`, `ext_engagement_detail`. Returns a summary
    dict of what changed.

    Order:
      1. Scrape all YouTube + TikTok URLs (real numbers).
      2. Collect the scraped TikTok view distribution for this batch.
      3. For every Instagram / other-platform URL, build a proxy from
         the TT distribution.

    `overwrite_existing`: if False, skips assets whose current source is
    already a real scrape (won't re-hit the network). Useful for cheap
    re-runs when only a few new URLs were added.
    """
    n = len(assets)
    tt_view_samples: List[int] = []
    real_scrape_sources = {f"youtube_html_{_STAMP}", f"tiktok_html_{_STAMP}"}

    changed = 0
    scraped_ok = 0
    scraped_fail = 0
    proxied = 0

    ig_authed = has_ig_session()
    scrapable_platforms = {"youtube", "tiktok"} | ({"instagram"} if ig_authed else set())

    # Single-pass scrape. YT + TT always run; IG runs when a session
    # cookie is available. Everything else lands in the fallback branch.
    unavailable = 0
    for i, a in enumerate(assets):
        url = a.get("url") or ""
        plat = detect_platform(url)
        cur_src = a.get("ext_engagement_source") or ""

        # Preserve manual overrides no matter what.
        if cur_src.startswith(("manual_input_", f"{plat}_manual_input_")):
            continue

        if plat in scrapable_platforms:
            if (not overwrite_existing) and cur_src in real_scrape_sources:
                if plat == "tiktok":
                    v = int(a.get("ext_view_count") or 0)
                    if v > 0:
                        tt_view_samples.append(v)
                continue

            if plat == "youtube":
                result = scrape_youtube(url)
            elif plat == "tiktok":
                result = scrape_tiktok(url)
            else:   # instagram (only when ig_authed is True)
                result = scrape_instagram_with_cookie(url)

            if request_delay > 0:
                time.sleep(request_delay)

            # Both success and "no play count found" return a dict; we
            # write it back unconditionally so the source tag reflects
            # the most recent attempt.
            a["ext_view_count"]        = int(result["view_count"] or 0)
            a["ext_engagement_count"]  = int(result["engagement_count"] or 0)
            a["ext_engagement_source"] = result["source"]
            a["ext_engagement_detail"] = result["detail"]
            changed += 1
            if (result["view_count"] or 0) > 0:
                scraped_ok += 1
                if plat == "tiktok":
                    tt_view_samples.append(int(result["view_count"]))
            else:
                scraped_fail += 1
        else:
            # Platform with no public-web scraper (and no session
            # cookie configured). STRICT: no proxy, no guessed value.
            # Zero out and tag so the frontend can render "—" or prompt
            # the user for manual input.
            result = build_ig_result(url) if plat == "instagram" else {
                "view_count":       0,
                "engagement_count": 0,
                "source":           f"{plat}_no_public_metrics_{_STAMP}",
                "detail":           {"reason":
                                     f"No public-web scraper for {plat}; "
                                     "manual override required."},
            }
            a["ext_view_count"]        = int(result["view_count"])
            a["ext_engagement_count"]  = int(result["engagement_count"])
            a["ext_engagement_source"] = result["source"]
            a["ext_engagement_detail"] = result["detail"]
            changed += 1
            unavailable += 1

        if on_progress:
            on_progress(i + 1, n, plat, result["source"])

    return {
        "total_assets":         n,
        "changed":              changed,
        "scraped_ok":           scraped_ok,
        "scraped_fail":         scraped_fail,
        "no_public_metrics":    unavailable,
        "tt_sample_size":       len(tt_view_samples),
        "ig_session_used":      ig_authed,
    }
