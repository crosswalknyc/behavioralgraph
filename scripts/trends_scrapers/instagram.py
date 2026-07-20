"""
Instagram trending scraper - real trending posts / reels from
instagram.com/explore (USA).

2026-07 switch: previously we scraped top-hashtags.com's all-time
hashtag popularity list (#love, #instagood, ...) which was neither
"trending" nor "content". Jenna's ask ("show trending content, not
hashtags") means we now surface actual posts and reels from the /explore
feed - what IG's own recommendation algorithm decides is worth showing
someone right now.

Auth is required. The /explore page redirects anonymous visitors to the
login wall. We inject donated instagram.com cookies (via
`donate_cookies.py`) so the Playwright session is logged in as Jenna's
regular IG account. Runs from residential IPs only because IG WAFs
datacenter egress hard.

Snapshot shape (matches `_base.py` social contract):

    {
      "source":   "instagram",
      "label":    "Instagram",
      "kind":     "social",
      "national": [
          {
              "rank":    1,
              "title":   "caption or alt text",
              "creator": "@handle",
              "url":     "https://www.instagram.com/reel/{shortcode}/",
              "image":   "https://scontent-.../{jpg}",
              "kind":    "reel" | "post",
          }, ...
      ],
      ...
    }

Standalone:

    python3 -m scripts.trends_scrapers.instagram
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from ._base import run_scraper

logger = logging.getLogger(__name__)


# IG post / reel URL patterns on /explore:
#   /reel/{shortcode}/    - Reels (short vertical video)
#   /p/{shortcode}/       - Photo / carousel / IGTV post
# Shortcodes are alphanumeric + '-_', typically 10-12 chars.
_IG_HREF_RE = re.compile(
    r'href="(/(reel|p)/([A-Za-z0-9_\-]{8,20})/?)"',
    re.IGNORECASE,
)


def _parse_explore_html(html: str, limit: int = 30) -> list[dict]:
    """Extract trending posts/reels from an /explore page render.

    IG's 2026 explore DOM is aggressively minimal: post/reel anchors
    render with almost no metadata attached (no alt text, no captions,
    no aria-labels), and most items are <video> elements rather than
    <img>. Rather than guess at brittle DOM structure, we:

      1. Extract every unique /p/{shortcode}/ or /reel/{shortcode}/
         URL in DOM order (which matches visual rank).
      2. Use IG's public `/media/?size=l` redirect endpoint for the
         thumbnail (works whether the underlying post is an image,
         carousel, or reel first-frame; IG serves an appropriately
         sized thumbnail for all three).

    Result: reliable clickable rows with thumbnails, ranked by IG's
    own Explore recommendation engine.
    """
    if not html:
        return []
    seen: set[str] = set()
    items: list[dict] = []
    for m in _IG_HREF_RE.finditer(html):
        path, kind, code = m.group(1), m.group(2), m.group(3)
        if code in seen:
            continue
        seen.add(code)
        # /p/{code}/media/?size=l returns a 302 redirect to the actual
        # CDN image for the post (image posts, carousel first frame,
        # or reel first frame). Works without auth for public posts.
        # Followed transparently by <img> tags in the browser.
        thumb = f'https://www.instagram.com/p/{code}/media/?size=l'
        items.append({
            'rank':    len(items) + 1,
            'title':   '',      # captions require a separate GraphQL fetch
            'creator': '',      # not exposed in explore DOM
            'url':     f'https://www.instagram.com{path}',
            'image':   thumb,
            'kind':    kind,    # 'reel' or 'p'
        })
        if len(items) >= limit:
            break
    return items


def _fetch_playwright() -> list[dict]:
    """Load instagram.com/explore with Playwright + donated cookies."""
    try:
        from ._playwright import (_lazy_playwright, _launch_browser,
                                  _try_stealth, UA)
        from ._base import (load_donated_cookies_playwright,
                            cookie_donation_status)
    except Exception as e:
        logger.info("instagram: playwright helpers unavailable: %s", e)
        return []
    sp = _lazy_playwright()
    if sp is None:
        logger.info("instagram: playwright not installed")
        return []

    domain = 'instagram.com'
    status = cookie_donation_status(domain)
    if not (status and status.get('donated')):
        logger.info("instagram: no donated %s cookies - /explore requires "
                    "login, so we'll get redirected to the wall. Run "
                    "donate_cookies.py instagram.com from the operator's "
                    "laptop.", domain)
        return []
    donated = load_donated_cookies_playwright(domain) or []
    if not donated:
        return []

    html_out = ''
    with sp() as pw:
        try:
            browser, _ch = _launch_browser(pw, prefer_chrome=True, proxy=None)
        except Exception as e:
            logger.warning("instagram playwright launch failed: %s", e)
            return []
        try:
            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1440, 'height': 900},
                locale='en-US',
                timezone_id='America/New_York',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            try:
                ctx.add_cookies(donated)
                logger.info("instagram: injected %d instagram.com cookies "
                            "(age=%.1fh)",
                            len(donated), status.get('age_hours') or -1)
            except Exception as e:
                logger.info("instagram: cookie inject failed: %s", e)

            page = ctx.new_page()
            _try_stealth(page)
            try:
                page.goto('https://www.instagram.com/',
                          wait_until='domcontentloaded', timeout=30_000)
                page.wait_for_timeout(2500)
                page.goto('https://www.instagram.com/explore/',
                          wait_until='domcontentloaded', timeout=45_000)
                # Wait for at least one post-card anchor.
                try:
                    page.wait_for_selector('a[href^="/reel/"], a[href^="/p/"]',
                                            timeout=15_000)
                except Exception:
                    logger.info("instagram: no /reel or /p anchors within 15s; "
                                "may be a login redirect")
                for _ in range(4):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(1000)
                html_out = page.content() or ''
                # Diagnostic: if we ended up at /accounts/login we know
                # the cookies were invalidated.
                cur = page.url or ''
                if '/accounts/login' in cur or 'login' in cur:
                    logger.warning("instagram: navigation ended at %s - "
                                    "donated cookies likely expired", cur)
            except Exception as e:
                logger.info("instagram /explore navigation error: %s", e)
        finally:
            try: browser.close()
            except Exception: pass

    items = _parse_explore_html(html_out, limit=30)
    if not items:
        logger.info("instagram: /explore yielded 0 items (html len=%d)",
                    len(html_out))
    else:
        logger.info("instagram: /explore yielded %d items", len(items))
    return items


def fetch() -> dict[str, Any]:
    items = _fetch_playwright()
    if not items:
        return {
            'national': [],
            'error':    ('instagram /explore returned no posts. Check that '
                         'instagram.com cookies are donated and unexpired '
                         '(python3 scripts/trends_scrapers/donate_cookies.py '
                         'instagram.com) and that this ran from a '
                         'residential IP.'),
        }
    return {'national': items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('instagram', 'Instagram', 'social', fetch)
    print(f"instagram: national={len(result.get('national', []))} "
          f"error={result.get('error')}", file=sys.stderr)
