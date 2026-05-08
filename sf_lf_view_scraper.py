"""
sf_lf_view_scraper.py — fetch published view counts for SF input URLs so the
SF→LF Conversion funnel can anchor its projections to real, auditable numbers
instead of an unbounded `panel_count × 494.85` projection.

Why this exists
---------------
The old `project_to_gen_pop(value) = value * 15 / 10_000_000 * 329_900_000`
formula has no upper bound. For high-reach SF content (e.g. major IP), the
projected unique-user count blew past 329M (US population) — multi-billion
"Unique Views" numbers that obviously can't be unique people.

User requirement: anchor projections to the actual view counts published on
each SF video's platform page. Take SF_LF_VIEW_DISCOUNT (default 0.75) of the
published views as the projected "Total Views (Duplicated)", then reverse-
derive the panel-equivalent base:

    raw_panel_views = (scraped_views * SF_LF_VIEW_DISCOUNT) / 329_900_000 * 10_000_000
                    = scraped_views * SF_LF_VIEW_DISCOUNT / 32.99

All other panel-derived metrics (unique, converted, LF-platform-visit) are
then rescaled by `scaled_total / panel_total` for that platform, so the funnel
stays internally consistent and unique always stays under US population.

Scrape strategy
---------------
- YouTube: yt_dlp.extract_info(download=False) — uses the same metadata
  pipeline the YouTube app does, no API key needed. Returns view_count
  reliably for normal videos and shorts.
- TikTok:  yt_dlp first (works for many public videos); falls back to a
  lightweight HTML scrape that tries the SIGI_STATE / __NEXT_DATA__ blobs
  and a few common play-count regexes.
- Instagram: HTML scrape only (yt_dlp requires a logged-in session for IG
  Reels). We try the og:description meta tag and several known regexes.
  IG often gates anonymous requests — failures here are expected.

Failures are silent (returns None for that URL) — the caller is responsible
for falling back to the panel-based estimate when coverage is poor.
"""
from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urlparse

import requests

# yt_dlp is optional — if missing we fall through to HTML scraping only.
try:
    import yt_dlp  # type: ignore

    _HAS_YT_DLP = True
except Exception:  # pragma: no cover
    yt_dlp = None  # type: ignore
    _HAS_YT_DLP = False

# Suppress yt_dlp's chatty stderr; we have our own logger.
logging.getLogger("yt_dlp").setLevel(logging.ERROR)
log = logging.getLogger(__name__)


# Browser-ish UA: many social platforms 403 plain `python-requests/X.Y`.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15"
)
_HTTP_TIMEOUT = 12

# Cap concurrent scrapes — we don't want to hammer YouTube's anti-abuse.
_MAX_WORKERS = int(os.environ.get("SF_LF_SCRAPE_WORKERS", "5"))


# ── platform detection ────────────────────────────────────────────────────
def detect_platform(url: str) -> str:
    """Return one of 'youtube', 'tiktok', 'instagram', 'facebook', or 'other'."""
    if not url:
        return "other"
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return "other"
    if "youtube." in host or "youtu.be" in host:
        return "youtube"
    if "tiktok." in host:
        return "tiktok"
    if "instagram." in host:
        return "instagram"
    # Facebook covers main domains (facebook.com, m.facebook.com, web.facebook.com),
    # the short-link domain fb.watch, and the rare fb.com vanity host.
    if (
        "facebook." in host
        or host == "fb.com"
        or host.endswith(".fb.com")
        or "fb.watch" in host
    ):
        return "facebook"
    return "other"


