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
_IG_URL_RE = re.compile(
    r'^/(reel|p)/([A-Za-z0-9_\-]{8,20})/?$',
    re.IGNORECASE,
)


# JavaScript that runs INSIDE the Playwright page context. Walks every
# `/reel/*` and `/p/*` anchor in DOM order and pulls out:
#   - href (post URL)
#   - the associated thumbnail: prefers <img src> from a descendant,
#     falls back to <video poster>. This is the actual CDN URL IG's
#     own frontend uses, so it renders correctly (as long as we don't
#     try to hotlink from another domain).
#   - the alt attribute of the thumbnail img, which IG populates with
#     an auto-generated caption ("Photo by @user on Jul 21, 2026. May
#     be an image of ...") - useful text even when no caption is
#     explicitly extractable.
#   - the creator handle parsed out of the alt text ("Photo by @X on...")
#
# CRITICAL - Reels have empty <video poster> until scrolled into view
# and the browser has a chance to render at least the first frame. So
# this function does a two-step per-anchor pass:
#   1. scrollIntoView({block: 'center'})  (forces IG's lazy loader)
#   2. sleep a tick (~120ms) for poster / img.src to latch
#   3. read image + alt
# This runs async and returns a promise Playwright can await.
_IG_EXTRACT_JS = r"""
async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const seen = new Set();
    const rows = [];
    // Anchors linking to a post or reel are the top-level tile
    // containers in /explore. querySelectorAll returns them in
    // document order, which matches the visual grid IG shows.
    const anchors = document.querySelectorAll(
        'a[href^="/reel/"], a[href^="/p/"]'
    );
    for (const a of anchors) {
        const href = a.getAttribute('href') || '';
        const m = href.match(/^\/(reel|p)\/([A-Za-z0-9_\-]{8,20})\/?/);
        if (!m) continue;
        const kind = m[1];
        const code = m[2];
        if (seen.has(code)) continue;
        seen.add(code);

        // Force the tile into the viewport. IG's IntersectionObserver
        // watches for tile visibility and only THEN starts loading the
        // <img>.src or lets the <video> render its first frame into
        // the poster attribute. `block: 'center'` puts it near the
        // middle of the viewport, which triggers the loader most
        // reliably.
        try { a.scrollIntoView({block: 'center', behavior: 'instant'}); }
        catch (_) { a.scrollIntoView(); }
        // Fire pointer + mouse enter events. IG's explore feed uses
        // these to trigger video poster generation and preview
        // playback - critical for reels which otherwise never
        // populate their poster attribute.
        try {
            for (const evName of ['pointerenter','mouseenter','mouseover']) {
                a.dispatchEvent(new MouseEvent(evName, {bubbles: true}));
            }
        } catch (_) {}
        // Small wait for the browser to actually paint and for IG to
        // populate img.src / video.poster. 250ms handles most tiles;
        // the retry loop below handles laggier ones.
        await sleep(250);

        let image = '';
        let alt   = '';

        // Prefer <img>: post thumbnails, carousel first frame, reel
        // cover frame if IG rendered one.
        const img = a.querySelector('img');
        if (img) {
            image = img.getAttribute('src') || img.currentSrc || '';
            alt   = img.getAttribute('alt') || '';
        }
        // Fall back to <video poster>. Retry a few times because
        // some reels only latch the poster after ~500ms.
        if (!image) {
            const vid = a.querySelector('video');
            if (vid) {
                for (let attempt = 0; attempt < 4; attempt++) {
                    image = vid.getAttribute('poster') || '';
                    if (image) break;
                    await sleep(200);
                }
            }
        }

        // Second-chance: re-read the <img> after the wait in case IG's
        // lazy loader was mid-flight.
        if (!image) {
            const img2 = a.querySelector('img');
            if (img2) {
                image = img2.getAttribute('src') || img2.currentSrc || '';
                if (!alt) alt = img2.getAttribute('alt') || '';
            }
        }

        // IG stuffs "Photo by @handle on ...", "Photo shared by @handle
        // on ...", or "Photo. By Mindset on July 13, 2026 tagging
        // @powerbyminds" into alt text for every image in the explore
        // feed. Peel off the handle if present. IG display names can
        // contain the full range of latin + some symbols (®, ™) but
        // account handles proper (@x) are always [A-Za-z0-9._]+.
        // We match both display-name preambles ("By Mindset on ...")
        // AND @-prefixed handles ("by @user on ...").
        let creator = '';
        if (alt) {
            // Prefer an explicit @handle if one appears.
            let cm = alt.match(/@([A-Za-z0-9._]{1,30})/);
            if (cm) {
                creator = '@' + cm[1];
            } else {
                // Otherwise take a display name after "by <name>".
                cm = alt.match(/(?:Photo|Video|Reel|Image)[.,]?\s+(?:shared\s+)?[Bb]y\s+([^\n.,]+?)(?:\s+on\s+|\s+tagging|[.,]|$)/i);
                if (cm) creator = cm[1].trim();
            }
        }
        rows.push({
            href:    href,
            kind:    kind,
            code:    code,
            image:   image,
            alt:     alt,
            creator: creator,
        });
        if (rows.length >= 40) break;
    }
    return rows;
}
"""


