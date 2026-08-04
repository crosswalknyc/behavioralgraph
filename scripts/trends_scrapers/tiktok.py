"""
TikTok trending scraper - real trending videos from tiktok.com (USA).

2026-07 switch: previously we scraped the Creative Center's weekly
trending-hashtag list. Jenna's ask ("show trending content, not
hashtags") means we now surface actual videos: title, creator, thumbnail,
view count, and a link.

Sources tried in order:

  1. TikTok's public trending-video RSS via `RSS.app` mirrors and the
     `https://www.tiktok.com/api/trending/item_list/` unofficial JSON
     (unsigned - returns 0 rows now, kept as a probe).
  2. Playwright on `https://www.tiktok.com/discover` and
     `https://www.tiktok.com/explore` - the /discover page renders a
     server-side list of "Trending videos" that is public (no login
     needed) but datacenter-IP-gated. Runs from residential IPs only,
     which is why this scraper lives in `local_residential_run.py`.
  3. Fallback: an empty payload with a diagnostic error. The frontend
     shows "coming soon" rather than stale hashtags in that state.

Snapshot shape (matches `_base.py` social contract):

    {
      "source":   "tiktok",
      "label":    "TikTok",
      "kind":     "social",
      "national": [
          {
              "rank":     1,
              "title":    "kid can't stop laughing at cat",
              "creator":  "@catsofinstagram",
              "url":      "https://www.tiktok.com/@catsofinstagram/video/7395...",
              "image":    "https://p16-sign-va.tiktokcdn.com/tos/...",
              "views":    12500000,
              "views_display": "12.5M",
          }, ...
      ],
      ...
    }

Standalone:

    python3 -m scripts.trends_scrapers.tiktok
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ._base import run_scraper

logger = logging.getLogger(__name__)


# TikTok /discover DOM structure (2026-07, logged-in view):
#   The page renders a grid of `data-e2e="explore-feed-item"` cards.
#   Each card groups 3 videos under a trending hashtag/challenge:
#     <div data-e2e="explore-feed-item">
#       <h4 data-e2e="explore-feed-title">#hashtag</h4>
#       <div data-e2e="explore-feed-video-list">
#         <div data-e2e="explore-feed-video">
#           <a title="video caption ..." href="/@user/video/12345">
#             <img src="https://...tiktokcdn.../...jpeg" alt="full caption">
#           </a>
#         </div>
#         ... (3 videos per hashtag block)
#       </div>
#     </div>
#
# We flatten ALL videos across every hashtag block into a single ranked
# list, since the user asked for "content, not hashtags." The hashtag
# name is preserved as `topic_hashtag` for display context.

_TT_VIDEO_BLOCK_RE = re.compile(
    # Anchor around each video: href gives us /@user/video/id,
    # title attr gives us the truncated caption, alt attr on the inner
    # img gives us the full caption.
    r'<a[^>]*title="([^"]*)"[^>]*href="(/(@[^/"]+)/video/(\d{15,25}))"'
    r'.*?<img[^>]*src="(https://[^"]+tiktokcdn[^"]+)"[^>]*alt="([^"]*)"',
    re.DOTALL | re.IGNORECASE,
)

# Fallback: bare video anchors when the title+img combo above misses.
_TT_VIDEO_HREF_RE = re.compile(
    r'href="(/(@[^/"]+)/video/(\d{15,25}))"',
    re.IGNORECASE,
)


def _parse_display_count(s: str) -> int:
    """'12.5M' -> 12_500_000. '3.2K' -> 3_200. '947' -> 947."""
    if not s:
        return 0
    s = s.strip().replace(',', '')
    m = re.match(r'^([\d.]+)\s*([KMB])?$', s, re.IGNORECASE)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except (TypeError, ValueError):
        return 0
    suffix = (m.group(2) or '').upper()
    if suffix == 'K':
        return int(n * 1_000)
    if suffix == 'M':
        return int(n * 1_000_000)
    if suffix == 'B':
        return int(n * 1_000_000_000)
    return int(n)


def _display_count(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _parse_discover_html(html: str, limit: int = 30) -> list[dict]:
    """Extract trending videos from the /discover DOM.

    Prefers the full (title, href, img, alt) match which yields caption
    + thumbnail + URL + creator in one shot. Falls back to bare href
    anchors for any residual videos we missed.
    """
    if not html:
        return []
    seen_ids: set[str] = set()
    items: list[dict] = []

    for m in _TT_VIDEO_BLOCK_RE.finditer(html):
        # Anchor title is a truncated caption ("caption text ..."); the
        # inner <img alt> has the full un-truncated caption. Prefer the
        # alt text for display when it looks reasonable.
        title_attr = _html.unescape(m.group(1) or '').strip()
        path       = m.group(2)
        handle     = m.group(3)
        vid_id     = m.group(4)
        img_url    = m.group(5)
        alt_text   = _html.unescape(m.group(6) or '').strip()
        if vid_id in seen_ids:
            continue
        seen_ids.add(vid_id)
        # The alt attr is the full caption. If it's suspiciously long
        # or empty, fall back to the truncated title attr.
        caption = alt_text if len(alt_text) > 10 else title_attr
        if len(caption) > 260:
            caption = caption[:257] + '...'
        items.append({
            'rank':    len(items) + 1,
            'title':   caption,
            'creator': handle,
            'url':     f'https://www.tiktok.com{path}',
            'image':   img_url,
        })
        if len(items) >= limit:
            break

    # Fallback: any videos on the page we didn't match with the full
    # regex (e.g. cards where the img failed to render before snapshot).
    if len(items) < limit:
        for m in _TT_VIDEO_HREF_RE.finditer(html):
            path, handle, vid_id = m.group(1), m.group(2), m.group(3)
            if vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            items.append({
                'rank':    len(items) + 1,
                'title':   '',
                'creator': handle,
                'url':     f'https://www.tiktok.com{path}',
                'image':   '',
            })
            if len(items) >= limit:
                break

    return items


def _fetch_playwright() -> list[dict]:
    """Load tiktok.com/discover with Playwright + donated cookies.
    Returns [] if Playwright isn't installed or the page never renders
    any video anchors."""
    try:
        from ._playwright import (_lazy_playwright, _launch_browser,
                                  _try_stealth, UA)
        from ._base import (load_donated_cookies_playwright,
                            cookie_donation_status)
    except Exception as e:
        logger.info("tiktok: playwright helpers unavailable: %s", e)
        return []
    sp = _lazy_playwright()
    if sp is None:
        logger.info("tiktok: playwright not installed")
        return []

    domain = 'tiktok.com'
    status = cookie_donation_status(domain)
    if not (status and status.get('donated')):
        logger.info("tiktok: no donated %s cookies "
                    "(anonymous /discover still works, but yields less)",
                    domain)
    donated = load_donated_cookies_playwright(domain) or []

    html_out = ''
    with sp() as pw:
        try:
            browser, _ch = _launch_browser(pw, prefer_chrome=True, proxy=None)
        except Exception as e:
            logger.warning("tiktok playwright launch failed: %s", e)
            return []
        try:
            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1440, 'height': 900},
                locale='en-US',
                timezone_id='America/New_York',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            if donated:
                try:
                    ctx.add_cookies(donated)
                    logger.info("tiktok: injected %d tiktok.com cookies", len(donated))
                except Exception as e:
                    logger.info("tiktok: cookie inject failed: %s", e)
            page = ctx.new_page()
            _try_stealth(page)
            try:
                page.goto('https://www.tiktok.com/',
                          wait_until='domcontentloaded', timeout=30_000)
                page.wait_for_timeout(2000)
                page.goto('https://www.tiktok.com/discover',
                          wait_until='domcontentloaded', timeout=45_000)
                # Wait for at least one video-card anchor before snapshotting.
                try:
                    page.wait_for_selector('a[href*="/video/"]',
                                            timeout=15_000)
                except Exception:
                    pass
                # Nudge lazy-loaded rows below the fold.
                for _ in range(4):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(1000)
                html_out = page.content() or ''
            except Exception as e:
                logger.info("tiktok /discover navigation error: %s", e)
        finally:
            try: browser.close()
            except Exception: pass

    items = _parse_discover_html(html_out, limit=40)
    if not items:
        logger.info("tiktok: /discover yielded 0 items (html len=%d)",
                    len(html_out))
    else:
        logger.info("tiktok: /discover yielded %d items", len(items))
    return items


# ---------------------------------------------------------------------------
# Per-video engagement enrichment
# ---------------------------------------------------------------------------
# TikTok's /discover DOM does NOT expose view / like / comment counts
# (only the caption + thumbnail + creator + video-id). To surface
# engagement stats on the dashboard alongside the video row, we hit
# each video's public page and parse the SSR JSON blob that TikTok
# embeds under `<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`.
#
# That blob (renamed from SIGI_STATE mid-2024) carries the full
# ItemModule tree: playCount (views), diggCount (likes), commentCount,
# shareCount. Requests use curl_cffi's Chrome-124 TLS impersonation
# so TikTok's WAF treats them as ordinary browser traffic. We do NOT
# need Playwright for this - the individual video pages are much
# less aggressively gated than /discover, and every request would
# have cost 2s in Playwright vs ~1s via curl_cffi.
_UNIVERSAL_DATA_RE = re.compile(
    r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>({.+?})</script>',
    re.DOTALL,
)


def _walk_for_stats(obj: Any, depth: int = 0) -> Optional[dict]:
    """Depth-first search for the first dict carrying `playCount`.
    Returns the enclosing stats dict or None if nothing found within
    the recursion cap (guards against pathological blobs)."""
    if depth > 10:
        return None
    if isinstance(obj, dict):
        if 'playCount' in obj:
            return obj
        for v in obj.values():
            found = _walk_for_stats(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_for_stats(v, depth + 1)
            if found is not None:
                return found
    return None


def _fetch_video_stats(video_url: str, timeout: int = 15) -> dict:
    """Return {views, likes, comments, shares} for one TikTok video,
    or {} if the fetch or parse fails. Never raises."""
    try:
        try:
            from curl_cffi import requests as crq
            r = crq.get(video_url, impersonate='chrome124',
                         headers={
                             'Accept':          'text/html,application/xhtml+xml',
                             'Accept-Language': 'en-US,en;q=0.9',
                         },
                         timeout=timeout)
        except ImportError:
            import requests as crq
            r = crq.get(video_url, headers={
                'User-Agent':      ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; '
                                     'rv:120.0) Gecko/20100101 Firefox/120.0'),
                'Accept':          'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }, timeout=timeout)
    except Exception as e:
        logger.debug("tiktok enrich %s: %s", video_url, e)
        return {}
    if not getattr(r, 'ok', False):
        logger.debug("tiktok enrich %s: http %s",
                      video_url, getattr(r, 'status_code', '?'))
        return {}
    m = _UNIVERSAL_DATA_RE.search(r.text or '')
    if not m:
        return {}
    try:
        blob = json.loads(m.group(1))
    except Exception:
        return {}
    stats = _walk_for_stats(blob)
    if not stats:
        return {}
    def _as_int(v: Any) -> int:
        try: return int(v or 0)
        except (TypeError, ValueError): return 0
    return {
        'views':    _as_int(stats.get('playCount')),
        'likes':    _as_int(stats.get('diggCount')),
        'comments': _as_int(stats.get('commentCount')),
        'shares':   _as_int(stats.get('shareCount')),
    }


def _enrich_items_with_stats(items: list[dict], *, max_workers: int = 8) -> None:
    """Mutate `items` in place, adding views/likes/comments/shares +
    a human-readable `views_display` (e.g. '12.5M') to each entry
    that has a valid URL. Runs enrichment fan-out via a thread pool
    so 30 videos settle in ~4s instead of 30s serial."""
    urls = [(i, it.get('url') or '') for i, it in enumerate(items)]
    urls = [(i, u) for (i, u) in urls if u]
    if not urls:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_video_stats, u): i for (i, u) in urls}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                stats = fut.result()
            except Exception:
                stats = {}
            if not stats:
                continue
            it = items[i]
            if stats.get('views'):
                it['views']         = stats['views']
                it['views_display'] = _display_count(stats['views'])
            if stats.get('likes'):
                it['likes']    = stats['likes']
            if stats.get('comments'):
                it['comments'] = stats['comments']
            if stats.get('shares'):
                it['shares']   = stats['shares']


def fetch() -> dict[str, Any]:
    items = _fetch_playwright()
    if not items:
        return {
            'national': [],
            'error':    ('tiktok /discover returned no video cards. '
                         'Check that tiktok.com cookies are donated '
                         '(python3 scripts/trends_scrapers/donate_cookies.py '
                         'tiktok.com) and that this ran from a residential '
                         'IP (Hetzner is fingerprinted).'),
        }
    # Enrich the top-30 with per-video engagement stats. Failures on
    # individual videos are silently swallowed - the row still renders,
    # just without the extra chips.
    _enrich_items_with_stats(items[:30])
    return {'national': items[:30]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('tiktok', 'TikTok', 'social', fetch)
    print(f"tiktok: national={len(result.get('national', []))} "
          f"error={result.get('error')}", file=sys.stderr)