# ── yt_dlp path ───────────────────────────────────────────────────────────
def _scrape_with_ytdlp(url: str) -> tuple[Optional[int], Optional[str]]:
    """Use yt_dlp to extract (view_count, creator/uploader) without
    downloading the media. Either field can be None if missing."""
    if not _HAS_YT_DLP:
        return None, None
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # Avoid extra requests (comments, formats we don't need).
        "extract_flat": False,
        "socket_timeout": _HTTP_TIMEOUT,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return None, None
        if isinstance(info.get("entries"), list) and info["entries"]:
            info = info["entries"][0] or info
        vc = info.get("view_count")
        # Creator: prefer the human-readable display name. yt_dlp populates
        # different fields per platform (YouTube: 'uploader' / 'channel';
        # TikTok: 'uploader' / 'creator'). Fall back through them in order
        # so we don't end up with raw channel IDs when a display name exists.
        creator = (
            info.get("uploader")
            or info.get("channel")
            or info.get("creator")
            or info.get("uploader_id")
        )
        if isinstance(creator, str):
            creator = creator.strip() or None
        else:
            creator = None
        try:
            vc = int(vc) if vc is not None else None
        except (TypeError, ValueError):
            vc = None
        return vc, creator
    except Exception as e:
        log.debug("yt_dlp failed for %s: %s", url, e)
        return None, None


# ── HTML scrape fallbacks ────────────────────────────────────────────────
def _http_get(url: str) -> Optional[str]:
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=_HTTP_TIMEOUT,
            allow_redirects=True,
        )
        if r.status_code != 200:
            log.debug("HTTP %s for %s", r.status_code, url)
            return None
        return r.text
    except Exception as e:
        log.debug("requests.get failed for %s: %s", url, e)
        return None


def _parse_count_word(s: str) -> Optional[int]:
    """'1.2M', '450K', '12,345', '12345 views' → int."""
    if not s:
        return None
    s = s.strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KkMmBb]?)", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    suffix = m.group(2).lower()
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(n * mult)


_TIKTOK_PLAY_PATTERNS = [
    re.compile(r'"playCount"\s*:\s*(\d+)'),
    re.compile(r'"play_count"\s*:\s*(\d+)'),
    re.compile(r'"stats"\s*:\s*\{[^}]*?"playCount"\s*:\s*(\d+)'),
]
# Creator extraction. Prefer the display/nickname name when present
# ("Luke Ross") over the @handle ("lukerossfilm"). The URL also embeds
# the @handle as a fallback (.../@username/video/...).
_TIKTOK_CREATOR_PATTERNS = [
    re.compile(r'"author"\s*:\s*\{[^}]*?"nickname"\s*:\s*"([^"]+)"'),
    re.compile(r'"authorMeta"\s*:\s*\{[^}]*?"nickName"\s*:\s*"([^"]+)"'),
    re.compile(r'"nickname"\s*:\s*"([^"]+)"'),
    re.compile(r'"uniqueId"\s*:\s*"([^"]+)"'),
]
_TIKTOK_URL_HANDLE = re.compile(r"tiktok\.com/@([^/?#]+)", re.I)


def _scrape_tiktok_html(url: str) -> tuple[Optional[int], Optional[str]]:
    html = _http_get(url)
    views: Optional[int] = None
    creator: Optional[str] = None
    if html:
        for pat in _TIKTOK_PLAY_PATTERNS:
            m = pat.search(html)
            if m:
                try:
                    views = int(m.group(1))
                    break
                except ValueError:
                    continue
        for pat in _TIKTOK_CREATOR_PATTERNS:
            m = pat.search(html)
            if m and m.group(1).strip():
                creator = m.group(1).strip()
                break
    if not creator:
        m = _TIKTOK_URL_HANDLE.search(url)
        if m:
            creator = '@' + m.group(1).strip()
    return views, creator


_IG_VIEW_PATTERNS = [
    re.compile(r'"video_view_count"\s*:\s*(\d+)'),
    re.compile(r'"video_play_count"\s*:\s*(\d+)'),
    re.compile(r'"play_count"\s*:\s*(\d+)'),
]
_IG_OG_DESC = re.compile(
    r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', re.I
)
_IG_OG_VIEWS = re.compile(r"([\d.,]+\s*[KkMmBb]?)\s*(?:views|plays|likes)", re.I)
# IG creator: og:title is typically "Luke Ross on Instagram: ..." for Reels.
# og:description often starts with "X likes, Y comments - username on..." too.
_IG_OG_TITLE = re.compile(
    r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', re.I
)
_IG_TITLE_USER = re.compile(r"^(.+?)\s+on Instagram", re.I)
_IG_DESC_USER = re.compile(r"-\s+([^@\s][^-:]+?)\s+on (?:Instagram|November|December|January|February|March|April|May|June|July|August|September|October)", re.I)
_IG_OWNER_PATTERNS = [
    re.compile(r'"owner"\s*:\s*\{[^}]*?"username"\s*:\s*"([^"]+)"'),
    re.compile(r'"owner"\s*:\s*\{[^}]*?"full_name"\s*:\s*"([^"]+)"'),
    re.compile(r'"username"\s*:\s*"([^"]+)"'),
]