def _clean_alt_caption(alt: str) -> str:
    """IG's auto-generated alt text looks like:

        "Photo by @user on Jul 21, 2026. May be an image of 3 people,
         beach, and text that says: 'summer vibes'."

    or:

        "Photo shared by @user on Jul 21, 2026 tagging @friend.
         May be an image of ..."

    Peel off the "Photo by @user on <date>" preamble and the "May be
    an image of" ML-caption suffix so what's left is the user's
    intent (tagged handles, hashtags, text-that-says content). If
    nothing usable remains, return "".
    """
    if not alt:
        return ''
    s = alt.strip()
    # Drop leading "Photo|Video|Reel by/shared by @user on <date>."
    s = re.sub(
        r'^(Photo|Video|Reel|Image)\s+(shared\s+)?by\s+@?[A-Za-z0-9._]+'
        r'(\s+on\s+[^.]+)?\.?\s*',
        '', s, flags=re.IGNORECASE
    )
    # Drop the ML caption suffix. Keep any "text that says: '...'" fragment.
    s = re.sub(
        r'\bMay be an (image|photo|graphic)\s+of\s+',
        '', s, flags=re.IGNORECASE
    )
    # Trim, collapse whitespace.
    s = re.sub(r'\s+', ' ', s).strip(' .,')
    return s


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
            raw_rows: list[dict] = []
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
                # First, do a rough scroll pass so IG's virtualiser
                # instantiates enough tile DOM nodes for us to walk.
                # Explore only renders anchor DOMs for the first ~20
                # tiles on initial load; scrolling injects more.
                for _ in range(4):
                    page.mouse.wheel(0, 2200)
                    page.wait_for_timeout(700)
                # Scroll back to top so the extractor starts at rank 1.
                page.evaluate('window.scrollTo({top:0, behavior:"instant"})')
                page.wait_for_timeout(700)

                # Now let the JS extractor drive the rest: it walks each
                # anchor, scrollIntoView({block:'center'}) forces IG's
                # lazy loader to populate img.src / video.poster, then
                # it collects the URL + alt. Because it runs in the
                # page context and awaits sleeps, we don't have to
                # coordinate scroll + wait + read from Python.
                try:
                    # The extractor sleeps ~120ms per tile x up to 40
                    # tiles = ~5s minimum, so bump the page default
                    # timeout before firing it. Playwright's
                    # page.evaluate uses the page default timeout;
                    # there's no per-call timeout kwarg.
                    page.set_default_timeout(60_000)
                    raw_rows = page.evaluate(_IG_EXTRACT_JS) or []
                except Exception as e:
                    logger.info("instagram: page.evaluate failed: %s", e)
                    raw_rows = []

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

    # IG's /explore is dominated by Reels rendered as <video> elements,
    # and the browser only populates <video>.poster once the tile is
    # actually visible AND briefly hovered / previewed. Even with
    # scrollIntoView + mouse events, ~60-70% of tiles come back without
    # a thumbnail URL. Rather than surface tappable blank-thumbnail
    # rows (worse UX than showing fewer richer rows), we filter down
    # to tiles that came back with a scontent-* image AND normalize
    # the caption + creator.
    scored: list[dict] = []
    for row in raw_rows:
        code = row.get('code') or ''
        if not code:
            continue
        image = row.get('image') or ''
        # Skip data-URI placeholders (IG's lazy-load 1x1 png). Real
        # thumbs are https://scontent-*.cdninstagram.com/...
        if image.startswith('data:') or 'cdninstagram' not in image:
            image = ''
        # Photos/Reels with a real thumbnail are the only rows worth
        # surfacing. A row with no image AND no caption AND no creator
        # is just a link with a lime placeholder box - visual noise.
        title = _clean_alt_caption(row.get('alt') or '')
        creator = row.get('creator') or ''
        if not (image or title or creator):
            continue
        kind = row.get('kind') or 'p'
        scored.append({
            'rank':    0,   # re-stamp after ranking
            'title':   title,
            'creator': creator,
            'url':     f'https://www.instagram.com/{("reel" if kind == "reel" else "p")}/{code}/',
            'image':   image,
            'kind':    kind,
            '_score':  (1 if image else 0) + (1 if title else 0) + (1 if creator else 0),
        })

    # Sort by richness (rows with image + caption + creator first), then
    # by DOM order among ties. Keep the top 15 - explore feed rank is
    # noisy anyway (IG's algo shuffles between sessions).
    scored.sort(key=lambda r: (-r['_score'], raw_rows.index(next(
        rr for rr in raw_rows if rr.get('code') and r['url'].endswith('/' + rr['code'] + '/')
    ))))
    items: list[dict] = []
    for r in scored[:15]:
        r.pop('_score', None)
        r['rank'] = len(items) + 1
        items.append(r)

    if not items:
        logger.info("instagram: /explore yielded 0 usable items")
    else:
        with_img = sum(1 for it in items if it.get('image'))
        with_txt = sum(1 for it in items if it.get('title'))
        with_cre = sum(1 for it in items if it.get('creator'))
        logger.info("instagram: /explore yielded %d usable items (img=%d, title=%d, creator=%d) from %d raw",
                    len(items), with_img, with_txt, with_cre, len(raw_rows))
    return items


def fetch() -> dict[str, Any]:
    items = _fetch_playwright()
    if not items:
        return {
            'national': [],
            'error':    ('instagram /explore returned no posts with '
                         'thumbnails. Check that instagram.com cookies '
                         'are donated and unexpired (python3 '
                         'scripts/trends_scrapers/donate_cookies.py '
                         'instagram.com) and that this ran from a '
                         'residential IP.'),
        }
    return {'national': items}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('instagram', 'Instagram', 'social', fetch)
    print(f"instagram: national={len(result.get('national', []))} "
          f"error={result.get('error')}", file=sys.stderr)