def _scrape_instagram_html(url: str) -> tuple[Optional[int], Optional[str]]:
    html = _http_get(url)
    views: Optional[int] = None
    creator: Optional[str] = None
    if html:
        for pat in _IG_VIEW_PATTERNS:
            m = pat.search(html)
            if m:
                try:
                    views = int(m.group(1))
                    break
                except ValueError:
                    continue
        # Last resort for views: og:description sometimes carries
        # "12.3K likes, 45 comments". Treat likes × 10 as a rough views
        # proxy (IG Reels engagement is typically in that band).
        if views is None:
            desc_m = _IG_OG_DESC.search(html)
            if desc_m:
                v = _IG_OG_VIEWS.search(desc_m.group(1))
                if v:
                    n = _parse_count_word(v.group(1))
                    if n:
                        views = int(n * 10)
        # Creator: prefer the og:title "Luke Ross on Instagram" form
        # (gives display name); fall back to JSON `owner.username` for
        # the @handle when no display name is in the meta.
        title_m = _IG_OG_TITLE.search(html)
        if title_m:
            tu = _IG_TITLE_USER.search(title_m.group(1))
            if tu and tu.group(1).strip():
                creator = tu.group(1).strip()
        if not creator:
            desc_m = _IG_OG_DESC.search(html)
            if desc_m:
                du = _IG_DESC_USER.search(desc_m.group(1))
                if du and du.group(1).strip():
                    creator = du.group(1).strip()
        if not creator:
            for pat in _IG_OWNER_PATTERNS:
                m = pat.search(html)
                if m and m.group(1).strip():
                    creator = '@' + m.group(1).strip()
                    break
    return views, creator


# Facebook view-count patterns embedded in the HTML / inlined GraphQL
# response. FB renames these on a regular cadence so we try several.
_FB_VIEW_PATTERNS = [
    re.compile(r'"video_view_count"\s*:\s*(\d+)'),
    re.compile(r'"video_view_count_reduced"\s*:\s*"([\d.,KMBkmb]+)"'),
    re.compile(r'"play_count"\s*:\s*(\d+)'),
    re.compile(r'"playCount"\s*:\s*(\d+)'),
    re.compile(r'"viewCount"\s*:\s*(\d+)'),
]
# Creator: FB embeds the page/actor name in several spots. Prefer the
# human-readable display name (in og:title / actors[0].name) over the
# page username/handle.
_FB_OG_TITLE = re.compile(
    r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', re.I
)
_FB_TITLE_USER = re.compile(r"^(.+?)\s*\|\s*Facebook", re.I)
_FB_ACTOR_PATTERNS = [
    re.compile(r'"actors"\s*:\s*\[\s*\{[^}]*?"name"\s*:\s*"([^"]+)"'),
    re.compile(r'"owner"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"'),
    re.compile(r'"page"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"'),
]
_FB_USERNAME_PATTERNS = [
    re.compile(r'"actors"\s*:\s*\[\s*\{[^}]*?"username"\s*:\s*"([^"]+)"'),
    re.compile(r'"page"\s*:\s*\{[^}]*?"username"\s*:\s*"([^"]+)"'),
]
# Pretty-URL handle fallback: facebook.com/<handle>/(posts|videos|reel)/...
# Skips reserved paths that aren't page handles.
_FB_URL_HANDLE = re.compile(
    r"facebook\.com/([A-Za-z0-9.\-]+)/(?:posts|videos|reel|photos)",
    re.I,
)
_FB_URL_HANDLE_RESERVED = {
    "permalink.php", "story.php", "watch", "reel", "story",
    "events", "marketplace", "groups", "pages", "profile.php",
    "media", "photo.php", "share",
}


def _scrape_facebook_html(url: str) -> tuple[Optional[int], Optional[str]]:
    """Best-effort views + creator from a Facebook page/reel/post.
    Often gated behind login — failures are expected and silent."""
    html = _http_get(url)
    views: Optional[int] = None
    creator: Optional[str] = None
    if html:
        for pat in _FB_VIEW_PATTERNS:
            m = pat.search(html)
            if not m:
                continue
            raw = m.group(1)
            try:
                views = int(raw)
                break
            except ValueError:
                # "video_view_count_reduced" gives '1.2K' / '450K' style.
                n = _parse_count_word(raw)
                if n:
                    views = n
                    break
        # Creator: prefer og:title's "Page Name | Facebook" shape since
        # it's the display name FB renders in the share card. Fall back
        # to inlined actor/page JSON when the meta tag is generic.
        title_m = _FB_OG_TITLE.search(html)
        if title_m:
            tu = _FB_TITLE_USER.search(title_m.group(1))
            if tu and tu.group(1).strip() and tu.group(1).strip().lower() != 'facebook':
                creator = tu.group(1).strip()
        if not creator:
            for pat in _FB_ACTOR_PATTERNS:
                m = pat.search(html)
                if m and m.group(1).strip():
                    creator = m.group(1).strip()
                    break
        if not creator:
            for pat in _FB_USERNAME_PATTERNS:
                m = pat.search(html)
                if m and m.group(1).strip():
                    creator = '@' + m.group(1).strip()
                    break
    # URL-path handle fallback (e.g. facebook.com/luxonline/posts/...).
    # Skipped for reserved paths like /reel/, /permalink.php, etc.
    if not creator:
        m = _FB_URL_HANDLE.search(url)
        if m and m.group(1).lower() not in _FB_URL_HANDLE_RESERVED:
            creator = '@' + m.group(1).strip()
    return views, creator


# ── unified per-URL scrape ────────────────────────────────────────────────
def scrape_one(url: str) -> tuple[str, str, Optional[int], Optional[str]]:
    """(url, platform, view_count_or_None, creator_or_None) — never raises."""
    platform = detect_platform(url)
    if platform == "other":
        return url, platform, None, None

    views: Optional[int] = None
    creator: Optional[str] = None

    # yt_dlp handles YouTube, TikTok, and Facebook well; IG needs auth so skip.
    if platform in ("youtube", "tiktok", "facebook"):
        v, c = _scrape_with_ytdlp(url)
        if v is not None and v > 0:
            views = v
        if c:
            creator = c

    # HTML fallbacks for views / creator if either still missing.
    if platform == "tiktok" and (views is None or creator is None):
        v, c = _scrape_tiktok_html(url)
        if views is None and v is not None and v > 0:
            views = v
        if creator is None and c:
            creator = c
    elif platform == "instagram" and (views is None or creator is None):
        v, c = _scrape_instagram_html(url)
        if views is None and v is not None and v > 0:
            views = v
        if creator is None and c:
            creator = c
    elif platform == "facebook" and (views is None or creator is None):
        v, c = _scrape_facebook_html(url)
        if views is None and v is not None and v > 0:
            views = v
        if creator is None and c:
            creator = c

    return url, platform, views, creator


# ── batch entry-point ────────────────────────────────────────────────────
def scrape_views_for_urls(
    urls: list[str], log_progress: bool = True
) -> dict[str, dict]:
    """
    Returns:
        {
            'per_url':       { url: {'platform': str,
                                     'views': int|None,
                                     'creator': str|None} },
            'per_platform':  { 'youtube': {'scraped_total': int,
                                          'scraped_count': int,   # # urls scraped successfully
                                          'attempted_count': int}, ... },
            'duration_sec':  float,
        }

    Safe to call on an empty list (returns zeros).
    """
    cleaned = [u.strip() for u in (urls or []) if u and u.strip()]
    out: dict[str, dict] = {
        "per_url": {},
        "per_platform": {},
        "duration_sec": 0.0,
    }
    if not cleaned:
        return out

    t0 = time.time()
    if log_progress:
        log.info("[SF-LF scrape] starting on %d URL(s)", len(cleaned))

    # Bucket per-platform attempted counts up front so we can compute coverage
    # even when every URL fails.
    for u in cleaned:
        plat = detect_platform(u)
        bucket = out["per_platform"].setdefault(
            plat,
            {"scraped_total": 0, "scraped_count": 0, "attempted_count": 0},
        )
        bucket["attempted_count"] += 1

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(scrape_one, u): u for u in cleaned}
        done = 0
        for fut in as_completed(futures):
            url, plat, views, creator = fut.result()
            out["per_url"][url] = {
                "platform": plat,
                "views": views,
                "creator": creator,
            }
            if views and views > 0:
                bucket = out["per_platform"].setdefault(
                    plat,
                    {"scraped_total": 0, "scraped_count": 0, "attempted_count": 0},
                )
                bucket["scraped_total"] += int(views)
                bucket["scraped_count"] += 1
            done += 1
            if log_progress and done % 5 == 0:
                log.info("[SF-LF scrape] %d/%d done", done, len(cleaned))

    out["duration_sec"] = round(time.time() - t0, 2)
    if log_progress:
        for plat, b in out["per_platform"].items():
            cov = (
                100.0 * b["scraped_count"] / b["attempted_count"]
                if b["attempted_count"]
                else 0.0
            )
            log.info(
                "[SF-LF scrape]   %-9s  %d/%d urls (%.0f%% coverage)  total views: %s",
                plat,
                b["scraped_count"],
                b["attempted_count"],
                cov,
                f"{b['scraped_total']:,}",
            )
        log.info("[SF-LF scrape] done in %.1fs", out["duration_sec"])
    return out


# ── projection / anchor math ─────────────────────────────────────────────
US_POPULATION = 329_900_000
PANEL_SIZE = 10_000_000
# (raw_panel × this) projects to US gen pop, capped naturally at US pop because
# raw_panel ≤ panel size. This is the no-15x-boost factor — same numerator,
# (US_POPULATION / PANEL_SIZE) ≈ 32.99.
NO_BOOST_MULTIPLIER = US_POPULATION / PANEL_SIZE  # 32.99
# 15× boost factor preserved for callers that need to undo old projections.
LEGACY_BOOST_MULTIPLIER = 15 * US_POPULATION / PANEL_SIZE  # 494.85


def get_view_discount() -> float:
    """SF_LF_VIEW_DISCOUNT env var, default 0.75."""
    try:
        v = float(os.environ.get("SF_LF_VIEW_DISCOUNT", "0.75"))
    except (TypeError, ValueError):
        v = 0.75
    return max(0.0, min(1.0, v))


# Minimum fraction of URLs on a platform that must scrape successfully before
# we trust the scraped total as the anchor. Below this we fall back to the
# panel estimate (raw × 32.99, capped at US pop). 0.80 is conservative — if
# 20%+ of URLs fail, the scraped sample is likely missing the long tail of
# popular videos (e.g. YouTube's "Video unavailable" / removed content), which
# would dramatically *under*-anchor the projection.
MIN_COVERAGE = float(os.environ.get("SF_LF_SCRAPE_MIN_COVERAGE", "0.80"))


# Platform-specific US user-base caps. Sources: Statista 2024-2025 (DataReportal,
# Pew). Used as a tighter ceiling than US_POPULATION for per-platform unique
# projections — e.g. you can't have 200M unique Instagram viewers in a country
# where Instagram has ~143M monthly active users.
PLATFORM_US_USER_CAP = {
    "youtube":   246_000_000,
    "instagram": 143_000_000,
    "tiktok":    150_000_000,
    "facebook":  175_000_000,
    "x":          70_000_000,
    "twitter":    70_000_000,
    "snapchat":  108_000_000,
}


def cap_for_platform(platform: Optional[str]) -> int:
    """Tighter unique-user cap for a known platform; falls back to US_POPULATION."""
    if not platform:
        return US_POPULATION
    return PLATFORM_US_USER_CAP.get(platform.lower(), US_POPULATION)


def compute_platform_anchors(
    scrape_result: dict,
    panel_totals_by_platform: dict[str, int],
) -> dict[str, dict]:
    """
    Build per-platform anchor info used downstream to rescale all panel-derived
    metrics so projections tie to real published view counts.

    Args:
        scrape_result: output of scrape_views_for_urls()
        panel_totals_by_platform: { 'youtube': panel_total_views, ... } — the
            duplicated/total-views count from the panel for that platform. Used
            to derive scale factor scrape_anchor / panel_total.

    Returns:
        {
            platform: {
                'mode':              'anchored' | 'panel_estimate',
                'scraped_total':     int,    # raw scraped sum
                'discount':          float,  # SF_LF_VIEW_DISCOUNT
                'projected_total':   int,    # scraped_total * discount
                'panel_total':       int,    # input
                'scale':             float,  # multiplier on raw panel counts
                'coverage':          float,  # 0.0–1.0
                'attempted':         int,
                'scraped_count':     int,
            }
        }
    """
    discount = get_view_discount()
    out: dict[str, dict] = {}
    per_plat = scrape_result.get("per_platform", {}) or {}

    # Union of platforms we have data on — either from scrape attempts or from
    # the panel (e.g. an SF platform with zero scrapeable URLs).
    all_plats = set(per_plat.keys()) | set(panel_totals_by_platform.keys())
    for plat in all_plats:
        b = per_plat.get(plat, {}) or {}
        attempted = int(b.get("attempted_count", 0))
        scraped_count = int(b.get("scraped_count", 0))
        scraped_total = int(b.get("scraped_total", 0))
        coverage = (scraped_count / attempted) if attempted else 0.0
        panel_total = int(panel_totals_by_platform.get(plat, 0) or 0)

        # Anchor only when (a) scrape coverage is high AND (b) the scraped
        # total is at least a meaningful fraction of what the panel implies.
        # Without (b), an anchor based on a few small successful scrapes (when
        # the popular videos are taken down / private) would dramatically
        # *under*-project the platform — dropping unique counts into the
        # hundreds and breaking the funnel ladder.
        panel_implied_total = panel_total * NO_BOOST_MULTIPLIER if panel_total else 0
        scrape_vs_panel_ratio = (
            (scraped_total * discount) / panel_implied_total
            if panel_implied_total > 0
            else 0.0
        )
        # Anchor when scrape disagrees with panel by less than 10× in either
        # direction — i.e. they're in the same ballpark. Wider than 10× means
        # one of them is unreliable and we should prefer the panel estimate.
        anchor_credible = 0.1 <= scrape_vs_panel_ratio <= 10.0

        if (
            scraped_total > 0
            and coverage >= MIN_COVERAGE
            and (anchor_credible or panel_total <= 0)
        ):
            mode = "anchored"
            projected_total = int(round(scraped_total * discount))
            if panel_total > 0:
                scale = projected_total / panel_total
            else:
                scale = NO_BOOST_MULTIPLIER
        else:
            mode = "panel_estimate"
            # No-15x-boost projection: each raw panel view represents 32.99
            # real US views. Naturally bounded by US pop × (raw / 10M).
            scale = NO_BOOST_MULTIPLIER
            projected_total = int(round(panel_total * scale)) if panel_total else 0

        out[plat] = {
            "mode": mode,
            "scraped_total": scraped_total,
            "discount": discount,
            "projected_total": projected_total,
            "panel_total": panel_total,
            "scale": scale,
            "coverage": coverage,
            "attempted": attempted,
            "scraped_count": scraped_count,
        }
    return out


def project_capped(
    raw_count: int,
    scale: float,
    cap: int = US_POPULATION,
    platform: Optional[str] = None,
) -> int:
    """Project raw panel count to gen pop with a hard cap.

    Used for any "people-shaped" metric (unique users, converted users,
    LF-platform visitors). Total/Duplicated Views should NOT use this — they
    can legitimately exceed US pop because one person watches many times.

    The cap is the tighter of `cap` and the platform-specific US user base
    (e.g. unique Instagram viewers can't exceed ~143M no matter what).
    """
    if raw_count is None or raw_count <= 0:
        return 0
    projected = int(round(raw_count * scale))
    effective_cap = min(cap, cap_for_platform(platform)) if platform else cap
    return min(projected, effective_cap)
